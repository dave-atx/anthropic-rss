// Cheap new-post detector. Each cron tick reads the four post indexes the
// Python pipeline reads (scrape.discover), pulls out the post slugs, and if any
// slug is new since the last tick, starts the GitHub Actions feed build.
// Everything else — scraping posts, dates, rendering — stays in the Python
// pipeline.
//
// Budget: the Workers Free plan allows 10 ms of CPU per invocation. The
// old Webflow listing was ~800 KB; HTMLRewriter took ~14 ms to walk it, while a
// regex scan took ~3 ms (text decode included), so the scans are deliberate.

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
const LISTING_CARD = /(?:^|\s)ResourceCard-module-scss-module__[\w-]+__card(?:\s|$)/;

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

// Mirrors scrape.parse_claude_dev_index.
type ClaudeDevIndex = { posts?: { slug?: string; url?: string; draft?: boolean }[] };

export function claudeDevSlugs(json: string): string[] {
	const index = JSON.parse(json) as ClaudeDevIndex;
	return (index.posts ?? []).filter((p) => p.slug && p.url && !p.draft).map((p) => p.slug as string);
}

// The link half of scrape.parse_anthropic_index: links to /<directory>/<slug>
// in the rendered HTML, except topic links (class
// TopicLink-module-scss-module__<hash>__link), which go to category pages such
// as /news/product. Deduped in page order. The Python side also decodes the
// page's embedded Next.js payload, which lists more posts, but that costs
// ~6 ms of CPU. Each page links its ~25 newest posts, and a new post is always
// among the newest, so links are enough to detect one.
const TOPIC_LINK = /(?:^|\s)TopicLink-module-scss-module__[\w-]+__link(?:\s|$)/;

export function anthropicSlugs(html: string, directory: "news" | "research"): string[] {
	const postHref = new RegExp(`^(?:https://www\\.anthropic\\.com)?/${directory}/([^/?#]+)/?$`);
	const slugs = new Set<string>();
	for (const [tag] of html.matchAll(ANCHOR)) {
		const slug = postHref.exec(HREF.exec(tag)?.[1] ?? "")?.[1];
		if (slug && !TOPIC_LINK.test(CLASS.exec(tag)?.[1] ?? "")) slugs.add(slug);
	}
	return [...slugs];
}

type Source = { url: string; slugs: (body: string) => string[] };

// The order of scrape.discover. Each index always lists some posts, so one
// yielding none means it moved or changed shape.
export const SOURCES: Source[] = [
	{ url: "https://claude.com/resources/product-announcements", slugs: listingSlugs },
	{ url: "https://claude.dev/posts.json", slugs: claudeDevSlugs },
	{ url: "https://www.anthropic.com/news", slugs: (html) => anthropicSlugs(html, "news") },
	{
		url: "https://www.anthropic.com/research",
		slugs: (html) => anthropicSlugs(html, "research"),
	},
];

async function readSource(source: Source, fetchFn: typeof fetch): Promise<string[]> {
	const res = await fetchFn(source.url, { headers: { "User-Agent": USER_AGENT } });
	if (!res.ok) throw new Error(`${source.url} fetch failed: ${res.status}`);
	const slugs = source.slugs(await res.text());
	if (slugs.length === 0) throw new Error(`${source.url} yielded no slugs; has the markup changed?`);
	return slugs;
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
 * dispatch is retried on the next tick. Known slugs accumulate rather than
 * being replaced, so an index that fails for a tick doesn't make its posts
 * look new on the next. A failed index doesn't stop the others being checked,
 * but the poll still throws afterwards so the failure shows up.
 */
export async function poll(env: Env, fetchFn: typeof fetch = fetch): Promise<string[]> {
	const results = await Promise.allSettled(SOURCES.map((s) => readSource(s, fetchFn)));
	const errors = results.flatMap((r) => (r.status === "rejected" ? [r.reason] : []));
	const slugs = [...new Set(results.flatMap((r) => (r.status === "fulfilled" ? r.value : [])))];

	const fresh = await recordSlugs(env, fetchFn, slugs);
	if (errors.length > 0) throw new AggregateError(errors, errors.map(String).join("; "));
	return fresh;
}

async function recordSlugs(env: Env, fetchFn: typeof fetch, slugs: string[]): Promise<string[]> {
	if (slugs.length === 0) return [];
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
	await env.STATE.put(KNOWN_KEY, JSON.stringify([...known, ...fresh]));
	console.log(JSON.stringify({ event: "dispatched", fresh }));
	return fresh;
}

export default {
	async scheduled(_controller, env, _ctx): Promise<void> {
		await poll(env);
	},
} satisfies ExportedHandler<Env>;
