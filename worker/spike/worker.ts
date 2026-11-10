// Spike Worker: fetch one post page and parse it, so Workers observability
// reports the CPU time of a parse on real infrastructure.
//
//   GET /?impl=rewriter|scan|scan-fast|stream|none&url=<post url>|fixture[&n=1]
//
// `none` only buffers the response: the baseline. `n` repeats the parse on the
// same bytes, to separate parse cost from per-invocation overhead. `stream`
// on a live URL parses the fetch response directly (n is ignored), so the
// early stop also skips downloading the rest; on the fixture it reads the
// bytes in 16 KiB chunks, roughly as they'd arrive from the network.

import fixture from "../../tests/fixtures/post_example_with_author.html";
import { type ParsedPost, parseWithRewriter, parseWithScan, parseWithStream } from "./parse-post";

const USER_AGENT = "claude-blog-rss/1.0 (+https://github.com/dave-atx/anthropic-rss)";
const DEFAULT_URL = "https://claude.com/resources/articles/how-to-prepare-for-ai-driven-code-modernization-projects";

export default {
	async fetch(request: Request): Promise<Response> {
		const params = new URL(request.url).searchParams;
		const impl = params.get("impl") ?? "rewriter";
		const src = params.get("url") ?? DEFAULT_URL;
		const n = Math.min(Number(params.get("n") ?? "1") || 1, 50);

		if (impl === "stream" && src !== "fixture") {
			const res = await fetch(src, { headers: { "User-Agent": USER_AGENT } });
			if (!res.ok) return new Response(`upstream ${res.status}`, { status: 502 });
			const { post, bytes, chunks } = await parseWithStream(res, res.url);
			console.log(JSON.stringify({ streamed: { bytes, chunks } }));
			return report(impl, 1, src, bytes, post);
		}

		let bytes: ArrayBuffer;
		let finalUrl = src;
		if (src === "fixture") {
			bytes = new TextEncoder().encode(fixture).buffer as ArrayBuffer;
		} else {
			const res = await fetch(src, { headers: { "User-Agent": USER_AGENT } });
			if (!res.ok) return new Response(`upstream ${res.status}`, { status: 502 });
			finalUrl = res.url;
			bytes = await res.arrayBuffer();
		}

		let post: ParsedPost | null = null;
		for (let i = 0; i < n; i++) {
			if (impl === "rewriter") {
				post = await parseWithRewriter(new Response(bytes), finalUrl);
			} else if (impl === "scan" || impl === "scan-fast") {
				post = parseWithScan(new TextDecoder().decode(bytes), finalUrl, impl === "scan-fast");
			} else if (impl === "stream") {
				post = (await parseWithStream(new Response(chunked(bytes)), finalUrl)).post;
			}
		}
		return report(impl, n, src, bytes.byteLength, post);
	},
} satisfies ExportedHandler;

export function chunked(bytes: ArrayBuffer, size = 16 * 1024): ReadableStream<Uint8Array> {
	let offset = 0;
	return new ReadableStream({
		pull(controller) {
			if (offset >= bytes.byteLength) return controller.close();
			controller.enqueue(new Uint8Array(bytes, offset, Math.min(size, bytes.byteLength - offset)));
			offset += size;
		},
	});
}

function report(impl: string, n: number, src: string, bytes: number, post: ParsedPost | null): Response {
	const { html_body, ...fields } = post ?? ({ html_body: "" } as ParsedPost);
	console.log(JSON.stringify({ impl, n, src, bytes }));
	return Response.json({ impl, n, src, bytes, ...fields, body_length: html_body.length });
}
