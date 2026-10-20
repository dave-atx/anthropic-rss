import { env } from "cloudflare:workers";
import { beforeEach, describe, expect, it } from "vitest";
import newsHtml from "../../tests/fixtures/anthropic_news_index.html?raw";
import researchHtml from "../../tests/fixtures/anthropic_research_index.html?raw";
import listingHtml from "../../tests/fixtures/articles_listing.html?raw";
import claudeDevJson from "../../tests/fixtures/claude_dev_posts.json?raw";
import expectedRaw from "../../tests/fixtures/index_slugs.json?raw";
import { anthropicSlugs, claudeDevSlugs, listingSlugs, poll } from "../src/index";

// What the Python scraper finds in each fixture; tests/test_scrape.py checks
// the same file, so the two can't drift apart.
const EXPECTED = JSON.parse(expectedRaw) as Record<string, string[]>;

// What a poll sees across all four indexes, in scrape.discover's order.
const ALL_SLUGS = [
	...new Set([
		...listingSlugs(listingHtml),
		...claudeDevSlugs(claudeDevJson),
		...anthropicSlugs(newsHtml, "news"),
		...anthropicSlugs(researchHtml, "research"),
	]),
];

const BODIES: Record<string, string> = {
	"https://claude.com/resources/product-announcements": listingHtml,
	"https://claude.dev/posts.json": claudeDevJson,
	"https://www.anthropic.com/news": newsHtml,
	"https://www.anthropic.com/research": researchHtml,
};

type Call = { url: string; init?: RequestInit };

function fakeFetch(opts: { bodies?: Record<string, string | number>; dispatchStatus?: number } = {}) {
	const calls: Call[] = [];
	const bodies = { ...BODIES, ...opts.bodies };
	const fn = (async (input: RequestInfo | URL, init?: RequestInit) => {
		const url = String(input);
		calls.push({ url, init });
		if (url in bodies) {
			const body = bodies[url];
			return typeof body === "number" ? new Response("error", { status: body }) : new Response(body);
		}
		if (url.startsWith("https://api.github.com/")) {
			return new Response(null, { status: opts.dispatchStatus ?? 204 });
		}
		throw new Error(`unexpected fetch: ${url}`);
	}) as typeof fetch;
	const dispatches = () => calls.filter((c) => c.url.startsWith("https://api.github.com/"));
	return { fn, calls, dispatches };
}

async function known(): Promise<string[] | null> {
	return env.STATE.get<string[]>("listing-slugs", "json");
}

describe("index parsers match the Python scraper", () => {
	it("on the claude.com listing", () => {
		expect(listingSlugs(listingHtml)).toEqual(EXPECTED["articles_listing.html"]);
	});

	it("on claude.dev's posts.json", () => {
		expect(claudeDevSlugs(claudeDevJson)).toEqual(EXPECTED["claude_dev_posts.json"]);
	});

	it.each([
		["news", newsHtml, "anthropic_news_index.html"],
		["research", researchHtml, "anthropic_research_index.html"],
	] as const)("on anthropic.com/%s, from links to the newest posts only", (directory, html, fixture) => {
		const slugs = anthropicSlugs(html, directory);
		expect(slugs.length).toBeGreaterThanOrEqual(15);
		// Every link the Worker finds is a post the Python side finds too.
		expect(EXPECTED[fixture]).toEqual(expect.arrayContaining(slugs));
	});

	it("skips topic links to category pages", () => {
		const html = `
			<a class="TopicLink-module-scss-module__Ab12Cd__link" href="/news/product">x</a>
			<a class="TableView-module-scss-module__Ab12Cd__title-link" href="/news/a-post">x</a>
			<a href="https://www.anthropic.com/news/absolute/">x</a>
			<a href="/research/team/interpretability">x</a>`;
		expect(anthropicSlugs(html, "news")).toEqual(["a-post", "absolute"]);
		expect(anthropicSlugs(html, "research")).toEqual([]);
	});
});

