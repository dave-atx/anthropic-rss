import { env } from "cloudflare:workers";
import { beforeEach, describe, expect, it } from "vitest";
import listingHtml from "../../tests/fixtures/articles_listing.html?raw";
import { listingSlugs, poll } from "../src/index";

// What scrape.list_slugs returns for the same fixture, in order.
const FIXTURE_SLUGS = [
	"dashboards-and-motion",
	"how-block-orchestrates-claude-fable-across-thousands-of-pull-requests",
	"how-comcast-booz-allen-use-claude-mythos-to-secure-their-codebases",
	"claude-now-works-in-google-docs-sheets-and-slides",
	"were-expanding-the-claude-startups-program-to-help-founders-build",
	"how-cresta-turned-cx-expertise-into-an-agent-builder-on-the-claude-agent-sdk",
];

type Call = { url: string; init?: RequestInit };

function fakeFetch(opts: { listing?: string; dispatchStatus?: number } = {}) {
	const calls: Call[] = [];
	const fn = (async (input: RequestInfo | URL, init?: RequestInit) => {
		const url = String(input);
		calls.push({ url, init });
		if (url === "https://claude.com/resources/articles") {
			return new Response(opts.listing ?? listingHtml, { headers: { "content-type": "text/html" } });
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

describe("listingSlugs", () => {
	it("matches the Python scraper on the fixture", () => {
		expect(listingSlugs(listingHtml)).toEqual(FIXTURE_SLUGS);
	});

	it("ignores off-site, index, nested, and localized links", () => {
		const html = `
			<a href="/resources/articles">All articles</a>
			<a href="https://claude.dev/blog/cross-post/">x</a>
			<a class="card" href="/resources/articles/real-post"><h3>Real</h3></a>
			<a href="/resources/articles/real-post">dup</a>
			<a href="https://claude.com/resources/articles/absolute-post/">x</a>
			<a href="/resources/articles/category/news">x</a>
			<a href="/ja/resources/articles/localized">x</a>`;
		expect(listingSlugs(html)).toEqual(["real-post", "absolute-post"]);
	});
});

describe("poll", () => {
	beforeEach(async () => {
		await env.STATE.delete("listing-slugs");
	});

	it("seeds state on the first run without dispatching", async () => {
		const f = fakeFetch();
		expect(await poll(env, f.fn)).toEqual([]);
		expect(f.dispatches()).toHaveLength(0);
		expect(await known()).toEqual(FIXTURE_SLUGS);
	});

	it("does nothing when no slug is new", async () => {
		await env.STATE.put("listing-slugs", JSON.stringify(FIXTURE_SLUGS));
		const f = fakeFetch();
		expect(await poll(env, f.fn)).toEqual([]);
		expect(f.dispatches()).toHaveLength(0);
	});

	it("dispatches the workflow when a new slug appears", async () => {
		await env.STATE.put("listing-slugs", JSON.stringify(FIXTURE_SLUGS.slice(1)));
		const f = fakeFetch();
		expect(await poll(env, f.fn)).toEqual([FIXTURE_SLUGS[0]]);

		const [call] = f.dispatches();
		expect(call.url).toBe(
			"https://api.github.com/repos/dave-atx/anthropic-rss/actions/workflows/update-feed.yml/dispatches",
		);
		expect(call.init?.method).toBe("POST");
		expect(JSON.parse(String(call.init?.body))).toEqual({ ref: "main" });
		expect(await known()).toEqual(FIXTURE_SLUGS);
	});

	it("keeps old state when the dispatch fails, so the next tick retries", async () => {
		const before = FIXTURE_SLUGS.slice(1);
		await env.STATE.put("listing-slugs", JSON.stringify(before));
		const f = fakeFetch({ dispatchStatus: 401 });
		await expect(poll(env, f.fn)).rejects.toThrow(/dispatch failed: 401/);
		expect(await known()).toEqual(before);
	});

	it("fails loudly when the listing has no cards", async () => {
		const f = fakeFetch({ listing: "<html><body>redesigned</body></html>" });
		await expect(poll(env, f.fn)).rejects.toThrow(/no slugs/);
		expect(await known()).toBeNull();
	});
});
