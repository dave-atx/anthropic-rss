"""Scrape post listings and post content from claude.com/resources/articles.

The blog lived at claude.com/blog (Webflow) until October 2026, when it moved
to claude.com/resources/articles (Next.js). Old /blog/<slug> URLs 308-redirect
to the same slug under the new path, so slugs, and the tag: ids built from
them, carry over unchanged.
"""

import html
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime

import requests
from bs4 import BeautifulSoup

from .models import Post

BASE_URL = "https://claude.com/resources/articles"
SITEMAP_URL = "https://claude.com/sitemap.xml"
PAGINATION_PARAM = "page"
REQUEST_DELAY = 1.0
USER_AGENT = "claude-blog-rss/1.0 (+https://github.com/dave-atx/anthropic-rss)"

_SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

# A listing card's link to an on-site article. Cards for cross-posts link off
# site (claude.dev/blog, anthropic.com/news) and are deliberately not matched.
_ARTICLE_HREF = re.compile(r"^(?:https://claude\.com)?/resources/articles/([^/?#]+)/?$")

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
    """Return [(slug, title), ...] from the listing page, deduped, in page order.

    Raises if the page has no article links at all: the listing always has
    some, so an empty result means the markup or URL scheme moved and the run
    can't be trusted.
    """
    url = BASE_URL if page == 1 else f"{BASE_URL}?{PAGINATION_PARAM}={page}"
    resp = get_session().get(url, timeout=30)
    resp.raise_for_status()
    results = parse_listing(resp.text)
    if not results:
        raise RuntimeError(f"{url} yielded no article slugs; has the markup changed?")
    return results


def parse_listing(html_text: str) -> list[tuple[str, str]]:
    """Return [(slug, title), ...] for every article link in a listing page.

    The same article can appear twice (featured carousel and grid); the first
    occurrence wins.
    """
    soup = BeautifulSoup(html_text, "lxml")
    seen: set[str] = set()
    results: list[tuple[str, str]] = []
    for link in soup.find_all("a", href=_ARTICLE_HREF):
        slug = _ARTICLE_HREF.match(link["href"]).group(1)
        if slug in seen:
            continue
        seen.add(slug)
        title_el = link.find("h3")
        results.append((slug, title_el.get_text(strip=True) if title_el else ""))
    return results


def fetch_post(slug: str) -> Post | None:
    """Fetch and parse a single post. Returns None on failure."""
    url = f"{BASE_URL}/{slug}"
    try:
        resp = get_session().get(url, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.error("Failed to fetch %s: %s", url, exc)
        return None

    soup = BeautifulSoup(resp.text, "lxml")

    title_el = soup.find("h1")
    title = title_el.get_text(strip=True) if title_el else slug

    date_str = _extract_detail(soup, "Date")
    pub_date = _parse_date(date_str)

    json_ld = _extract_json_ld(soup)
    categories = _extract_detail_list(soup, "Category", "Categories")
    authors = _extract_authors(soup, json_ld)
    summary = _extract_summary(json_ld)
    image_url = _extract_image(soup, json_ld)

    html_body = _extract_body_html(soup)

    return {
        "slug": slug,
        "url": url,
        "title": title,
        "date_str": date_str or "",
        "pub_date": pub_date.isoformat() if pub_date else "",
        "categories": categories,
        "authors": authors,
        "summary": summary,
        "image_url": image_url,
        "html_body": html_body,
    }


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
    """Author names from JSON-LD, falling back to the sidebar's Author(s) item."""
    raw = json_ld.get("author") or []
    if isinstance(raw, dict):
        raw = [raw]
    names = [html.unescape(a.get("name") or "").strip() for a in raw if isinstance(a, dict)]
    names = [n for n in names if n]
    return names or _extract_detail_list(soup, "Author", "Authors")


def _extract_json_ld(soup: BeautifulSoup) -> dict:
    """Return the BlogPosting JSON-LD block for a post, or {} if absent/invalid."""
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except json.JSONDecodeError, TypeError:
            continue
        if isinstance(data, dict) and data.get("@type") == "BlogPosting":
            return data
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
    """Walk listing pages and return unique (slug, title) pairs.

    `?page=N` is cumulative ("load more"): it renders every card up to page N,
    and past the end it keeps rendering the full list. So stop at the first
    page that adds nothing new rather than waiting for an empty one.
    """
    all_slugs: dict[str, str] = {}
    for page in range(1, max_pages + 1):
        logger.info("Fetching listing page %d", page)
        before = len(all_slugs)
        for slug, title in list_slugs(page):
            all_slugs.setdefault(slug, title)
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
