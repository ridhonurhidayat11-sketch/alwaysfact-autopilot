# alwaysfact-autopilot

Instagram automation for @alwaysfact_. An AI agent drafts a week of carousel posts, you approve them, and plain scripts publish them. Slides and publishing use no AI tokens.

## How it works
1. **Sunday 08:00 WIB** `generate.yml` calls Claude once, drafts 7 posts into `queue.csv` (status `pending`), renders slides into `media/`, and opens an issue asking for approval.
2. **You** read the drafts, edit captions if needed, and change `status` from `pending` to `approved`.
3. **Every 30 minutes (07:00-22:59 WIB)** `publish.yml` runs the tests, then posts at most one approved row whose `scheduled_at` has passed, and records `posted` or `error`.

Only rows with status `approved` are ever posted.

## One-time setup (values are typed by you, never shared in chat or code)
Repo -> Settings -> Secrets and variables -> Actions -> New repository secret:
- `IG_USER_ID`: Instagram Business account id
- `IG_TOKEN`: long-lived access token (about 60 days, renew before it expires)
- `ANTHROPIC_API_KEY`: key for the weekly drafting agent

Until `IG_USER_ID` and `IG_TOKEN` exist, `publish.yml` runs the tests and skips posting. The repo must stay public so Instagram can fetch images from `raw.githubusercontent.com`.

## Safety
Anti double-post (same caption already live), one bad row never blocks the others, queue saved right after posting, media URLs checked first, daily quota respected, issue opened when posting fails. 12 tests in `tests/`.

## Local commands
```
python -m unittest discover -s tests
DRY_RUN=1 IG_USER_ID=x IG_TOKEN=x python publisher/publish.py
```

Explainer deck: `docs/alwaysfact-autopilot.pptx`.
