# claude-blog-rss

Scrapes Claude product announcements (`claude.com/resources/product-announcements`), the
claude.dev blog, and anthropic.com news and research posts, and publishes them as one Atom 1.0 +
JSON Feed 1.1 feed via GitHub Pages, with per-year archives under RFC 5005. Until October 2026 it
scraped the whole Claude blog (`claude.com/blog`, then `claude.com/resources/articles`); those
posts stay in the corpus. Python 3.14, managed with `uv`.

## Orientation

Six modules in `claude_blog_rss/`, ~1,300 lines total. Read them in this order:

| File | Role |
|---|---|
| `models.py` | `Post` TypedDict — the stored schema. **Start here**; it documents every field. |
| `scrape.py` | HTTP + HTML parsing. The four indexes (`discover`), post pages (one parser per host), sitemap. |
| `jsonfeed.py` | `Post` ⇄ JSON Feed 1.1. This is the **state layer**, not just an output format. |
| `archives.py` | Decides which posts go in which published document (year buckets). |
| `feed.py` | Turns `Post` dicts into Atom XML via `feedgen`, including RFC 5005 archives. |
| `main.py` | Orchestration: load state → diff → fetch new → enrich dates → write documents. |

Data flows one direction: `scrape` → `main` → `{jsonfeed, archives, feed}`. Nothing imports
`main`. `jsonfeed` imports constants from `feed`; `archives` depends only on `models`.

## Commands

```bash
uv sync --group dev
uv run pytest -q          # 114 tests, all offline (HTML fixtures, no network)
uv run ruff check .
uv run ruff format .
uv run update-feed        # real run — hits the network
uv run update-feed --backfill   # every page of the claude.com listing
uv run update-feed --refresh    # re-fetch all known posts, overwriting stored data
uv run update-feed --refresh SLUG...   # re-fetch just these
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

**Posts are discovered from four indexes**, merged by `scrape.discover` in this order (first
URL wins for a slug in several): the claude.com product-announcements listing (`list_slugs`, the
articles listing filtered to one category; its cards link to `/resources/articles/<slug>`),
claude.dev's `posts.json` (`list_claude_dev`; `rss.xml` and `llms.txt` list the same posts), and
anthropic.com's `/news` and `/research` pages (`list_anthropic`). anthropic.com has no feed.
In October 2026 it folded `/engineering` into `/research` (`/engineering/<slug>` 308-redirects to
`/research/<slug>`; stored posts keep their old URL and slug) and moved every post to one CMS type.
Each index page renders links to its ~20–25 newest posts; the embedded Next.js payload
(`self.__next_f.push([1,"…"])` chunks) carries more, ~60–80, each as a
`{"_id":"…","_type":"article",…}` object with an `articleType` of `news` or `research`. Both
pages carry both types, so `parse_anthropic_index` keeps only the page's own (`ANTHROPIC_INDEXES`),
and skips objects with a `linkedUrl` (they live elsewhere, e.g. `/features/…`). It joins the
chunks, JSON-decodes each such object whole, and takes its `slug.current`. Don't go back to
regex-matching slugs out of the payload: a post without a slug would take the next object's.
Rendered links are added too, except `TopicLink…__link` anchors, which go to category pages at
the same depth (`/news/product`). Each index raises if it yields nothing, like the listing, and
any index failing fails the run. An index entry that redirects to a URL in
`SKIPPED_URL_PREFIXES` (e.g. an old engineering post that became a `code.claude.com/docs` page) is
skipped quietly by `fetch_post`; add to that tuple rather than letting it log an error every run.
claude.dev lists all its posts on one page; anthropic.com's pages only the newest, which is
enough for a normal run. The full set is only in anthropic.com's sitemap (its `lastmod` is the
last edit, not the publish date, so it can't date anything): `--backfill` reads it for
`/research/<slug>` posts (`list_anthropic_sitemap`), alongside every page of the claude.com
listing. Not for `/news`, whose sitemap entries include category pages.

**Some listing cards are posts on other hosts**, scraped for full text like the rest:
`claude.dev/blog` and `anthropic.com` (news, research, model-launch pages). These are matched
only on listing cards (`ResourceCard…__card`), because the nav links to anthropic.com too, and
only on hosts with a parser; any other off-site card is logged and skipped. Their slug is the URL's
last path segment, which is deliberate: several are old `claude.com/blog` posts that moved to
claude.dev, and keeping the slug keeps their entry id, so they don't re-appear as new.
`fetch_post` picks the parser from the URL *after redirects* and stores that final URL. It parses
`resp.content`, not `resp.text`: claude.dev sends no charset header, `requests` then assumes
Latin-1, and every curly quote came out garbled. Off-site bodies go through `_clean_external_body`,
which drops buttons, makes root-relative `src`/`href` absolute, and replaces inline SVG charts with
a link to the original (keeping claude.dev's `aria-label` description). Feed readers strip SVG —
Miniflux drops the tags and leaves the axis labels as stray text — and these charts are styled by
the site's CSS, so they wouldn't render standalone anyway. On-site bodies are stored as served. anthropic.com news pages carry a
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
committing any closed-year archive it changes). After a parser change, the workflow's `refresh`
input re-fetches specific slugs (or `all`); prefer slugs, since a full refresh also rewrites every
pre-move post's URL and categories.

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
reads the same four indexes in parallel, compares slugs with the set stored in KV, and on a new one
calls `workflow_dispatch` on `update-feed.yml`. KV is written only after a successful dispatch, so
failures retry next tick, and known slugs accumulate rather than being replaced, so an index that
fails one tick doesn't make its posts look new the next; the poll still throws so the failure is
visible. Its parsers mirror `parse_listing`, `parse_claude_dev_index` and `parse_anthropic_index`,
except that for anthropic.com it reads only rendered links (still skipping topic links): decoding
the payload costs ~6 ms of CPU, the whole budget, and the links already cover the newest posts. Change one side, change both.
`tests/fixtures/index_slugs.json` holds what the Python parsers find in each index fixture; both
`tests/test_scrape.py` and `worker/test/index.spec.ts` check against it. Regenerate it from the
Python parsers when you refresh a fixture. All four parsers together take ~3–4.5 ms (decode
included) on the fixtures. The workflow's
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
