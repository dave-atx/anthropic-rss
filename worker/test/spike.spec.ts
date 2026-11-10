import { describe, expect, it } from "vitest";
import post from "../../tests/fixtures/post_example.html?raw";
import postWithAuthor from "../../tests/fixtures/post_example_with_author.html?raw";
import expectedRaw from "../spike/expected.json?raw";
import { decodeEntities, type ParsedPost, parseWithRewriter, parseWithScan, parseWithStream, readUntilPayload } from "../spike/parse-post";
import { chunked } from "../spike/worker";

// What scrape._parse_claude_article finds in each fixture, with the body
// reduced to its whitespace-collapsed text (spike/expected.json).
const EXPECTED = JSON.parse(expectedRaw) as Record<string, Record<string, unknown>>;
const FIXTURES: Record<string, string> = { post_example: post, post_example_with_author: postWithAuthor };

function comparable(p: ParsedPost) {
	const { html_body, ...fields } = p;
	const body_text = decodeEntities(html_body.replace(/<[^>]*>/g, ""))
		.replace(/\s+/g, " ")
		.trim();
	return { ...fields, body_text };
}

describe.each(Object.keys(FIXTURES))("%s", (name) => {
	it("scan matches the Python parser", () => {
		expect(comparable(parseWithScan(FIXTURES[name], "u"))).toEqual(EXPECTED[name]);
	});

	it("HTMLRewriter matches the Python parser", async () => {
		const res = new Response(FIXTURES[name], { headers: { "content-type": "text/html; charset=utf-8" } });
		expect(comparable(await parseWithRewriter(res, "u"))).toEqual(EXPECTED[name]);
	});

	it("scan with the indexOf sidebar matches the Python parser", () => {
		expect(comparable(parseWithScan(FIXTURES[name], "u", true))).toEqual(EXPECTED[name]);
	});

	it("streamed scan stops early and matches the Python parser", async () => {
		const bytes = new TextEncoder().encode(FIXTURES[name]).buffer as ArrayBuffer;
		const { stopped, bytes: read } = await readUntilPayload(new Response(chunked(bytes)));
		expect(stopped).toBe(true);
		expect(read).toBeLessThan(bytes.byteLength / 2);
		expect(comparable((await parseWithStream(new Response(chunked(bytes)), "u")).post)).toEqual(EXPECTED[name]);
	});
});
