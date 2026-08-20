"""Scrape post listings and post content from claude.com/blog."""

import html
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://claude.com/blog"
SITEMAP_URL = "https://claude.com/sitemap.xml"
PAGINATION_PARAM = "d7430fcd_page"
REQUEST_DELAY = 1.0
USER_AGENT = "claude-blog-rss/1.0 (+https://github.com/dave-atx/anthropic-rss)"

_SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

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
    """Return [(slug, title), ...] from the listing page, deduped."""
    url = BASE_URL if page == 1 else f"{BASE_URL}?{PAGINATION_PARAM}={page}"
    resp = get_session().get(url, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    seen: set[str] = set()
    results: list[tuple[str, str]] = []

    for card in soup.find_all(
        "div", attrs={"role": "listitem", "class": re.compile(r"\bblog_cms_item\b")}
    ):
        link = card.find(
            "a", attrs={"data-cta": "Blog page", "href": re.compile(r"^/blog/[^/]+$")}
        )
        if link is None:
            continue
        slug = link["href"].lstrip("/blog/")
        slug = link["href"][len("/blog/") :]
        title = (link.get("data-cta-copy") or "").strip()
        if slug and slug not in seen:
            seen.add(slug)
            results.append((slug, title))

    return results


def fetch_post(slug: str) -> dict | None:
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

    categories = _extract_detail_list(soup, "Category")
    authors = _extract_detail_list(soup, "Author(s)")

    json_ld = _extract_json_ld(soup)
    summary = _extract_summary(json_ld)
    image_url = _extract_image(soup, json_ld)

    body_div = soup.find("div", class_="blog_post_content_wrap")
    if body_div:
        rich = body_div.find("div", class_=re.compile(r"\bu-rich-text-blog\b"))
        html_body = str(rich) if rich else str(body_div)
    else:
        html_body = ""

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


def _extract_detail(soup: BeautifulSoup, label: str) -> str:
    """Pull the value text from a hero_blog_post_details_item with a given label."""
    for item in soup.find_all(
        "li", class_=re.compile(r"\bhero_blog_post_details_item\b")
    ):
        label_el = item.find(class_=re.compile(r"\bu-foreground-tertiary\b"))
        if label_el and label_el.get_text(strip=True) == label:
            # Value is the next sibling div / link text
            value_els = item.find_all(class_=re.compile(r"\bu-text-style-body-3\b"))
            texts = [
                el.get_text(strip=True) for el in value_els if el.get_text(strip=True)
            ]
            if texts:
                return texts[0]
    return ""


def _extract_detail_list(soup: BeautifulSoup, label: str) -> list[str]:
    """Pull all value texts (e.g. multiple categories) from a details item."""
    for item in soup.find_all(
        "li", class_=re.compile(r"\bhero_blog_post_details_item\b")
    ):
        label_el = item.find(class_=re.compile(r"\bu-foreground-tertiary\b"))
        if label_el and label_el.get_text(strip=True) == label:
            texts = [
                el.get_text(strip=True)
                for el in item.find_all(
                    ["a", "div"], class_=re.compile(r"\bu-text-style-body-3\b")
                )
            ]
            return [t for t in texts if t]
    return []


def _extract_json_ld(soup: BeautifulSoup) -> dict:
    """Return the BlogPosting JSON-LD block for a post, or {} if absent/invalid."""
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict) and data.get("@type") == "BlogPosting":
            return data
    return {}


def _extract_summary(json_ld: dict) -> str:
    """Short teaser for RSS <description>, from JSON-LD (HTML-entity-decoded)."""
    return html.unescape(json_ld.get("description") or "").strip()


def _extract_image(soup: BeautifulSoup, json_ld: dict) -> str:
    """Per-post image: last non-empty og:image, falling back to JSON-LD image.

    claude.com/blog emits an empty og:image placeholder followed by a real one
    (either a per-post card or the site's generic fallback), so the last
    non-empty value is always the right one to use.
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
            return datetime.strptime(date_str.strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    logger.warning("Could not parse date: %r", date_str)
    return None


def _parse_sitemap(xml_bytes: bytes) -> dict[str, datetime]:
    """Return {slug: lastmod} for direct /blog/<slug> entries in a sitemap.

    Webflow bumps a page's <lastmod> on each CMS publish of that page, which
    gives genuine time-of-day precision (unlike the dynamically-rendered
    Last-Modified HTTP header on claude.com, which just reflects when that
    particular response was cache-filled, not the page's real publish/edit
    time).
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


def scrape_all_pages(
    max_pages: int = 20, delay: float = REQUEST_DELAY
) -> list[tuple[str, str]]:
    """Walk all listing pages and return unique (slug, title) pairs."""
    all_slugs: dict[str, str] = {}
    for page in range(1, max_pages + 1):
        logger.info("Fetching listing page %d", page)
        slugs = list_slugs(page)
        if not slugs:
            logger.info("No items on page %d, stopping.", page)
            break
        for slug, title in slugs:
            all_slugs.setdefault(slug, title)
        if page < max_pages:
            time.sleep(delay)
    return list(all_slugs.items())
