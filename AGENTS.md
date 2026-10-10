# claude-blog-rss

Scrapes `claude.com/resources/articles` (formerly `claude.com/blog`) and publishes it as Atom 1.0 + JSON Feed 1.1 via GitHub Pages,
with per-year archives under RFC 5005. Python 3.14, managed with `uv`.

## Orientation

Six modules in `claude_blog_rss/`, ~1,300 lines total. Read them in this order:

| File | Role |
|---|---|
| `models.py` | `Post` TypedDict — the stored schema. **Start here**; it documents every field. |
| `scrape.py` | HTTP + HTML parsing. Listing pages, post pages (one parser per host), sitemap. |
| `jsonfeed.py` | `Post` ⇄ JSON Feed 1.1. This is the **state layer**, not just an output format. |
| `archives.py` | Decides which posts go in which published document (year buckets). |
| `feed.py` | Turns `Post` dicts into Atom XML via `feedgen`, including RFC 5005 archives. |
| `main.py` | Orchestration: load state → diff → fetch new → enrich dates → write documents. |

Data flows one direction: `scrape` → `main` → `{jsonfeed, archives, feed}`. Nothing imports
`main`. `jsonfeed` imports constants from `feed`; `archives` depends only on `models`.

## Commands

```bash
uv sync --group dev
uv run pytest -q          # 102 tests, all offline (HTML fixtures, no network)
uv run ruff check .
uv run ruff format .
uv run update-feed        # real run — hits the network
uv run update-feed --backfill   # all historical posts
uv run update-feed --refresh    # re-fetch known posts, overwriting stored data
```

CI (`.github/workflows/ci.yml`) runs `ruff check`, `ruff format --check`, and `pytest` on
push and PR. Both must be clean before you're done.

## Things that will bite you

**The site moved, and may move again.** Until October 2026 the blog was Webflow at
`claude.com/blog`; it is now Next.js at `claude.com/resources/articles`, and every `/blog/<slug>`
308-redirects to the same slug there. Posts stored before the move keep their `/blog/` `url`
(it still resolves); new ones get the new path. Slugs, and therefore entry IDs, carried over.
On-site articles are matched by `href` alone (`/resources/articles/<slug>`), so they survive
markup churn. Both `list_slugs` and the Worker *raise* on a listing with no article links — that
means the site moved, and the old code silently reported "no new posts" for days.

**Some listing cards are posts on other hosts**, scraped for full text like the rest:
`claude.dev/blog` and `anthropic.com` (news, engineering, model-launch pages). These are matched
only on listing cards (`ResourceCard…__card`), because the nav links to anthropic.com too, and
only on hosts with a parser; any other off-site card is logged and skipped. Their slug is the URL's
last path segment, which is deliberate: several are old `claude.com/blog` posts that moved to
claude.dev, and keeping the slug keeps their entry id, so they don't re-appear as new.
`fetch_post` picks the parser from the URL *after redirects* and stores that final URL. Off-site
bodies go through `_clean_external_body`, which drops buttons and makes root-relative `src`/`href`
absolute; on-site bodies don't need it and are stored as served. anthropic.com news pages carry a
real publish time, which is stored with `pub_date_precise`.

**Post pages use CSS-module class names with build hashes**, e.g.
`DetailSidebar-module-scss-module__3RcHtG__label`. The hash changes on redeploys, so
`_module_class` matches around it; never hard-code a hashed token. The body is
`div.text-rich-text--article` (unhashed). Authors come from the JSON-LD `author` list, falling
back to the sidebar; categories come from the sidebar's Category links.

**BeautifulSoup matches class tokens natively.** `class_="foo"` already matches
`class="a foo b"` and does *not* match `class="foobar"`. Do not reintroduce
`class_=re.compile(r"\bfoo\b")` — it's pure noise. Regexes in `scrape.py` are only for
genuine patterns: article `href`s and hashed CSS-module tokens.

**Bodies may span several rich-text divs.** `_extract_body_html` concatenates every
`text-rich-text--article` div. On the old Webflow site long posts really were split, and grabbing
only the first silently truncated the article (commit `2c80ca4`). The new site uses one div so
far; keep the concatenation.

**Listing pagination is cumulative.** `?page=N` renders every card up to page N, and past the end
it keeps rendering the full list instead of an empty page. `scrape_all_pages` therefore stops at the
first page that adds no new slug. The hourly run reads page 1 only; to pick up older posts, run the
`Update feeds` workflow by hand with `backfill` ticked (or `uv run update-feed --backfill` locally,
committing any closed-year archive it changes).

**`pub_date` precision is a three-stage thing** (plus a shortcut: an off-site page with a real
publish time sets it, precise, in `fetch_post`, and `_preserve_pub_times` lets that win over a
discovery time). `fetch_post` only reads the page's Date
field, giving midnight UTC — which readers show as the previous evening in US time zones.
`stamp_discovery_times` replaces that on a *newly discovered* post with the time the run first saw
it, if that is still the same UTC day (the Worker dispatches a run within ~10 minutes of a post appearing). `enrich_pub_dates`
later upgrades either to the real time from the sitemap's `lastmod`, but only when `lastmod` falls
on the same UTC day, and then sets `pub_date_precise` so it's never re-evaluated. Since the site
move the sitemap lists articles *without* `lastmod`, so this stage is currently dormant and the
discovery time is the only time-of-day source; it is kept in case `lastmod` returns. A discovery time
is *not* flagged precise, so the sitemap can still override it. `_preserve_pub_times` exists because
`--refresh` would otherwise erase every precise timestamp and discovery time. Touch any of these
four and check the others. The discovery time is captured once into state, not at render time.

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
(`claude.ai/blog` → `claude.com/blog` → `claude.com/resources/articles` has already happened). Don't "simplify" them to the
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

**The Worker in `worker/` only detects; it never scrapes.** It runs on Cloudflare's free plan,
whose 10 ms CPU cap per invocation is why it's TypeScript with a regex scan rather than the Python
code (HTMLRewriter took ~14 ms on the ~800 KB Webflow listing; the regex scan ~3 ms). Every 10 minutes it
reads listing page 1, compares slugs with the set stored in KV, and on a new one calls
`workflow_dispatch` on `update-feed.yml`. KV is written only after a successful dispatch, so failures
retry next tick. Its link and card matching mirrors `scrape.parse_listing` — change one, change both;
`worker/test/index.spec.ts` checks both agree on `tests/fixtures/articles_listing.html`. The workflow's
own cron is a 6-hourly backstop, so a broken Worker degrades latency, not correctness.

```bash
cd worker && npm ci
npm run check && npm test             # tsc + vitest in workerd, offline
npx wrangler secret put GITHUB_TOKEN  # fine-grained PAT: this repo, Actions read/write
```

Deploys happen in `.github/workflows/deploy-worker.yml` on pushes to `main` that touch `worker/`,
after the same checks pass. It needs repo secrets `CLOUDFLARE_API_TOKEN` ("Edit Cloudflare
Workers" template) and `CLOUDFLARE_ACCOUNT_ID`. The KV namespace id is pinned in `wrangler.jsonc`.

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

Why the current year isn't committed: it can change several times a day and is ~2 MB by December. It is
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
