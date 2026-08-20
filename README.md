# claude-blog-rss

Unofficial daily-updated RSS feed for [claude.com/blog](https://claude.com/blog).

## Subscribe

RSS 2.0:

```
https://dave-atx.github.io/anthropic-rss/rss.xml
```

Atom 1.0:

```
https://dave-atx.github.io/anthropic-rss/atom.xml
```

Add either URL to your feed reader (Feedly, NetNewsWire, Miniflux, …); both carry the same content.

The feed is updated twice daily (~7am and ~6pm Pacific, drifting an hour across DST — 14:00 and 01:00 UTC) and contains the 20 most recent posts with full article text.

## How it works

A GitHub Actions workflow runs twice a day, scrapes `claude.com/blog` for new posts, and rebuilds `docs/rss.xml` and `docs/atom.xml` (gitignored build output, not committed). It deploys `docs/` as a GitHub Pages artifact via `actions/upload-pages-artifact` and `actions/deploy-pages`. `data/posts.json`, the scraper's local state, is round-tripped between runs via `actions/cache` rather than committed to the repo.

## Setup (fork this repo)

1. Fork or clone
2. Create a new GitHub repo and push
3. In repo Settings → Pages, set Source = **GitHub Actions**
4. Update `_DEFAULT_FEED_URL` / `_DEFAULT_ATOM_FEED_URL` in `claude_blog_rss/feed.py` (or set the `FEED_URL` / `ATOM_FEED_URL` env vars as repo variables/secrets)
5. Run the initial backfill manually:

   ```
   uv sync
   uv run update-feed --backfill
   git add data/posts.json && git commit -m "Initial backfill"
   git push
   ```

6. The daily workflow in `.github/workflows/update-feed.yml` takes it from there

## Development

```bash
uv sync --group dev
uv run pytest
uv run ruff check .         # lint
uv run update-feed          # daily run (checks page 1-2)
uv run update-feed --backfill   # fetch all historical posts
uv run update-feed --refresh    # re-fetch all known posts, overwriting stored data
```

## Disclaimer

Content © Anthropic. This is a personal convenience tool and is not affiliated with Anthropic.
