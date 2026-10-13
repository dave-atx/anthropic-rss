"""Discover and scrape posts from Anthropic's sites.

Four indexes are read (see `discover`): claude.com's product-announcements
listing, claude.dev's post index, and anthropic.com's news and engineering
pages. Every post is keyed by the last segment of its URL, its slug.

The blog lived at claude.com/blog (Webflow) until October 2026, when it moved
to claude.com/resources/articles (Next.js). Old /blog/<slug> URLs 308-redirect
to the same slug under the new path, so slugs, and the tag: ids built from
them, carry over unchanged. Several old claude.com/blog posts moved to
claude.dev or anthropic.com; keying on the last segment keeps their entry ids.
"""

import html
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

from .models import Post

BASE_URL = "https://claude.com/resources/articles"
# The articles listing filtered to one category. Its cards link to articles
# under BASE_URL, and to launch posts on anthropic.com.
LISTING_URL = "https://claude.com/resources/product-announcements"
CLAUDE_DEV_INDEX_URL = "https://claude.dev/posts.json"
ANTHROPIC_URL = "https://www.anthropic.com"
# anthropic.com index pages, with the CMS type of the posts they list. The
# rendered HTML links only the first few; the page's embedded Next.js data
# carries every one.
ANTHROPIC_INDEXES = {"news": "post", "engineering": "engineeringArticle"}
# Index entries that redirect to one of these aren't posts (an engineering
# "post" that is now a docs page). Matched on the URL after redirects, so
# they're still requested once per run, but skipped quietly.
SKIPPED_URL_PREFIXES = ("https://code.claude.com/docs/",)
SITEMAP_URL = "https://claude.com/sitemap.xml"
PAGINATION_PARAM = "page"
REQUEST_DELAY = 1.0
USER_AGENT = "claude-blog-rss/1.0 (+https://github.com/dave-atx/anthropic-rss)"

_SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

# A link to an on-site article, anywhere on the listing page.
_ARTICLE_HREF = re.compile(r"^(?:https://claude\.com)?/resources/articles/([^/?#]+)/?$")
# An off-site post on a host we have a parser for. Only matched on listing
# cards, since the nav and footer link to anthropic.com pages too.
_EXTERNAL_HREF = re.compile(
    r"^https://(?:claude\.dev/blog|(?:www\.)?anthropic\.com)/(?:[^/?#]+/)*([^/?#]+)/?$"
)

logger = logging.getLogger(__name__)


def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    adapter = requests.adapters.HTTPAdapter(
        max_retries=requests.adapters.Retry(
            total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504]
        )
    )
    s.mount("https://", adapter)
    return s


_shared_session: requests.Session | None = None


def get_session() -> requests.Session:
    global _shared_session
    if _shared_session is None:
        _shared_session = _session()
    return _shared_session


def list_slugs(page: int = 1) -> list[tuple[str, str]]:
    """Return [(slug, url), ...] from the listing page, deduped, in page order.

    Raises if the page has no on-site article links at all: the listing always
    has some, so an empty result means the markup or URL scheme moved and the
    run can't be trusted.
    """
    url = LISTING_URL if page == 1 else f"{LISTING_URL}?{PAGINATION_PARAM}={page}"
    resp = get_session().get(url, timeout=30)
    resp.raise_for_status()
    results = parse_listing(resp.text)
    if not any(post_url.startswith(f"{BASE_URL}/") for _slug, post_url in results):
        raise RuntimeError(f"{url} yielded no article slugs; has the markup changed?")
    return results


_LISTING_CARD = re.compile(r"^ResourceCard-module-scss-module__\w+__card$")


