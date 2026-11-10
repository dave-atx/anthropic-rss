// Spike: parse a claude.com/resources/articles page in a Worker, to measure
// CPU time on the Workers Free plan (10 ms per invocation / Workflow step).
// Mirrors scrape._parse_claude_article. Two implementations, so the numbers
// can be compared: HTMLRewriter (streaming, lol-html) and a plain string scan.

export type ParsedPost = {
	url: string;
	title: string;
	date_str: string;
	pub_date: string;
	categories: string[];
	authors: string[];
	summary: string;
	image_url: string;
	html_body: string;
};

// ---- shared helpers ---------------------------------------------------------

const NAMED: Record<string, string> = {
	amp: "&",
	lt: "<",
	gt: ">",
	quot: '"',
	apos: "'",
	nbsp: " ",
	rsquo: "’",
	lsquo: "‘",
	rdquo: "”",
	ldquo: "“",
	mdash: "—",
	ndash: "–",
	hellip: "…",
};

export function decodeEntities(s: string): string {
	if (!s.includes("&")) return s;
	return s.replace(/&(#[xX][0-9a-fA-F]+|#\d+|[a-zA-Z]+);/g, (m, e: string) => {
		if (e[0] === "#") {
			const code = e[1] === "x" || e[1] === "X" ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10);
			return Number.isFinite(code) ? String.fromCodePoint(code) : m;
		}
		return NAMED[e] ?? m;
	});
}

const MONTHS = [
	"january",
	"february",
	"march",
	"april",
	"may",
	"june",
	"july",
	"august",
	"september",
	"october",
	"november",
	"december",
];

// scrape._parse_date: "%B %d, %Y" or "%b %d, %Y", as midnight UTC.
export function parseDate(s: string): string {
	const m = /^([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})$/.exec(s.trim());
	if (!m) return "";
	const name = m[1].toLowerCase();
	const month = MONTHS.findIndex((full) => full === name || (name.length === 3 && full.startsWith(name)));
	if (month < 0) return "";
	const pad = (n: number) => String(n).padStart(2, "0");
	return `${m[3]}-${pad(month + 1)}-${pad(Number(m[2]))}T00:00:00+00:00`;
}

const LD_TYPES = new Set(["BlogPosting", "NewsArticle", "Article"]);
type JsonLd = Record<string, unknown>;

// scrape._extract_json_ld: the first article object, top level or in @graph.
function articleJsonLd(blocks: string[]): JsonLd {
	for (const raw of blocks) {
		let data: unknown;
		try {
			data = JSON.parse(raw);
		} catch {
			continue;
		}
		if (!data || typeof data !== "object" || Array.isArray(data)) continue;
		const d = data as JsonLd;
		const items = Array.isArray(d["@graph"]) && d["@graph"].length ? (d["@graph"] as unknown[]) : [d];
		for (const item of items) {
			if (item && typeof item === "object" && LD_TYPES.has((item as JsonLd)["@type"] as string)) {
				return item as JsonLd;
			}
		}
	}
	return {};
}

type Detail = { text: string; links: string[] };

// scrape._extract_detail_list: link texts, else comma-separated text.
function detailList(details: Map<string, Detail>, ...labels: string[]): string[] {
	for (const label of labels) {
		const d = details.get(label);
		if (!d) continue;
		const texts = d.links.length ? d.links : d.text.split(",");
		return texts.map((t) => t.trim()).filter(Boolean);
	}
	return [];
}

function assemble(
	url: string,
	title: string,
	details: Map<string, Detail>,
	ldBlocks: string[],
	ogImages: string[],
	body: string,
): ParsedPost {
	const ld = articleJsonLd(ldBlocks);
	const dateStr = details.get("Date")?.text ?? "";
	let rawAuthors = ld.author ?? [];
	if (rawAuthors && typeof rawAuthors === "object" && !Array.isArray(rawAuthors)) rawAuthors = [rawAuthors];
	const authors = (rawAuthors as JsonLd[])
		.filter((a) => a && typeof a === "object" && a["@type"] !== "Organization")
		.map((a) => decodeEntities(String(a.name ?? "")).trim())
		.filter(Boolean);
	const images = ogImages.map((u) => u.trim()).filter(Boolean);
	return {
		url,
		title,
		date_str: dateStr,
		pub_date: parseDate(dateStr),
		categories: detailList(details, "Category", "Categories"),
		authors: authors.length ? authors : detailList(details, "Author", "Authors"),
		summary: decodeEntities(String(ld.description ?? "")).trim(),
		image_url: images.length ? images[images.length - 1] : String(ld.image ?? "").trim(),
		html_body: body,
	};
}

// BeautifulSoup's get_text(strip=True): strip each text node, join with "".
class TextNodes {
	private nodes: string[] = [];
	private buf = "";
	add(chunk: string, last: boolean) {
		this.buf += chunk;
		if (last) {
			const t = decodeEntities(this.buf).trim();
			if (t) this.nodes.push(t);
			this.buf = "";
		}
	}
	toString() {
		return this.nodes.join("");
	}
}

