# alwaysfact_ Instagram automation

Flow: weekly agents draft rows into `queue.csv` as `pending` -> you review and change
`status` to `approved` -> `.github/workflows/publish.yml` runs every 20 minutes and posts
at most one approved, due row, then marks it `posted` or `error`.

## One-time setup (you do this, secrets never go in chat or code)
1. Create a private GitHub repo and push this folder.
2. Repo -> Settings -> Secrets and variables -> Actions -> New repository secret:
   - `IG_USER_ID`: the Instagram Business account id
   - `IG_TOKEN`: the long-lived access token
3. Actions tab -> run "Publish approved Instagram posts" once with `workflow_dispatch`.

## Test without posting
`DRY_RUN=1 IG_USER_ID=x IG_TOKEN=x python publisher/publish.py`

## Notes
- Images must be public JPEG URLs. Carousels use 2-10 URLs separated by spaces.
- Long-lived tokens last about 60 days; renew before they expire.
- The Instagram API has no native scheduling, so the cron job is the scheduler.
