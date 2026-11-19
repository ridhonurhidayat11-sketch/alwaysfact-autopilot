import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "agents"))
sys.path.insert(0, str(HERE.parent / "publisher"))
sys.path.insert(0, str(HERE))

import common  # noqa: E402
import control  # noqa: E402
import evidence  # noqa: E402
import ingest  # noqa: E402
import routine_brief  # noqa: E402
from test_agents import good_post  # noqa: E402
import publish as P  # noqa: E402

NOW = datetime(2026, 11, 20, 1, 0, tzinfo=timezone.utc)
Q1 = "Venus takes about 243 Earth days to rotate once on its axis"
Q2 = "A year on Venus lasts about 225 Earth days"
PAGES = {"https://science.nasa.gov/venus": f"<html><body><p>{Q1}.</p><p>{Q2}, less than a day.</p></body></html>",
         "https://blog.example.org/a": f"<p>{Q1}</p>", "https://news.other.net/b": f"<p>{Q1}</p>"}


def fetcher(url):
    if url not in PAGES:
        raise OSError("404")
    return evidence.html.unescape(evidence.re.sub(r"<[^>]+>", " ", PAGES[url]))


def proposal(**over):
    d = {"topic_id": "t1", "source_url": "https://science.nasa.gov/venus", "risk": "low", "post": good_post(),
         "verdicts": [{"claim": 1, "verdict": "supported", "evidence_url": "https://science.nasa.gov/venus", "quote": Q1, "note": "ok"},
                      {"claim": 2, "verdict": "supported", "evidence_url": "https://science.nasa.gov/venus", "quote": Q2, "note": "ok"},
                      {"claim": 0, "verdict": "supported", "note": "ok"}]}
    d.update(over)
    return d


def ctl(**kw):
    c = copy.deepcopy(control.DEFAULT)
    c.update({"mode": "shadow", **kw})
    return c


class EvidenceTests(unittest.TestCase):
    def test_quote_must_really_be_on_the_page(self):
        page = "Venus takes about 243 Earth days\n to rotate once on its axis, NASA says."
        self.assertTrue(evidence.quote_on_page("Venus takes about 243 Earth days to rotate once", page))
        self.assertFalse(evidence.quote_on_page("Venus takes about 300 Earth days to rotate once", page))

    def test_tiny_quotes_prove_nothing(self):
        self.assertFalse(evidence.quote_on_page("Venus", "Venus is a planet"))

    def test_typography_does_not_matter(self):
        self.assertTrue(evidence.quote_on_page("It’s a planet—a hot one indeed", "it's a planet-a hot one indeed here"))

    def test_numbers_compare_across_separators(self):
        self.assertEqual(evidence.numbers("1,500 and 1.500 and 243"), {"1500", "243"})

    def test_unreadable_page_blanks_the_url(self):
        v = proposal()["verdicts"]
        v[0]["evidence_url"] = "https://science.nasa.gov/missing"
        out, notes = evidence.verify_verdicts(good_post(), v, fetcher)
        self.assertEqual(out[0]["evidence_url"], "")
        self.assertTrue(any("could not be read" in n for n in notes))

    def test_invented_quote_blanks_the_url(self):
        v = proposal()["verdicts"]
        v[0]["quote"] = "Venus rotates once every single week without fail"
        out, notes = evidence.verify_verdicts(good_post(), v, fetcher)
        self.assertEqual(out[0]["evidence_url"], "")
        self.assertTrue(any("not on the page" in n for n in notes))

    def test_a_number_in_the_claim_that_the_quote_lacks_downgrades_the_claim(self):
        post = good_post()
        post["claims"][0]["text"] = "Satu rotasi Venus 250 hari Bumi"
        out, _ = evidence.verify_verdicts(post, proposal()["verdicts"], fetcher)
        self.assertEqual(out[0]["verdict"], "unsupported")


class JudgeTests(unittest.TestCase):
    TIER1 = ["nasa.gov"]

    def judge(self, data, history=()):
        return ingest.judge(data, self.TIER1, fetcher, list(history))

    def test_verified_tier1_proposal_is_accepted(self):
        self.assertTrue(self.judge(proposal())[0])

    def test_non_low_risk_is_never_accepted(self):
        ok, why, _ = self.judge(proposal(risk="medium"))
        self.assertFalse(ok)
        self.assertIn("only 'low'", why[0])

    def test_one_ordinary_site_is_not_enough_but_two_are(self):
        d = proposal()
        for v in d["verdicts"][:2]:
            v["evidence_url"] = "https://blog.example.org/a"
            v["quote"] = Q1 if v["claim"] == 1 else Q1
        d["post"]["claims"][1]["text"] = "Satu rotasi Venus 243 hari Bumi lagi"
        self.assertFalse(self.judge(d)[0])
        for v in d["verdicts"][:2]:
            v["second_evidence_url"] = "https://news.other.net/b"
            v["second_quote"] = Q1
        self.assertTrue(self.judge(d)[0])

    def test_repeat_topic_is_rejected(self):
        ok, why, _ = self.judge(proposal(), history=["Venus"])
        self.assertFalse(ok)

    def test_broken_structure_is_rejected_before_any_fetching(self):
        d = proposal()
        d["post"]["slides"] = d["post"]["slides"][:2]
        with mock.patch.object(evidence, "fetch_text", side_effect=AssertionError("must not fetch")):
            self.assertFalse(ingest.judge(d, self.TIER1, evidence.fetch_text, [])[0])

    def test_malformed_proposal(self):
        self.assertFalse(self.judge({"post": "oops"})[0])


