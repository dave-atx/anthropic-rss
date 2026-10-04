// Cheap new-post detector. Each cron tick fetches page 1 of the blog listing,
// pulls out the post slugs, and if any slug is new since the last tick, starts
// the GitHub Actions feed build. Everything else — scraping posts, dates,
// rendering — stays in the Python pipeline.
//
// Budget: the Workers Free plan allows 10 ms of CPU per invocation. The
// listing is ~800 KB; HTMLRewriter took ~14 ms to walk it, while the regex scan
// below takes ~3 ms (text decode included), so the scan is deliberate.

const LISTING_URL = "https://claude.com/blog";
const USER_AGENT = "claude-blog-rss/1.0 (+https://github.com/dave-atx/anthropic-rss)";
const KNOWN_KEY = "listing-slugs";

// Mirrors scrape.list_slugs: inside each listing card (a role="listitem"
// div with class blog_cms_item), the first "Blog page" link to /blog/<slug>.
const CARD = /<div\b[^>]*\bblog_cms_item\b[^>]*>/g;
const CARD_ROLE = /\srole="listitem"/;
const LINK = /<a\b[^>]*\sdata-cta="Blog page"[^>]*>/g;
const SLUG_HREF = /\shref="\/blog\/([^"/]+)"/;

export function listingSlugs(html: string): string[] {
	const starts = [...html.matchAll(CARD)]
		.filter((m) => CARD_ROLE.test(m[0]))
		.map((m) => m.index);
	const slugs = new Set<string>();
	starts.forEach((start, i) => {
		const end = starts[i + 1] ?? html.length;
		LINK.lastIndex = start;
		const link = LINK.exec(html);
		if (!link || link.index >= end) return;
		const m = SLUG_HREF.exec(link[0]);
		if (m) slugs.add(m[1]);
	});
	return [...slugs];
}

async function dispatchBuild(env: Env, fetchFn: typeof fetch): Promise<void> {
	const url = `https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/${env.GITHUB_WORKFLOW}/dispatches`;
	const res = await fetchFn(url, {
		method: "POST",
		headers: {
			Accept: "application/vnd.github+json",
			Authorization: `Bearer ${env.GITHUB_TOKEN}`,
			"User-Agent": USER_AGENT,
			"X-GitHub-Api-Version": "2022-11-28",
		},
		body: JSON.stringify({ ref: env.GITHUB_REF }),
	});
	if (!res.ok) {
		throw new Error(`workflow dispatch failed: ${res.status} ${await res.text()}`);
	}
}

/**
 * One poll. Returns the slugs that triggered a build (empty if none).
 *
 * The first run only seeds KV: with nothing to compare against, every slug
 * would look new. KV is written only after a successful dispatch, so a failed
 * dispatch is retried on the next tick.
 */
export async function poll(env: Env, fetchFn: typeof fetch = fetch): Promise<string[]> {
	const res = await fetchFn(LISTING_URL, { headers: { "User-Agent": USER_AGENT } });
	if (!res.ok) throw new Error(`listing fetch failed: ${res.status}`);

	const slugs = listingSlugs(await res.text());
	// Webflow class names are generated; an empty result means the markup moved.
	if (slugs.length === 0) throw new Error("listing yielded no slugs; has the markup changed?");

	const known = await env.STATE.get<string[]>(KNOWN_KEY, "json");
	if (known === null) {
		await env.STATE.put(KNOWN_KEY, JSON.stringify(slugs));
		console.log(JSON.stringify({ event: "seeded", count: slugs.length }));
		return [];
	}

	const knownSet = new Set(known);
	const fresh = slugs.filter((s) => !knownSet.has(s));
	if (fresh.length === 0) return [];

	await dispatchBuild(env, fetchFn);
	await env.STATE.put(KNOWN_KEY, JSON.stringify(slugs));
	console.log(JSON.stringify({ event: "dispatched", fresh }));
	return fresh;
}

export default {
	async scheduled(_controller, env, _ctx): Promise<void> {
		await poll(env);
	},
} satisfies ExportedHandler<Env>;