function textOfHtml(html: string): string {
	return html
		.split(/<[^>]*>/)
		.map((s) => decodeEntities(s).trim())
		.filter(Boolean)
		.join("");
}

// ---- HTMLRewriter -----------------------------------------------------------

const SIDEBAR_LABEL = /(?:^|\s)DetailSidebar-module-scss-module__[\w-]+__label(?:\s|$)/;
const SIDEBAR_VALUE = /(?:^|\s)DetailSidebar-module-scss-module__[\w-]+__value(?:\s|$)/;
const SIDEBAR_SELECTOR = '[class*="DetailSidebar-module-scss-module__"]';

const VOID = new Set([
	"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr",
]);

function escapeAttr(v: string): string {
	return v.replace(/&/g, "&amp;").replace(/"/g, "&quot;");
}

function startTag(el: Element): string {
	let s = `<${el.tagName}`;
	for (const [name, value] of el.attributes) s += ` ${name}="${escapeAttr(value)}"`;
	return s + ">";
}

export async function parseWithRewriter(res: Response, url: string): Promise<ParsedPost> {
	let h1State: "before" | "in" | "done" = "before";
	const title = new TextNodes();

	const details = new Map<string, Detail>();
	let label: TextNodes | null = null;
	let pendingLabel: string | null = null;
	let value: { text: TextNodes; links: TextNodes[] } | null = null;
	let link: TextNodes | null = null;

	const ldBlocks: string[] = [];
	let ld: string | null = null;
	const ogImages: string[] = [];

	const body: string[] = [];
	let bodyDepth = 0;

	const rewriter = new HTMLRewriter()
		.on("h1", {
			element(el) {
				if (h1State !== "before") return;
				h1State = "in";
				el.onEndTag(() => {
					h1State = "done";
				});
			},
		})
		.on(SIDEBAR_SELECTOR, {
			element(el) {
				const cls = el.getAttribute("class") ?? "";
				if (SIDEBAR_LABEL.test(cls)) {
					const l = new TextNodes();
					label = l;
					el.onEndTag(() => {
						pendingLabel = l.toString();
						label = null;
					});
				} else if (SIDEBAR_VALUE.test(cls) && pendingLabel !== null) {
					const name = pendingLabel;
					const v = { text: new TextNodes(), links: [] as TextNodes[] };
					value = v;
					pendingLabel = null;
					el.onEndTag(() => {
						if (!details.has(name)) {
							details.set(name, { text: v.text.toString(), links: v.links.map(String) });
						}
						value = null;
					});
				}
			},
		})
		.on(`${SIDEBAR_SELECTOR} a`, {
			element(el) {
				if (!value) return;
				const t = new TextNodes();
				value.links.push(t);
				link = t;
				el.onEndTag(() => {
					link = null;
				});
			},
		})
		.on('script[type="application/ld+json"]', {
			element(el) {
				ld = "";
				el.onEndTag(() => {
					if (ld !== null) ldBlocks.push(ld);
					ld = null;
				});
			},
			text(t) {
				if (ld !== null) ld += t.text;
			},
		})
		.on('meta[property="og:image"]', {
			element(el) {
				ogImages.push(decodeEntities(el.getAttribute("content") ?? ""));
			},
		})
		// The body div and everything in it, re-serialized from the parse.
		.on("div.text-rich-text--article, div.text-rich-text--article *", {
			element(el) {
				body.push(startTag(el));
				bodyDepth++;
				if (!VOID.has(el.tagName)) {
					el.onEndTag((end) => {
						body.push(`</${end.name}>`);
						bodyDepth--;
					});
				} else {
					bodyDepth--;
				}
			},
		})
		.onDocument({
			text(t) {
				if (bodyDepth > 0) body.push(t.text);
				if (h1State === "in") title.add(t.text, t.lastInTextNode);
				if (label) label.add(t.text, t.lastInTextNode);
				if (value) value.text.add(t.text, t.lastInTextNode);
				if (link) link.add(t.text, t.lastInTextNode);
			},
		});

	await rewriter.transform(res).arrayBuffer();
	return assemble(url, title.toString(), details, ldBlocks, ogImages, body.join(""));
}

// ---- string scan ------------------------------------------------------------

const H1 = /<h1\b[^>]*>([\s\S]*?)<\/h1>/;
const SIDEBAR_PAIR =
	/<(\w+)\b[^>]*\bclass="(?:[^"]*\s)?DetailSidebar-module-scss-module__[\w-]+__label(?:\s[^"]*)?"[^>]*>([\s\S]*?)<\/\1>\s*<(\w+)\b[^>]*\bclass="(?:[^"]*\s)?DetailSidebar-module-scss-module__[\w-]+__value(?:\s[^"]*)?"[^>]*>([\s\S]*?)<\/\3>/g;
const LINK_TEXT = /<a\b[^>]*>([\s\S]*?)<\/a>/g;
const LD_JSON = /<script\b[^>]*\btype="application\/ld\+json"[^>]*>([\s\S]*?)<\/script>/g;
const META = /<meta\b[^>]*>/g;
const PROPERTY_OG_IMAGE = /\sproperty="og:image"/;
const CONTENT = /\scontent="([^"]*)"/;
const BODY_START = /<div\b[^>]*\bclass="(?:[^"]*\s)?text-rich-text--article(?:\s[^"]*)?"[^>]*>/g;
const DIV_TAG = /<(\/?)div\b[^>]*>/g;

function bodyDivs(html: string): string {
	const out: string[] = [];
	BODY_START.lastIndex = 0;
	let m: RegExpExecArray | null;
	while ((m = BODY_START.exec(html))) {
		let depth = 1;
		DIV_TAG.lastIndex = BODY_START.lastIndex;
		let end = -1;
		let d: RegExpExecArray | null;
		while ((d = DIV_TAG.exec(html))) {
			depth += d[1] ? -1 : 1;
			if (depth === 0) {
				end = DIV_TAG.lastIndex;
				break;
			}
		}
		if (end < 0) break;
		out.push(html.slice(m.index, end));
		BODY_START.lastIndex = end;
	}
	return out.join("");
}

function addDetail(details: Map<string, Detail>, labelHtml: string, valueHtml: string) {
	const name = textOfHtml(labelHtml);
	if (details.has(name)) return;
	details.set(name, {
		text: textOfHtml(valueHtml),
		links: [...valueHtml.matchAll(LINK_TEXT)].map((l) => textOfHtml(l[1])),
	});
}

// The same pairs as SIDEBAR_PAIR, but the regex is only tried at tags that
// carry a DetailSidebar class, found with indexOf, instead of at every `<`.
const SIDEBAR_PREFIX = "DetailSidebar-module-scss-module__";
const SIDEBAR_PAIR_AT = new RegExp(SIDEBAR_PAIR.source, "y");

function sidebarDetails(html: string, details: Map<string, Detail>) {
	for (let i = html.indexOf(SIDEBAR_PREFIX); i >= 0; i = html.indexOf(SIDEBAR_PREFIX, i + 1)) {
		SIDEBAR_PAIR_AT.lastIndex = html.lastIndexOf("<", i);
		const m = SIDEBAR_PAIR_AT.exec(html);
		if (!m) continue;
		addDetail(details, m[2], m[4]);
		i = SIDEBAR_PAIR_AT.lastIndex - 1;
	}
}

export function parseWithScan(html: string, url: string, fastSidebar = false): ParsedPost {
	const h1 = H1.exec(html);
	const details = new Map<string, Detail>();
	if (fastSidebar) {
		sidebarDetails(html, details);
	} else {
		for (const [, , labelHtml, , valueHtml] of html.matchAll(SIDEBAR_PAIR)) addDetail(details, labelHtml, valueHtml);
	}
	const ldBlocks = [...html.matchAll(LD_JSON)].map((m) => m[1]);
	const ogImages: string[] = [];
	for (const [tag] of html.matchAll(META)) {
		if (PROPERTY_OG_IMAGE.test(tag)) ogImages.push(decodeEntities(CONTENT.exec(tag)?.[1] ?? ""));
	}
	return assemble(url, h1 ? textOfHtml(h1[1]) : "", details, ldBlocks, ogImages, bodyDivs(html));
}

// ---- streamed scan, stopping early ------------------------------------------

// Everything a post needs (head metadata, sidebar, body) comes before the
// Next.js payload, which is ~80% of the page. Read the response as a stream
// and stop at the first payload script after the body starts. Any further
// body divs in the rendered HTML before it are still read. If no body turns
// up, this reads the whole page, like parseWithScan.
const PAYLOAD_SCRIPT = "self.__next_f.push";
const BODY_CLASS = "text-rich-text--article";

export async function readUntilPayload(
	res: Response,
): Promise<{ html: string; bytes: number; chunks: number; stopped: boolean }> {
	const reader = res.body!.getReader();
	const decoder = new TextDecoder();
	// Collected as parts and joined once: appending to one string and
	// searching it after every chunk copies the whole prefix each time.
	const parts: string[] = [];
	let bytes = 0;
	let tail = ""; // the end of the previous chunk, so a marker split across chunks is still found
	let seenBody = false;
	for (;;) {
		const { done, value } = await reader.read();
		if (done) {
			parts.push(decoder.decode());
			return { html: parts.join(""), bytes, chunks: parts.length - 1, stopped: false };
		}
		bytes += value.byteLength;
		const text = decoder.decode(value, { stream: true });
		parts.push(text);
		const window = tail + text;
		let from = 0;
		if (!seenBody) {
			const at = window.indexOf(BODY_CLASS);
			if (at >= 0) {
				seenBody = true;
				from = at;
			}
		}
		if (seenBody && window.indexOf(PAYLOAD_SCRIPT, from) >= 0) {
			await reader.cancel();
			return { html: parts.join(""), bytes, chunks: parts.length, stopped: true };
		}
		tail = window.slice(-64);
	}
}

export async function parseWithStream(
	res: Response,
	url: string,
): Promise<{ post: ParsedPost; bytes: number; chunks: number }> {
	const { html, bytes, chunks } = await readUntilPayload(res);
	return { post: parseWithScan(html, url, true), bytes, chunks };
}
