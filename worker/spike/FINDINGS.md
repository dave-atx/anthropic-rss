# Spike: parsing post pages on the Workers Free plan

November 2026. Question: could the whole pipeline (scrape, store, render) move
to Cloudflare and stay on the Workers Free plan? Its binding constraint is
**10 ms of CPU per invocation**, and per *step* in Workflows (1,024 steps per
workflow, 3,000 per day on Free). Time waiting on `fetch()`, KV, R2 or D1 doesn't
count. Python Workers (Pyodide + BeautifulSoup) were ruled out without
measuring: a bs4 parse of a 600 KB page won't fit. So this measured
TypeScript parsers for one claude.com article page.

## What's here

- `parse-post.ts` is a port of `scrape._parse_claude_article` (title, sidebar
  Date / Category / Authors, JSON-LD authors and summary, og:image, body), in
  four variants:
  - `parseWithRewriter`: HTMLRewriter, re-serializing the body from its events.
  - `parseWithScan(html, url)`: regex / string scan of the whole decoded page.
  - `parseWithScan(html, url, true)`: the same, but the sidebar regex is only
    tried at tags found with `indexOf("DetailSidebar-module-scss-module__")`.
  - `parseWithStream`: reads the response as a stream, stops at the first
    `self.__next_f.push` after the body div, then runs the fast scan on that
    prefix.
- `worker.ts` is a timing Worker: `GET /?impl=rewriter|scan|scan-fast|stream|none&url=<post>|fixture[&n=N]`.
- `expected.json` holds what the Python parser extracts from the two claude.com
  post fixtures (body reduced to whitespace-collapsed text), generated with
  `_parse_claude_article`. `test/spike.spec.ts` checks every variant against
  it; all pass.

The Worker was deployed as `claude-blog-rss-parse-spike` on workers.dev and has
been **deleted**. To measure again:

```bash
cd worker
npx wrangler deploy -c spike/wrangler.jsonc
npx wrangler tail -c spike/wrangler.jsonc --format json > tail.jsonl   # each event has cpuTime / wallTime
curl 'https://claude-blog-rss-parse-spike.<subdomain>.workers.dev/?impl=stream&url=https://claude.com/resources/articles/dashboards-and-motion'
npx wrangler delete -c spike/wrangler.jsonc --force   # crawlers hit it, and every hit fetches claude.com
```

`performance.now()` doesn't advance in Workers (or in vitest's workerd pool)
without I/O, so CPU has to come from tail / observability, not in-code timers.

## Results

CPU ms per request, 10 runs each, medians with (min - max). "×10" parses the
same bytes 10 times in one request, which gives the per-parse cost apart from
per-request overhead.

| Input | none (fetch only) | HTMLRewriter | scan | scan-fast | stream |
|---|---|---|---|---|---|
| fixture (599 KB) | 1 | 20 (14 - 34) | 7.5 - 10 | 7.5 - 9 | **3 (2 - 8)** |
| fixture ×10 | 1 | 150 | 33 - 52 | 30 - 38 | **17** |
| live `dashboards-and-motion` | 1 - 2 | 11.5 | 7 - 10.5 | 6.5 | **4 (2 - 12)** |
| live `how-to-prepare-for-ai-driven-code-modernization-projects` | 1 | 15 | 8 - 9 | 7.5 - 10 | **5.5 (4 - 13)** |

(Ranges in the scan columns are across separate benchmark runs.)

- **Streaming with an early stop is the one that fits.** About 1.7 ms per parse
  once warm, 4 - 5.5 ms per live request. The maximums of 12 - 13 ms are
  probably first requests in a fresh isolate (my guess; not separated out).
- **HTMLRewriter is too slow here**, 15 ms per parse: it walks the whole
  page, ~80% of which is the Next.js payload.
- **Where things sit in a claude.com article:** og:image and JSON-LD at ~5 - 9
  KB, sidebar ~64 KB, h1 ~82 - 93 KB, body div ~95 - 103 KB (~8 - 25 KB long),
  first `self.__next_f.push` ~183 - 196 KB, end ~540 - 600 KB. The stream reads
  ~200 KB.
- **Live responses arrive in ~60 chunks of ~3 KB.** The first stream version
  did `html += chunk` then `indexOf` over the whole string per chunk, which
  copied the growing string each time and made it *slower* than a full scan
  (11 - 15 ms live). Collecting chunk strings in an array, searching only the
  new chunk plus a 64-char tail, and joining once fixed it.
- **Every request returned `outcome: "ok"`**, including one at 432 ms of CPU,
  and the account **is on Workers Free** (Dave confirmed). Over these
  benchmarks dozens of requests ran past 10 ms (HTMLRewriter, every time) and
  none was terminated. The docs say isolates get "some built-in flexibility"
  for occasional overruns and Error 1102 when it's consistent, so the 10 ms
  limit is enforced loosely. Don't design around that: "ok" here doesn't mean
  something fits, and enforcement could tighten.

## Not done yet

- **The other post parsers.** anthropic.com (news, research, launch pages)
  and claude.dev, plus `_clean_external_body` (absolute URLs, dropping buttons,
  replacing SVG charts with links). Check whether their content also comes
  before a large payload so the early stop works.
- **Rendering.** Rebuilding Atom + JSON Feed from ~2 MB of state in one step
  is the other likely place to blow 10 ms (`JSON.parse` / stringify of 2 MB
  alone is a few ms). Ideas: store each entry's rendered Atom / JSON fragment
  when the post is fetched so rendering just concatenates strings; or render
  one document per step.
- **The free-tier design sketched for this:** the cron Worker starts a
  Workflow instead of dispatching to GitHub Actions; one step per index and
  one per new post; per-post records in D1 or R2; rendered documents in R2 or
  Workers static assets. Backfill spread over several workflow instances (or
  a Queue, 10k operations/day on Free) to stay under 1,024 steps per
  workflow / 3,000 per day. Closed-year archives would lose git as their
  record unless a step commits them through the GitHub API.
- **Body serialization differs from Python.** The scan keeps the body as
  served; Python stores `str(div)` from BeautifulSoup, which re-serializes.
  Only the body's text was compared. Moving the scraper over would change
  stored bodies (and the byte-identical re-render check) unless that's
  normalized.
