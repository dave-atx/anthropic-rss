import { env } from "cloudflare:workers";
import { beforeEach, describe, expect, it } from "vitest";
import listingHtml from "../../tests/fixtures/blog_listing.html?raw";
import { listingSlugs, poll } from "../src/index";

// What scrape.list_slugs returns for the same fixture, in order.
const FIXTURE_SLUGS = [
	"meet-the-winners-of-our-built-with-opus-4-6-claude-code-hackathon",
	"best-practices-for-using-claude-opus-4-7-with-claude-code",
	"using-claude-code-session-management-and-1m-context",
	"claude-code-desktop-redesign",
	"introducing-routines-in-claude-code",
	"preparing-your-security-program-for-ai-accelerated-offense",
	"seeing-like-an-agent",
	"multi-agent-coordination-patterns",
	"the-advisor-strategy",
	"cowork-for-enterprise",
	"claude-managed-agents",
	"carta-healthcare-clinical-abstractor",
	"subagents-in-claude-code",
	"harnessing-claudes-intelligence",
	"claude-platform-compliance-api",
];

type Call = { url: string; init?: RequestInit };

function fakeFetch(opts: { listing?: string; dispatchStatus?: number } = {}) {
	const calls: Call[] = [];
	const fn = (async (input: RequestInfo | URL, init?: RequestInit) => {
		const url = String(input);
		calls.push({ url, init });
		if (url === "https://claude.com/blog") {
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

	it("ignores links outside listing cards and nested paths", () => {
		const html = `
			<a data-cta="Blog page" href="/blog/stray">x</a>
			<div role="listitem" class="blog_cms_item w-dyn-item">
				<a data-cta="Blog page" href="/blog/real-post">x</a>
				<a data-cta="Blog page" href="/blog/category/news">x</a>
				<a data-cta="Other" href="/blog/other">x</a>
			</div>`;
		expect(listingSlugs(html)).toEqual(["real-post"]);
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
