# alwaysfact-autopilot

Fully automatic Instagram account @alwaysfact_ (mind-blowing science facts). It runs in GitHub's cloud, so no laptop has to be on.
**Nothing runs or posts until `config/control.json` says so** (default `"mode": "off"`).

## How a day works
| Time (WIB) | Workflow | What happens |
|---|---|---|
| 05:00 | `plan.yml` | Tops the queue up to 2 days of posts: picks topics, writes, fact-checks, renders slides, queues. |
| 07:00 | `watchdog.yml` | Health check. Pauses posting by itself if something looks wrong, and opens an issue. |
| 07:00-22:59, every 30 min | `publish.yml` | Posts the next due post (max 5 per 24 h, min 60 min apart). |

Posting slots (WIB): 07:30, 10:30, 13:30, 17:30, 20:30.

## The control file `config/control.json` (edit it from the GitHub mobile site)
- `"mode": "off"`: nothing runs. `"shadow"`: everything runs and the queue fills, but nothing is posted (use it to judge quality). `"live"`: posts for real.
- `"paused": true`: stops posting immediately. The watchdog sets this by itself on 3 failed posts in a row, or when the fact-checker rejects 80%+ of topics.
- `posts_per_day`, `slots_wib`, `max_usd_per_day` (token spending cap), `min_score`, and more.

## Quality gates (no human in the loop, so they are strict)
1. Only low-risk topics are auto-selected. High risk (medical, politics, disasters, named people) is rejected, medium risk is held for a human and never auto-posted.
2. A separate fact-checker with web search must support EVERY claim, with a tier-1 source (`config/trusted_sources.json`) or two independent sites. Max 2 rewrites, then the topic is dropped.
3. Structure checks (slide count, lengths, licensed visual sources, real source links).
4. Duplicate topic and duplicate caption detection.
5. Publisher checks the media URLs, the daily quota and the daily cap, and never double-posts.
Rows only become `approved` after their slides exist (`pending_media` -> `approved`).

## One-time setup (values typed by you, never shared in chat)
Repo -> Settings -> Secrets and variables -> Actions:
- Secrets: `ANTHROPIC_API_KEY`, `IG_USER_ID`, `IG_TOKEN`
- Variable `IG_TOKEN_ISSUED` = date the token was created (YYYY-MM-DD), so the watchdog warns before the 60-day expiry
Then set `"mode": "shadow"`, watch a few days of drafts, and only then `"live"`.

## Local commands
```
python -m unittest discover -s tests
python agents/pipeline.py report      # tokens and cost per agent
python agents/autopilot.py watchdog
```
Hand-written drafts 001-007 are marked `held` in `queue.csv`: their facts were never verified.
Explainer deck: `docs/alwaysfact-autopilot.pptx`.
