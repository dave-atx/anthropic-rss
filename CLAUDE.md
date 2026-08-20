# claude-blog-rss

Scrapes `claude.com/blog` and publishes it as RSS 2.0 + Atom 1.0 feeds via GitHub Pages.
Python 3.14, managed with `uv`.

## Orientation

Four modules in `claude_blog_rss/`, ~600 LOC total. Read them in this order:

| File | Role |
|---|---|
| `models.py` | `Post` TypedDict — the on-disk schema. **Start here**; it documents every stored field. |
| `scrape.py` | HTTP + HTML parsing. Listing pages, post pages, sitemap. |
| `feed.py` | Turns `Post` dicts into RSS/Atom XML via `feedgen`. |
| `main.py` | Orchestration: load state → diff → fetch new → enrich dates → save → render. |

Data flows one direction: `scrape` → `main` → `feed`. Nothing imports `main`.

## Commands

```bash
uv sync --group dev
uv run pytest -q          # 59 tests, all offline (HTML fixtures, no network)
uv run ruff check .
uv run ruff format .
uv run update-feed        # real run — hits the network
uv run update-feed --backfill   # all historical posts
uv run update-feed --refresh    # re-fetch known posts, overwriting stored data
```

CI (`.github/workflows/ci.yml`) runs `ruff check`, `ruff format --check`, and `pytest` on
push and PR. Both must be clean before you're done.

## Things that will bite you

**The scraper targets Webflow-generated markup.** Selectors key off Webflow's class names
(`blog_post_content_wrap`, `hero_blog_post_details_item`, `u-rich-text-blog`). These are
generated and can change without notice — a silent empty result usually means the site
changed, not that the code is wrong.

**BeautifulSoup matches class tokens natively.** `class_="foo"` already matches
`class="a foo b"` and does *not* match `class="foobar"`. Do not reintroduce
`class_=re.compile(r"\bfoo\b")` — it's pure noise. The one remaining regex in `scrape.py`
matches an `href` pattern, which is genuine.

**Long posts render as multiple sibling content divs.** `_extract_body_html` concatenates
them. Grabbing only the first silently truncates the article — this was a real bug
(commit `2c80ca4`). It also skips divs inside a `w-condition-invisible` wrapper, which is
how Webflow hides unused components; without that check, testimonial text splices into
article bodies.

**`pub_date` precision is a two-stage thing.** `fetch_post` only reads the page's Date
field, giving midnight UTC. `enrich_pub_dates` later upgrades it to a real time-of-day from
the sitemap's `lastmod`, but only when `lastmod` falls on the same UTC day, and then sets
`pub_date_precise` so it's never re-evaluated. `_preserve_precise_dates` exists because
`--refresh` would otherwise erase every precise timestamp it had locked in. Touch any of
these three and check the other two.

**`feedgen` link ordering matters.** In `_build_feed_generator`, the self-referencing
`atom:link` must be added *before* the human-facing alternate link — feedgen's RSS `<link>`
takes whichever `link()` call happens last.

**Entry IDs are `tag:` URIs, not URLs.** Deliberately: they survive a domain move
(`claude.ai/blog` → `claude.com/blog` already happened once). Don't "simplify" them to the
post URL — every subscriber would see the whole feed as new.

## Verifying changes to scraping or feed rendering

Tests alone aren't enough — they don't catch output drift across all 221 stored posts. Snapshot
before and after, and diff:

```bash
uv run python -c "
import json, pathlib
from claude_blog_rss.feed import render, render_atom
from claude_blog_rss.main import POSTS_PATH
posts = json.loads(POSTS_PATH.read_text())
pl = sorted(posts.values(), key=lambda p: p.get('pub_date') or '', reverse=True)[:20]
pathlib.Path('/tmp/rss.xml').write_text(render(pl))
pathlib.Path('/tmp/atom.xml').write_text(render_atom(pl))
"
```

The **only** legitimate diff between two runs is the `lastBuildDate` (RSS) / `updated` (Atom)
line, which `feedgen` stamps at render time. Anything else is a regression.

This works offline — it renders from stored state and never hits the network.

## Data and deployment

`data/posts.json` is the scraper's state: a JSON object keyed by slug, ~2.5 MB for 221 posts,
~96% of which is `html_body`. Format is deliberate — machine-written, stdlib-only, fast.
TOML was evaluated and rejected (no stdlib writer, fragile multi-line HTML escaping, slow at
this size).

**Note its ambiguous status:** the file is tracked in git, but the workflow never commits it
back — it round-trips through `actions/cache` between runs. The committed copy is therefore a
seed that drifts from production state. A cache miss degrades gracefully (it re-scrapes recent
pages, and older posts survive from the tracked copy), but don't assume the repo copy is current.

`docs/rss.xml` and `docs/atom.xml` are gitignored build output. Pages deploys them straight from
the Actions artifact, so nothing is committed on a feed update.

## Conventions

- Be polite to the origin: keep the 1s `REQUEST_DELAY` between fetches and the descriptive
  `USER_AGENT`. This is an unofficial scraper of someone else's site.
- Network failures on a single post are logged and skipped (`fetch_post` returns `None`);
  a listing-page failure crashes the run deliberately, since it means the whole run is unreliable.
- Tests are fully offline. Keep them that way — add an HTML fixture to `tests/fixtures/`
  rather than reaching for the network or mocking `requests` ad hoc.