class RunTests(unittest.TestCase):
    def sandbox(self):
        d = Path(tempfile.mkdtemp())
        for sub in ("state", "content", "inbox"):
            (d / sub).mkdir()
        qp = d / "queue.csv"
        P.write_queue([], str(qp))
        for m, n in [(ingest, "STATE"), (ingest, "CONTENT"), (common, "STATE"), (common, "CONTENT"), (control, "STATE")]:
            p = mock.patch.object(m, n, d / ("state" if n == "STATE" else "content"))
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(ingest, "load_json", wraps=common.load_json)
        p.start()
        self.addCleanup(p.stop)
        return d, qp

    def put(self, d, name, data):
        (d / "inbox" / name).write_text(json.dumps(data) if not isinstance(data, str) else data, encoding="utf-8")

    def go(self, d, qp, c=None):
        c = c or ctl(posts_per_day=2, buffer_days=1)
        with mock.patch.object(ingest.factcheck, "load_json", return_value={"tier1": ["nasa.gov"]}), \
                mock.patch.object(ingest, "load_json", side_effect=lambda p, default: (
                    {"tier1": ["nasa.gov"]} if str(p).endswith("trusted_sources.json") else common.load_json(p, default))):
            return ingest.run(d / "inbox", c, NOW, fetcher, queue_path=qp)

    def test_accepts_the_good_rejects_the_bad_and_survives_garbage(self):
        d, qp = self.sandbox()
        bad = proposal()
        bad["verdicts"][0]["quote"] = "This sentence was invented by the model and is not on any page"
        self.put(d, "a-good.json", proposal())
        self.put(d, "b-bad.json", bad)
        self.put(d, "c-garbage.json", "{not json")
        out = self.go(d, qp)
        self.assertEqual((out["accepted"], out["queued"], out["rejected"]), (1, 1, 2))
        rows = P.read_queue(str(qp))
        self.assertEqual((len(rows), rows[0]["status"], rows[0]["media_urls"]), (1, "pending_media", ""))
        saved = json.loads((d / "content" / "001.json").read_text())
        self.assertEqual((saved["origin"], saved["status"]), ("routine", "queued"))

    def test_same_proposal_is_never_processed_twice(self):
        d, qp = self.sandbox()
        self.put(d, "a.json", proposal())
        self.go(d, qp)
        out = self.go(d, qp)
        self.assertEqual((out["accepted"], len(P.read_queue(str(qp)))), (0, 1))

    def test_full_queue_makes_proposals_wait_instead_of_burning_them(self):
        d, qp = self.sandbox()
        P.write_queue([dict(id="x", scheduled_at="2026-11-21T05:00:00Z", media_urls="u", caption="c", status="approved",
                            result="", posted_at="")], str(qp))
        self.put(d, "a.json", proposal())
        out = self.go(d, qp, ctl(posts_per_day=1, buffer_days=1))
        self.assertEqual(out["accepted"], 0)
        self.assertEqual(out["report"][0]["result"], "waiting")
        self.assertEqual(common.load_json(d / "state" / "ingested.json", []), [])

    def test_off_and_paused_modes_do_nothing(self):
        d, qp = self.sandbox()
        self.put(d, "a.json", proposal())
        self.assertIn("off", ingest.run(d / "inbox", ctl(mode="off"), NOW, fetcher, queue_path=qp)["skipped"])
        self.assertIn("paused", ingest.run(d / "inbox", ctl(paused=True, pause_reason="x"), NOW, fetcher, queue_path=qp)["skipped"])


class BriefTests(unittest.TestCase):
    def test_off_mode_says_stop(self):
        with mock.patch.object(control, "load", return_value=ctl(mode="off")):
            self.assertFalse(routine_brief.brief(NOW)["go"])

    def test_brief_lists_only_fresh_candidates(self):
        d = Path(tempfile.mkdtemp())
        (d / "state").mkdir()
        common.save_json(d / "state" / "candidates.json", [
            {"id": "t1", "source": "NASA", "title": "Venus berputar mundur", "summary": "s", "url": "https://a"},
            {"id": "t2", "source": "Nature", "title": "Gurita punya tiga jantung", "summary": "s", "url": "https://b"},
            {"id": "t3", "source": "Phys", "title": "Kepiting merah bermigrasi", "summary": "s", "url": "https://c"}])
        common.save_json(d / "state" / "used.json", [{"url": "https://b", "title": "Gurita"}])
        qp = d / "queue.csv"
        P.write_queue([], str(qp))
        with mock.patch.object(control, "load", return_value=ctl(posts_per_day=2, buffer_days=1)), \
                mock.patch.object(routine_brief, "STATE", d / "state"), mock.patch.object(common, "STATE", d / "state"), \
                mock.patch.object(common, "CONTENT", d / "content"):
            b = routine_brief.brief(NOW, qp)
        self.assertTrue(b["go"])
        self.assertEqual(b["need_posts"], 2)
        self.assertEqual({c["id"] for c in b["candidates"]}, {"t1", "t3"})
        self.assertIn("claims", b["schema"])


if __name__ == "__main__":
    unittest.main()
