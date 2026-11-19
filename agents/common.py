"""Shared core for the content agents: Claude calls with a spending cap and token log, JSON extraction,
duplicate detection and atomic file writes. Python 3.9+, no dependencies except the `anthropic` SDK.
"""
import difflib
import json
import os
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state"
CONTENT = ROOT / "content"
CONFIG = ROOT / "config"

# USD per million tokens (input, output). Haiku/Sonnet/Opus 5.5 list prices as of 2026-11-03.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-opus-5-5": (4.0, 20.0),
}
HAIKU, SONNET = "claude-haiku-5-5", "claude-sonnet-5-5"


class BudgetExceeded(RuntimeError):
    pass


class LlmRefused(RuntimeError):
    pass


class LlmTruncated(RuntimeError):
    pass


def cost_usd(model, tokens_in, tokens_out):
    pin, pout = PRICES.get(model, PRICES[SONNET])
    return (tokens_in * pin + tokens_out * pout) / 1e6


class Llm:
    """One place that talks to Claude. Tracks spend, enforces a cap, appends every call to state/usage.jsonl."""

    def __init__(self, client=None, budget_usd=None, log_path=None):
        self._client = client
        self.budget = float(os.environ.get("MAX_USD_PER_RUN", "0.50")) if budget_usd is None else budget_usd
        self.spent = 0.0
        self.log_path = Path(log_path) if log_path else STATE / "usage.jsonl"

    @property
    def client(self):
        if self._client is None:
            import anthropic  # imported lazily so tests and feed code need no SDK

            self._client = anthropic.Anthropic()
        return self._client

    def call(self, agent, model, system, user, max_tokens=8000, effort="low", tools=None):
        if self.spent >= self.budget:
            raise BudgetExceeded(f"spent ${self.spent:.4f} of ${self.budget:.2f} before {agent}")
        messages = [{"role": "user", "content": user}]
        text, t_in, t_out = "", 0, 0
        for _ in range(4):  # server tools such as web search can pause a turn; continue up to 3 times
            kw = dict(model=model, max_tokens=max_tokens, system=system, messages=messages,
                      output_config={"effort": effort})
            if tools:
                kw["tools"] = tools
            resp = self.client.messages.create(**kw)
            t_in += resp.usage.input_tokens
            t_out += resp.usage.output_tokens
            text += "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
            if resp.stop_reason == "refusal":
                self._log(agent, model, t_in, t_out)
                raise LlmRefused(f"{agent}: the model declined this request")
            if resp.stop_reason == "max_tokens":
                self._log(agent, model, t_in, t_out)
                raise LlmTruncated(f"{agent}: output cut off at {max_tokens} tokens")
            if resp.stop_reason != "pause_turn":
                break
            messages = messages + [{"role": "assistant", "content": resp.content}]
        self._log(agent, model, t_in, t_out)
        return text

    def _log(self, agent, model, t_in, t_out):
        c = cost_usd(model, t_in, t_out)
        self.spent += c
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": int(time.time()), "agent": agent, "model": model,
                                "in": t_in, "out": t_out, "usd": round(c, 6)}) + "\n")


def extract_json(text):
    """Pull the first JSON object or array out of a model reply, tolerating code fences and chatter."""
    t = re.sub(r"```(?:json)?", "", text).strip()
    dec = json.JSONDecoder()
    for i, ch in enumerate(t):
        if ch in "[{":
            try:
                obj, _ = dec.raw_decode(t[i:])
                return obj
            except ValueError:
                continue
    raise ValueError("no JSON found in model reply")


def norm(s):
    return re.sub(r"[^a-z0-9 ]+", "", (s or "").lower()).strip()


def similar(a, b):
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


def is_duplicate(title, history, threshold=0.62):
    return any(similar(title, h) >= threshold for h in history)


def load_json(path, default):
    p = Path(path)
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding="utf-8"))


def save_json(path, obj):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)  # never leaves a half-written file


def history_titles():
    """Everything already posted or drafted, so new topics are not repeats."""
    titles = []
    for f in sorted(CONTENT.glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            titles.append(d.get("topic") or (d.get("slides") or [{}])[0].get("title", ""))
        except ValueError:
            continue
    for t in load_json(STATE / "topics.json", []):
        if t.get("status") in ("selected", "written", "ready", "used"):
            titles.append(t.get("title", ""))
    return [t for t in titles if t]


def next_post_id():
    nums = [int(p.stem) for p in CONTENT.glob("*.json") if p.stem.isdigit()]
    return f"{(max(nums) if nums else 0) + 1:03d}"
