# claude-blog-rss

Scrapes `claude.com/blog` and publishes it as Atom 1.0 + JSON Feed 1.1 via GitHub Pages,
with per-year archives under RFC 5005. Python 3.14, managed with `uv`.

## Orientation

Six modules in `claude_blog_rss/`, ~850 LOC total. Read them in this order:

| File | Role |
|---|---|
| `models.py` | `Post` TypedDict — the stored schema. **Start here**; it documents every field. |
| `scrape.py` | HTTP + HTML parsing. Listing pages, post pages, sitemap. |
| `jsonfeed.py` | `Post` ⇄ JSON Feed 1.1. This is the **state layer**, not just an output format. |
| `archives.py` | Decides which posts go in which published document (year buckets). |
| `feed.py` | Turns `Post` dicts into Atom XML via `feedgen`, including RFC 5005 archives. |
| `main.py` | Orchestration: load state → diff → fetch new → enrich dates → write documents. |

Data flows one direction: `scrape` → `main` → `{jsonfeed, archives, feed}`. Nothing imports
`main`. `jsonfeed` imports constants from `feed`; `archives` depends only on `models`.

## Commands

```bash
uv sync --group dev
uv run pytest -q          # 82 tests, all offline (HTML fixtures, no network)
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

**Rendered output must never touch the wall clock.** Archive documents are immutable, so a
render-time timestamp would rewrite every file on every run. `_build_feed_generator` sets the
feed-level `updated` to the newest post's `pub_date` (`EPOCH` if there is none) and *always*
calls `fe.updated()` per entry — `feedgen` silently defaults both to `datetime.now()` if you
don't. The consequence is a strong invariant: **rendering the same state twice is byte-identical.**
Any diff at all is a regression.

**JSON Feed is state, not just output.** `docs/*.json` is the only copy of the corpus — there is
no separate database. `parse_jsonfeed(render_jsonfeed(posts))` must reproduce every `Post` field
exactly; `tests/test_jsonfeed.py::test_full_corpus_round_trip` enforces it over the real stored
corpus. Anything a `Post` gains that JSON Feed has no native slot for belongs in the
`_claude_blog_rss` extension object (this is spec-sanctioned — JSON Feed reserves `_` prefixes),
*not* folded lossily into a native field. Two legacy posts (`artifacts`, `max-plan`) predate
`authors`/`summary`/`image_url` and lack those keys; the round trip normalizes them to schema
defaults, which is why that test applies defaults to its expected side.

**Closed-year archives are committed; CI never commits.** If a run changes an already-committed
`archive-YYYY.json` — normally because `--backfill` found a post nobody had seen — the new file
deploys but the next checkout reverts it, and the post is lost again. `save_documents` logs a
warning when this happens. Commit the file.

**Entry IDs are `tag:` URIs, not URLs.** Deliberately: they survive a domain move
(`claude.ai/blog` → `claude.com/blog` already happened once). Don't "simplify" them to the
post URL — every subscriber would see the whole feed as new.

**`gh` resolves to the wrong repo in a fresh clone.** This repo has an `upstream` remote
(`tim-hilde/anthropic-rss`, the fork parent). With no default set, `gh` resolves a fork's
`origin` to its parent, so `gh run list` / `gh workflow list` silently report *upstream's*
Actions history — which still runs the old daily cron and different commits. This has
produced false "the workflow isn't firing" conclusions. Fix once per clone:

```bash
gh repo set-default dave-atx/anthropic-rss
```

Sanity-check with `gh repo view --json nameWithOwner` before trusting any `gh` output.

## Verifying changes to scraping or feed rendering

Tests alone aren't enough — they don't catch output drift across the whole stored corpus. Rebuild
every document from stored state and diff:

```bash
cp -r docs /tmp/docs-before
uv run python -c "
from claude_blog_rss.main import load_state, save_documents
save_documents(load_state())
"
diff -r /tmp/docs-before docs    # must be empty
```

Rendering is fully deterministic, so **any** diff is a regression — there is no timestamp line to
excuse. This works offline: it renders from stored state and never hits the network.

To compare entry-level output across a refactor that *is* meant to change document structure,
diff the `<entry>` elements rather than whole files:

```bash
uv run python -c "
import json, pathlib, xml.etree.ElementTree as ET
from claude_blog_rss.feed import render_atom
from claude_blog_rss.main import load_state
A = '{http://www.w3.org/2005/Atom}'
pl = sorted(load_state().values(), key=lambda p: p.get('pub_date') or '', reverse=True)
out = {}
for c in range(0, len(pl), 40):
    for e in ET.fromstring(render_atom(pl[c:c + 40])).findall(A + 'entry'):
        out[e.find(A + 'id').text] = ET.tostring(e, encoding='unicode')
pathlib.Path('/tmp/entries.json').write_text(json.dumps(out, indent=0, sort_keys=True))
"
```

## Data and deployment

Everything published lives in `docs/`, and the JSON half of it *is* the state:

| Path | Committed? | Role |
|---|---|---|
| `docs/archive-YYYY.json` | **yes** | Closed year. State + a subscribable JSON Feed. Immutable. |
| `docs/feed.json` | no | Current year. State + subscription JSON Feed. Rebuilt every run. |
| `docs/atom.xml` | no | Subscription feed: current year, min 20 entries. |
| `docs/archive-YYYY.xml` | no | RFC 5005 archive, `<fh:archive/>`, chained by `prev/next-archive`. |
| `docs/index.html` | yes | Landing page. Hand-maintained. |

The JSON documents partition the corpus exactly — every post is in exactly one of them, which is
what makes them safe as state (`tests/test_archives.py` asserts it). The Atom subscription
document deliberately does *not* follow that rule: it tops up from the previous year to clear 20
entries, so in January an entry appears both in `atom.xml` and in an archive. RFC 5005 tolerates
that; clients dedupe on the `atom:id` tag URI.

Why XML isn't committed: it is regenerable and coupled to the `feedgen` version, so a library
bump would rewrite every "immutable" archive file. JSON has no such coupling.

Why the current year isn't committed: it changes twice a day and is ~2 MB by December. It is
recovered in order from `actions/cache` → the copy deployed on Pages (`fetch_text`) → a
re-scrape. Closed years always come from git, so history cannot be lost to a cache miss.

TOML was evaluated for the old state file and rejected (no stdlib writer, fragile multi-line HTML
escaping, slow at this size). JSON Feed replaced it: same lossless string round-trip, plus an
external spec and `_`-prefixed extension fields.

## Conventions

- Be polite to the origin: keep the 1s `REQUEST_DELAY` between fetches and the descriptive
  `USER_AGENT`. This is an unofficial scraper of someone else's site.
- Network failures on a single post are logged and skipped (`fetch_post` returns `None`);
  a listing-page failure crashes the run deliberately, since it means the whole run is unreliable.
- Tests are fully offline. Keep them that way — add an HTML fixture to `tests/fixtures/`
  rather than reaching for the network or mocking `requests` ad hoc.