def parse_listing(html_text: str) -> list[tuple[str, str]]:
    """Return [(slug, url), ...] for every post linked from a listing page.

    On-site articles are matched by href alone. Off-site posts are matched only
    on listing cards, and only on hosts `fetch_post` can parse; other off-site
    cards are logged and skipped. The same post can appear twice (featured
    carousel and grid); the first occurrence wins.
    """
    soup = BeautifulSoup(html_text, "lxml")
    seen: set[str] = set()
    results: list[tuple[str, str]] = []
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if m := _ARTICLE_HREF.match(href):
            slug, url = m.group(1), f"{BASE_URL}/{m.group(1)}"
        elif not any(_LISTING_CARD.match(c) for c in link.get("class", [])):
            continue
        elif m := _EXTERNAL_HREF.match(href):
            slug, url = m.group(1), href
        else:
            if href.startswith("http"):
                logger.warning("Skipping listing card on an unsupported host: %s", href)
            continue
        if slug not in seen:
            seen.add(slug)
            results.append((slug, url))
    return results


def list_claude_dev() -> list[tuple[str, str]]:
    """Return [(slug, url), ...] for every published post in claude.dev's index.

    Raises if the index lists none: it holds every post, so empty means it moved.
    """
    resp = get_session().get(CLAUDE_DEV_INDEX_URL, timeout=30)
    resp.raise_for_status()
    results = parse_claude_dev_index(resp.json())
    if not results:
        raise RuntimeError(f"{CLAUDE_DEV_INDEX_URL} listed no posts; has its format changed?")
    return results


def parse_claude_dev_index(index: dict) -> list[tuple[str, str]]:
    return [
        (p["slug"], p["url"])
        for p in index.get("posts") or []
        if p.get("slug") and p.get("url") and not p.get("draft")
    ]


def list_anthropic(directory: str) -> list[tuple[str, str]]:
    """Return [(slug, url), ...] for every post on an anthropic.com index page.

    Raises if the page yields none, for the same reason `list_slugs` does.
    """
    url = f"{ANTHROPIC_URL}/{directory}"
    resp = get_session().get(url, timeout=30)
    resp.raise_for_status()
    results = parse_anthropic_index(resp.text, directory)
    if not results:
        raise RuntimeError(f"{url} yielded no posts; has the markup changed?")
    return results


# A chunk of the Next.js (RSC) payload: a JSON string literal holding part of
# the stream. Chunks can split anywhere, so they're joined before parsing.
_NEXT_PUSH = re.compile(r'self\.__next_f\.push\(\[1,("[^"\\]*(?:\\.[^"\\]*)*")\]\)')


def _next_payload(html_text: str) -> str:
    return "".join(json.loads(m.group(1)) for m in _NEXT_PUSH.finditer(html_text))


def parse_anthropic_index(html_text: str, directory: str) -> list[tuple[str, str]]:
    """Return [(slug, url), ...] for an anthropic.com news or engineering page.

    Posts come from the Next.js payload embedded in the page, where each is a
    JSON object opening `{"_type":"<type>",` with a `slug.current` field; every
    such object is decoded whole, so a slug is only ever taken from its own
    post. Links in the rendered HTML are added too, so the newest posts don't
    depend on the payload's shape alone. Deduped, payload order first.

    The Worker reads only those links (the payload is too costly for its CPU
    budget): news renders links to its ten newest posts, engineering to all.
    """
    payload = _next_payload(html_text)
    opening = f'{{"_type":"{ANTHROPIC_INDEXES[directory]}",'
    decoder = json.JSONDecoder()
    slugs: list[str] = []
    pos = payload.find(opening)
    while pos != -1:
        try:
            post, _end = decoder.raw_decode(payload, pos)
        except json.JSONDecodeError:
            pass
        else:
            slug = (post.get("slug") or {}).get("current")
            if isinstance(slug, str) and slug and "/" not in slug:
                slugs.append(slug)
        pos = payload.find(opening, pos + 1)
    href = re.compile(rf'href="(?:{re.escape(ANTHROPIC_URL)})?/{directory}/([^/?#"]+)/?"')
    slugs += href.findall(html_text)
    return [(slug, f"{ANTHROPIC_URL}/{directory}/{slug}") for slug in dict.fromkeys(slugs)]


