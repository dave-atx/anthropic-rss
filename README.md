# claude-blog-rss

Unofficial daily-updated RSS feed for [claude.com/blog](https://claude.com/blog).

## Subscribe

```
https://tim-hilde.github.io/anthropic-rss/rss.xml
```

Add this URL to your feed reader (Feedly, NetNewsWire, Miniflux, …).

The feed is updated daily at 06:00 UTC and contains the 20 most recent posts with full article text.

## How it works

A GitHub Actions cron job runs every day, scrapes `claude.com/blog` for new posts, and commits the updated `docs/rss.xml` and `data/posts.json` to this repo. GitHub Pages serves `docs/` as the site.

## Setup (fork this repo)

1. Fork or clone
2. Create a new GitHub repo and push
3. In repo Settings → Pages, set Source = `main` branch, Folder = `/docs`
4. Update `FEED_URL` in `src/feed.py` (or set `FEED_URL` as a repo variable/secret)
5. Run the initial backfill manually:

   ```
   uv sync
   uv run python -m src.main --backfill
   git add data/posts.json docs/rss.xml && git commit -m "Initial backfill"
   git push
   ```

6. The daily workflow in `.github/workflows/update-feed.yml` takes it from there

## Development

```bash
uv sync --group dev
uv run pytest
uv run python -m src.main          # daily run (checks page 1-2)
uv run python -m src.main --backfill   # fetch all historical posts
uv run python -m src.main --refresh    # re-fetch all known posts, overwriting stored data
```

## Disclaimer

Content © Anthropic. This is a personal convenience tool and is not affiliated with Anthropic.
