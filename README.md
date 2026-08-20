# claude-blog-rss

Unofficial Atom and JSON Feed mirror of [claude.com/blog](https://claude.com/blog), with full
article text.

## Subscribe

Atom 1.0:

```
https://dave-atx.github.io/anthropic-rss/atom.xml
```

JSON Feed 1.1:

```
https://dave-atx.github.io/anthropic-rss/feed.json
```

Add either URL to your feed reader (Feedly, NetNewsWire, Miniflux, …); both carry the same posts.
Atom is the safer default — JSON Feed support is less widespread.

The feeds are updated twice daily (~7am and ~6pm Pacific, drifting an hour across DST — 14:00 and
01:00 UTC). The subscription feed carries the current year, and never fewer than the 20 most recent
posts.

## Full history

Older posts live in per-year archive documents published under
[RFC 5005](https://www.rfc-editor.org/rfc/rfc5005) (Feed Paging and Archiving). `atom.xml` links to
the most recent archive with `rel="prev-archive"`; each archive links further back the same way, is
marked `<fh:archive/>`, and points at the subscription feed with `rel="current"`.

Client support for RFC 5005 is thin, so archives are also plain URLs you can fetch or subscribe to
directly:

```
https://dave-atx.github.io/anthropic-rss/archive-2025.xml     Atom
https://dave-atx.github.io/anthropic-rss/archive-2025.json    JSON Feed
```

A year is *closed* once it is past: no post can be added to it, so its documents never change again.

## How it works

A GitHub Actions workflow runs twice a day, scrapes `claude.com/blog` for new posts, and rebuilds
every document in `docs/`, which it deploys as a GitHub Pages artifact via
`actions/upload-pages-artifact` and `actions/deploy-pages`.

The JSON Feed documents are also the scraper's state — there is no separate database. They partition
the corpus exactly: `feed.json` holds the current year, `archive-YYYY.json` holds each closed year,
and every post appears in exactly one of them. Round-tripping through JSON is lossless, including
raw `html_body`; the few fields JSON Feed has no slot for ride in the spec-sanctioned
`_claude_blog_rss` extension object.

Closed-year archives are committed, so history is durable in git and a cache miss cannot lose it.
The current year is not committed — it would be a ~2 MB blob twice a day — and is recovered, in
order, from `actions/cache`, the copy already deployed on Pages, or a re-scrape. All rendered XML is
gitignored build output: it is regenerable, and coupled to the `feedgen` version, so committing it
would churn every file on a library bump.

## Setup (fork this repo)

1. Fork or clone
2. Create a new GitHub repo and push
3. In repo Settings → Pages, set Source = **GitHub Actions**
4. Point the feeds at your own Pages site: set the `FEED_BASE_URL` environment variable (e.g.
   `https://you.github.io/anthropic-rss`), or change `_DEFAULT_BASE_URL` in
   `claude_blog_rss/feed.py`
5. Run the initial backfill and commit the closed-year archives:

   ```
   uv sync
   uv run update-feed --backfill
   git add docs/archive-*.json && git commit -m "Initial backfill"
   git push
   ```

6. The workflow in `.github/workflows/update-feed.yml` takes it from there

## Development

```bash
uv sync --group dev
uv run pytest
uv run ruff check .             # lint
uv run update-feed              # normal run (checks listing pages 1-2)
uv run update-feed --backfill   # fetch all historical posts
uv run update-feed --refresh    # re-fetch all known posts, overwriting stored data
```

## Disclaimer

Content © Anthropic. This is a personal convenience tool and is not affiliated with Anthropic.
