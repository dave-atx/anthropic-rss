// Cheap new-post detector. Each cron tick fetches page 1 of the blog listing,
// pulls out the post slugs, and if any slug is new since the last tick, starts
// the GitHub Actions feed build. Everything else — scraping posts, dates,
// rendering — stays in the Python pipeline.
//
// Budget: the Workers Free plan allows 10 ms of CPU per invocation. The
// old Webflow listing was ~800 KB; HTMLRewriter took ~14 ms to walk it, while a
// regex scan took ~3 ms (text decode included), so the scan is deliberate.

const LISTING_URL = "https://claude.com/resources/articles";
const USER_AGENT = "claude-blog-rss/1.0 (+https://github.com/dave-atx/anthropic-rss)";
const KNOWN_KEY = "listing-slugs";

// Mirrors scrape.parse_listing: a link to /resources/articles/<slug> anywhere,
// plus listing cards (class ResourceCard-module-scss-module__<hash>__card) that
// link off site to claude.dev/blog or anthropic.com, keyed by the URL's last
// segment. Deduped in page order.
const ANCHOR = /<a\b[^>]*>/g;
const HREF = /\shref="([^"]*)"/;
const CLASS = /\sclass="([^"]*)"/;
const ARTICLE_HREF = /^(?:https:\/\/claude\.com)?\/resources\/articles\/([^/?#]+)\/?$/;
const EXTERNAL_HREF =
	/^https:\/\/(?:claude\.dev\/blog|(?:www\.)?anthropic\.com)\/(?:[^/?#]+\/)*([^/?#]+)\/?$/;
const LISTING_CARD = /(?:^|\s)ResourceCard-module-scss-module__\w+__card(?:\s|$)/;

export function listingSlugs(html: string): string[] {
	const slugs = new Set<string>();
	for (const [tag] of html.matchAll(ANCHOR)) {
		const href = HREF.exec(tag)?.[1];
		if (!href) continue;
		const onSite = ARTICLE_HREF.exec(href);
		if (onSite) {
			slugs.add(onSite[1]);
			continue;
		}
		if (!LISTING_CARD.test(CLASS.exec(tag)?.[1] ?? "")) continue;
		const offSite = EXTERNAL_HREF.exec(href);
		if (offSite) slugs.add(offSite[1]);
	}
	return [...slugs];
}

async function dispatchBuild(env: Env, fetchFn: typeof fetch): Promise<void> {
	const url = `https://api.github.com/repos/${env.DISPATCH_REPO}/actions/workflows/${env.DISPATCH_WORKFLOW}/dispatches`;
	const res = await fetchFn(url, {
		method: "POST",
		headers: {
			Accept: "application/vnd.github+json",
			Authorization: `Bearer ${env.GITHUB_TOKEN}`,
			"User-Agent": USER_AGENT,
			"X-GitHub-Api-Version": "2022-11-28",
		},
		body: JSON.stringify({ ref: env.DISPATCH_REF }),
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
	// The listing always links some articles; none means the markup or URLs moved.
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