def discover(backfill: bool = False) -> list[tuple[str, str]]:
    """Return [(slug, url), ...] for every post the indexes list, deduped.

    The claude.com listing comes first, so a post it shares with another
    index keeps the listing's URL. Only that listing paginates: page 1 unless
    `backfill`. The others list every post on one page. Any index failing
    raises, like a listing failure, since a partial view can't be trusted.
    """
    found: dict[str, str] = {}
    sources = [
        lambda: scrape_all_pages() if backfill else list_slugs(),
        list_claude_dev,
        *(lambda d=d: list_anthropic(d) for d in ANTHROPIC_INDEXES),
    ]
    for i, source in enumerate(sources):
        if i:
            time.sleep(REQUEST_DELAY)
        for slug, url in source():
            found.setdefault(slug, url)
    return list(found.items())


def fetch_post(slug: str, url: str | None = None) -> Post | None:
    """Fetch and parse a single post. Returns None on failure.

    `url` defaults to the on-site article. The parser is chosen from the URL
    after redirects, so a stored claude.com/blog URL lands on the right one, and
    the post's stored `url` is that final URL.
    """
    url = url or f"{BASE_URL}/{slug}"
    try:
        resp = get_session().get(url, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.error("Failed to fetch %s: %s", url, exc)
        return None

    final_url = resp.url
    if final_url.startswith(SKIPPED_URL_PREFIXES):
        logger.info("Skipping %s: it redirects to %s, which isn't a post", url, final_url)
        return None
    parser = _parser_for(final_url)
    if parser is None:
        logger.error("No parser for %s (from %s)", final_url, url)
        return None
    # Bytes, not resp.text: claude.dev sends no charset header, and requests
    # then assumes Latin-1. Given bytes, the parser honors <meta charset>.
    post = parser(BeautifulSoup(resp.content, "lxml"), final_url)
    post["slug"] = slug
    for field in ("html_body", "pub_date"):
        if not post[field]:
            logger.warning("No %s for %s; has the markup changed?", field, final_url)
    return post


def _parser_for(url: str):
    parts = urlsplit(url)
    host = parts.netloc.removeprefix("www.")
    if host == "claude.com" and parts.path.startswith("/resources/articles/"):
        return _parse_claude_article
    if host == "claude.dev" and parts.path.startswith("/blog/"):
        return _parse_claude_dev
    if host == "anthropic.com":
        return _parse_anthropic
    return None


def _parse_claude_article(soup: BeautifulSoup, url: str) -> Post:
    """A claude.com/resources/articles page."""
    title_el = soup.find("h1")
    date_str = _extract_detail(soup, "Date")
    pub_date = _parse_date(date_str)
    json_ld = _extract_json_ld(soup)
    return {
        "slug": "",
        "url": url,
        "title": title_el.get_text(strip=True) if title_el else "",
        "date_str": date_str or "",
        "pub_date": pub_date.isoformat() if pub_date else "",
        "categories": _extract_detail_list(soup, "Category", "Categories"),
        "authors": _extract_authors(soup, json_ld),
        "summary": _extract_summary(json_ld),
        "image_url": _extract_image(soup, json_ld),
        "html_body": _extract_body_html(soup),
    }


def _parse_claude_dev(soup: BeautifulSoup, url: str) -> Post:
    """A claude.dev/blog post. The body is `div#body`; the category is a chip above the h1."""
    json_ld = _extract_json_ld(soup)
    body = soup.find("div", id="body")
    chip = soup.find(class_="cat-chip")
    return _external_post(
        soup,
        url,
        json_ld,
        categories=[chip.get_text(strip=True)] if chip and chip.get_text(strip=True) else [],
        bodies=[body] if body else [],
    )


_ANTHROPIC_BODY = re.compile(r"^Body-module-scss-module__\w+__body$")
_ANTHROPIC_FOOTNOTES = re.compile(r"^MooFootnotes-module-scss-module__\w+__footnotes$")
_ANTHROPIC_SUBJECTS = re.compile(r"^PostDetail-module-scss-module__\w+__subjects$")


def _parse_anthropic(soup: BeautifulSoup, url: str) -> Post:
    """An anthropic.com news, engineering, or model-launch page.

    All three put the body in a `Body` CSS-module div inside <article>; launch
    pages add a footnotes section after it. Testimonial cards nest their own
    `Body` divs, so only outermost ones are taken.
    """
    root = soup.find("article") or soup
    bodies = [
        el
        for el in root.find_all(class_=_ANTHROPIC_BODY)
        if not el.find_parent(class_=_ANTHROPIC_BODY)
    ]
    bodies += root.find_all(class_=_ANTHROPIC_FOOTNOTES)
    subjects = soup.find(class_=_ANTHROPIC_SUBJECTS)
    categories = (
        [t for t in (el.get_text(strip=True) for el in subjects.find_all("span")) if t]
        if subjects
        else []
    )
    # Some launch pages show the date only as hero text, with no <time>.
    eyebrow = soup.find("p", class_="eyebrow")
    return _external_post(
        soup,
        url,
        _extract_json_ld(soup),
        categories=categories,
        bodies=bodies,
        date_text=eyebrow.get_text(strip=True) if eyebrow else "",
    )


def _external_post(
    soup: BeautifulSoup,
    url: str,
    json_ld: dict,
    *,
    categories: list[str],
    bodies: list,
    date_text: str = "",
) -> Post:
    """Fields every off-site host exposes the same way: JSON-LD and OpenGraph
    metadata, falling back to <time>, then `date_text`, and <h1>.

    A page that publishes a real time of day (anthropic.com news does) gets it
    as a precise pub_date. Midnight UTC is treated as date-only.
    """
    title = (
        html.unescape(json_ld.get("headline") or "").strip()
        or _meta(soup, "og:title")
        or (soup.find("h1").get_text(" ", strip=True) if soup.find("h1") else "")
    )
    time_el = soup.find("time", datetime=True)
    published = _parse_iso(
        json_ld.get("datePublished")
        or _meta(soup, "article:published_time")
        or (time_el["datetime"] if time_el else "")
    ) or _parse_date(date_text)
    post: Post = {
        "slug": "",
        "url": url,
        "title": title,
        "date_str": f"{published:%B} {published.day}, {published.year}" if published else "",
        "pub_date": published.isoformat() if published else "",
        "categories": categories,
        "authors": _extract_authors(soup, json_ld),
        "summary": _extract_summary(json_ld) or _meta(soup, "og:description"),
        "image_url": _extract_image(soup, json_ld),
        "html_body": "".join(_clean_external_body(b, url) for b in bodies),
    }
    if published and published != published.replace(hour=0, minute=0, second=0):
        post["pub_date_precise"] = True
    return post


def _meta(soup: BeautifulSoup, prop: str) -> str:
    el = soup.find("meta", attrs={"property": prop})
    return (el.get("content") or "").strip() if el else ""


def _parse_iso(value: str) -> datetime | None:
    """Parse an ISO date or datetime to UTC, whole seconds."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        logger.warning("Could not parse date: %r", value)
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).replace(microsecond=0)


_URL_ATTRS = ("href", "src", "poster")


def _clean_external_body(el, base_url: str) -> str:
    """Make an off-site body safe to lift into a feed: drop interactive
    controls, swap inline SVG figures for a link, and make relative URLs
    absolute (both hosts serve images and video from root-relative paths,
    which a feed reader would resolve against the wrong site).
    """
    for junk in el.find_all(["button", "script", "style", "noscript", "input"]):
        junk.decompose()
    _replace_svg_figures(el, base_url)
    for tag in el.find_all(True):
        for attr in _URL_ATTRS:
            if tag.get(attr) and not tag[attr].startswith("#"):
                tag[attr] = urljoin(base_url, tag[attr])
        if tag.get("srcset"):
            tag["srcset"] = ", ".join(
                " ".join([urljoin(base_url, part.split()[0]), *part.split()[1:]])
                for part in tag["srcset"].split(", ")
                if part.strip()
            )
    return str(el)


def _replace_svg_figures(el, base_url: str) -> None:
    """Replace each inline SVG chart or diagram with a link to the original.

    Feed readers sanitize SVG away (Miniflux drops the tags and keeps their
    text, leaving stray axis labels), and these charts are styled by the
    site's stylesheet, so they wouldn't render standalone anyway. A chart's
    drawing is the SVG itself, or the `role="img"` wrapper claude.dev puts
    around it; that wrapper's aria-label is a prose description of the chart,
    which is kept. Any other SVG (icons, avatars, anything hidden from screen
    readers without such a wrapper) is decoration and just removed.
    """
    host = urlsplit(base_url).netloc.removeprefix("www.")
    for svg in el.find_all("svg"):
        if svg.decomposed or el not in svg.parents:
            continue  # inside a drawing already replaced
        drawing = svg.find_parent(attrs={"role": "img", "aria-label": True})
        if drawing is None and (
            svg.get("aria-hidden") == "true" or svg.find_parent(attrs={"aria-hidden": "true"})
        ):
            svg.decompose()
            continue
        drawing = drawing or svg
        placeholder = _FRAGMENT.new_tag("p")
        if drawing is not svg:
            description = _FRAGMENT.new_tag("em")
            description.string = drawing["aria-label"].strip()
            placeholder.append(description)
            placeholder.append(" ")
        link = _FRAGMENT.new_tag("a", href=base_url)
        link.string = f"View this chart on {host}"
        placeholder.append(link)
        drawing.replace_with(placeholder)


_FRAGMENT = BeautifulSoup("", "lxml")


def _extract_body_html(soup: BeautifulSoup) -> str:
    """Concatenate a post's article body.

    The rich-text body is a `text-rich-text--article` div. Posts seen so far
    have exactly one, but take every one in document order: the old Webflow
    site split long posts into several, and grabbing only the first silently
    truncated them.
    """
    return "".join(str(div) for div in soup.find_all("div", class_="text-rich-text--article"))


def _module_class(component: str, name: str) -> re.Pattern[str]:
    """Match a CSS-module class token like `DetailSidebar-module-scss-module__3RcHtG__label`.

    The middle segment is a build hash that changes on redeploys, so match
    around it rather than on the whole token.
    """
    return re.compile(rf"^{component}-module-scss-module__\w+__{name}$")


_SIDEBAR_LABEL = _module_class("DetailSidebar", "label")
_SIDEBAR_VALUE = _module_class("DetailSidebar", "value")


def _extract_detail(soup: BeautifulSoup, *labels: str) -> str:
    """Pull the text of the post sidebar item with one of the given labels."""
    for label_el in soup.find_all(class_=_SIDEBAR_LABEL):
        if label_el.get_text(strip=True) in labels:
            value_el = label_el.find_next_sibling(class_=_SIDEBAR_VALUE)
            return value_el.get_text(strip=True) if value_el else ""
    return ""


def _extract_detail_list(soup: BeautifulSoup, *labels: str) -> list[str]:
    """Pull a multi-valued sidebar item: its links' texts, else its comma-separated text."""
    for label_el in soup.find_all(class_=_SIDEBAR_LABEL):
        if label_el.get_text(strip=True) not in labels:
            continue
        value_el = label_el.find_next_sibling(class_=_SIDEBAR_VALUE)
        if value_el is None:
            return []
        links = value_el.find_all("a")
        texts = (
            [a.get_text(strip=True) for a in links]
            if links
            else value_el.get_text(strip=True).split(",")
        )
        return [t.strip() for t in texts if t.strip()]
    return []


def _extract_authors(soup: BeautifulSoup, json_ld: dict) -> list[str]:
    """Person names from JSON-LD, falling back to the sidebar's Author(s) item.

    anthropic.com credits the Organization on every post; that isn't a byline.
    """
    raw = json_ld.get("author") or []
    if isinstance(raw, dict):
        raw = [raw]
    names = [
        html.unescape(a.get("name") or "").strip()
        for a in raw
        if isinstance(a, dict) and a.get("@type") != "Organization"
    ]
    names = [n for n in names if n]
    return names or _extract_detail_list(soup, "Author", "Authors")


_ARTICLE_LD_TYPES = {"BlogPosting", "NewsArticle", "Article"}


def _extract_json_ld(soup: BeautifulSoup) -> dict:
    """Return the article JSON-LD block for a post, or {} if absent/invalid.

    It may be a top-level object or an entry in an @graph.
    """
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except json.JSONDecodeError, TypeError:
            continue
        if not isinstance(data, dict):
            continue
        for item in data.get("@graph") or [data]:
            if isinstance(item, dict) and item.get("@type") in _ARTICLE_LD_TYPES:
                return item
    return {}


def _extract_summary(json_ld: dict) -> str:
    """Short teaser for RSS <description>, from JSON-LD (HTML-entity-decoded)."""
    return html.unescape(json_ld.get("description") or "").strip()


def _extract_image(soup: BeautifulSoup, json_ld: dict) -> str:
    """Per-post image: last non-empty og:image, falling back to JSON-LD image.

    The old Webflow site emitted an empty og:image placeholder before the real
    one, so the last non-empty value is the one to use.
    """
    og_images = [
        (m.get("content") or "").strip()
        for m in soup.find_all("meta", attrs={"property": "og:image"})
    ]
    og_images = [u for u in og_images if u]
    if og_images:
        return og_images[-1]
    return (json_ld.get("image") or "").strip()


_DATE_FORMATS = ["%B %d, %Y", "%b %d, %Y"]


def _parse_date(date_str: str) -> datetime | None:
    if not date_str:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(date_str.strip(), fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    logger.warning("Could not parse date: %r", date_str)
    return None


def _parse_sitemap(xml_bytes: bytes) -> dict[str, datetime]:
    """Return {slug: lastmod} for direct article entries in a sitemap.

    Webflow bumped a page's <lastmod> on each CMS publish, which gave genuine
    time-of-day precision (unlike the dynamically-rendered Last-Modified HTTP
    header on claude.com, which just reflects when that particular response was
    cache-filled). Since the move to /resources/articles the sitemap lists
    articles without <lastmod>, so this currently finds nothing; it is kept so
    precise times come back if the site starts emitting them again.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        logger.error("Failed to parse sitemap: %s", exc)
        return {}

    prefix = f"{BASE_URL}/"
    lastmods: dict[str, datetime] = {}
    for url_el in root.findall("sm:url", _SITEMAP_NS):
        loc_el = url_el.find("sm:loc", _SITEMAP_NS)
        lastmod_el = url_el.find("sm:lastmod", _SITEMAP_NS)
        if loc_el is None or lastmod_el is None:
            continue
        loc = (loc_el.text or "").strip()
        if not loc.startswith(prefix):
            continue
        slug = loc[len(prefix) :]
        if not slug or "/" in slug:
            continue
        try:
            lastmods[slug] = datetime.fromisoformat(
                (lastmod_el.text or "").strip().replace("Z", "+00:00")
            )
        except ValueError:
            continue
    return lastmods


def fetch_sitemap_lastmods() -> dict[str, datetime]:
    """Fetch and parse claude.com/sitemap.xml. Returns {} on failure."""
    try:
        resp = get_session().get(SITEMAP_URL, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.error("Failed to fetch sitemap: %s", exc)
        return {}
    return _parse_sitemap(resp.content)


def scrape_all_pages(max_pages: int = 40, delay: float = REQUEST_DELAY) -> list[tuple[str, str]]:
    """Walk listing pages and return unique (slug, url) pairs.

    `?page=N` is cumulative ("load more"): it renders every card up to page N,
    and past the end it keeps rendering the full list. So stop at the first
    page that adds nothing new rather than waiting for an empty one.
    """
    all_slugs: dict[str, str] = {}
    for page in range(1, max_pages + 1):
        logger.info("Fetching listing page %d", page)
        before = len(all_slugs)
        for slug, url in list_slugs(page):
            all_slugs.setdefault(slug, url)
        if len(all_slugs) == before:
            logger.info("Page %d added no new slugs, stopping.", page)
            break
        if page < max_pages:
            time.sleep(delay)
    return list(all_slugs.items())


def fetch_text(url: str, timeout: int = 30) -> str | None:
    """Fetch a URL as text, returning None on any failure.

    Used to recover published state from GitHub Pages when the Actions cache
    misses. A failure here is recoverable (the caller falls back to a
    re-scrape), so unlike a listing-page failure it must not crash the run.
    """
    try:
        resp = get_session().get(url, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Could not fetch %s: %s", url, exc)
        return None
    return resp.text