describe("listingSlugs", () => {
	it("matches off-site posts only on cards and known hosts", () => {
		const card = 'class="ResourceCard-module-scss-module__q33Tuq__card"';
		const html = `
			<a href="/resources/articles">All articles</a>
			<a href="https://www.anthropic.com/news/in-the-nav">x</a>
			<a ${card} href="https://claude.dev/blog/cross-post/">x</a>
			<a ${card} href="https://www.anthropic.com/engineering/eng-post">x</a>
			<a ${card} href="https://example.com/blog/elsewhere">x</a>
			<a href="/resources/articles/real-post">x</a>
			<a ${card} href="/resources/articles/real-post">dup</a>
			<a href="https://claude.com/resources/articles/absolute-post/">x</a>
			<a href="/resources/articles/category/news">x</a>
			<a href="/ja/resources/articles/localized">x</a>`;
		expect(listingSlugs(html)).toEqual(["cross-post", "eng-post", "real-post", "absolute-post"]);
	});
});

describe("poll", () => {
	beforeEach(async () => {
		await env.STATE.delete("listing-slugs");
	});

	it("reads every index", async () => {
		const f = fakeFetch();
		await poll(env, f.fn);
		expect(f.calls.map((c) => c.url).sort()).toEqual(Object.keys(BODIES).sort());
	});

	it("seeds state on the first run without dispatching", async () => {
		const f = fakeFetch();
		expect(await poll(env, f.fn)).toEqual([]);
		expect(f.dispatches()).toHaveLength(0);
		expect(await known()).toEqual(ALL_SLUGS);
	});

	it("does nothing when no slug is new", async () => {
		await env.STATE.put("listing-slugs", JSON.stringify(ALL_SLUGS));
		const f = fakeFetch();
		expect(await poll(env, f.fn)).toEqual([]);
		expect(f.dispatches()).toHaveLength(0);
	});

	it("dispatches the workflow when a new slug appears on any index", async () => {
		const newest = anthropicSlugs(researchHtml, "research")[0];
		await env.STATE.put("listing-slugs", JSON.stringify(ALL_SLUGS.filter((s) => s !== newest)));
		const f = fakeFetch();
		expect(await poll(env, f.fn)).toEqual([newest]);

		const [call] = f.dispatches();
		expect(call.url).toBe(
			"https://api.github.com/repos/dave-atx/anthropic-rss/actions/workflows/update-feed.yml/dispatches",
		);
		expect(call.init?.method).toBe("POST");
		expect(JSON.parse(String(call.init?.body))).toEqual({ ref: "main" });
		expect(new Set(await known())).toEqual(new Set(ALL_SLUGS));
	});

	it("keeps old state when the dispatch fails, so the next tick retries", async () => {
		const before = ALL_SLUGS.slice(1);
		await env.STATE.put("listing-slugs", JSON.stringify(before));
		const f = fakeFetch({ dispatchStatus: 401 });
		await expect(poll(env, f.fn)).rejects.toThrow(/dispatch failed: 401/);
		expect(await known()).toEqual(before);
	});

	it("keeps known slugs from an index that failed this tick", async () => {
		const extra = "no-longer-listed";
		await env.STATE.put("listing-slugs", JSON.stringify([extra, ...ALL_SLUGS.slice(1)]));
		const f = fakeFetch({ bodies: { "https://www.anthropic.com/news": 503 } });
		await expect(poll(env, f.fn)).rejects.toThrow(/anthropic\.com\/news fetch failed: 503/);
		// The other indexes were still checked, and the new slug dispatched.
		expect(f.dispatches()).toHaveLength(1);
		expect(await known()).toContain(extra);
		expect(await known()).toContain(ALL_SLUGS[0]);
	});

	it("fails loudly when an index yields no slugs, after checking the rest", async () => {
		const f = fakeFetch({
			bodies: { "https://claude.com/resources/product-announcements": "<html>redesigned</html>" },
		});
		await expect(poll(env, f.fn)).rejects.toThrow(/product-announcements yielded no slugs/);
		expect((await known())?.length).toBeGreaterThan(0);
	});
});
