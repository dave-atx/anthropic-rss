"""Tests for scrape.py using local HTML fixtures (no network)."""

from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from claude_blog_rss.scrape import (
    _extract_body_html,
    _extract_detail,
    _extract_detail_list,
    _extract_image,
    _extract_json_ld,
    _extract_summary,
    _parse_date,
    _parse_sitemap,
)

FIXTURES = Path(__file__).parent / "fixtures"


def listing_html() -> str:
    return (FIXTURES / "blog_listing.html").read_text()


def post_html() -> str:
    """Post with no byline and no per-post social image (generic fallback only)."""
    return (FIXTURES / "post_example.html").read_text()


def post_with_author_html() -> str:
    """Post with a multi-author byline and a real per-post social image."""
    return (FIXTURES / "post_example_with_author.html").read_text()


# ── listing page ──────────────────────────────────────────────────────────────

def test_listing_finds_slugs():
    """At least 10 unique slugs on the listing page."""
    import re
    html = listing_html()
    soup = BeautifulSoup(html, "lxml")
    links = soup.find_all(
        "a",
        attrs={"data-cta": "Blog page", "href": re.compile(r"^/blog/[^/]+$")},
    )
    seen = {l["href"][len("/blog/"):] for l in links}
    assert len(seen) >= 10, f"Expected >=10 slugs, got {len(seen)}"


def test_listing_unique_slugs_at_least_10():
    """Listing page has at least 10 *unique* slugs (marquee+grid may repeat the same slug)."""
    import re
    html = listing_html()
    soup = BeautifulSoup(html, "lxml")
    links = soup.find_all(
        "a",
        attrs={"data-cta": "Blog page", "href": re.compile(r"^/blog/[^/]+$")},
    )
    unique = {l["href"][len("/blog/"):] for l in links}
    assert len(unique) >= 10, f"Expected >=10 unique slugs, got {len(unique)}"


# ── post detail page ──────────────────────────────────────────────────────────

def test_post_extracts_title():
    soup = BeautifulSoup(post_html(), "lxml")
    h1 = soup.find("h1")
    assert h1 is not None
    assert len(h1.get_text(strip=True)) > 5


def test_post_extracts_date():
    soup = BeautifulSoup(post_html(), "lxml")
    date_str = _extract_detail(soup, "Date")
    assert date_str, "Date not found"
    dt = _parse_date(date_str)
    assert dt is not None, f"Could not parse date: {date_str!r}"
    assert dt.year >= 2024


def test_post_extracts_categories():
    soup = BeautifulSoup(post_html(), "lxml")
    cats = _extract_detail_list(soup, "Category")
    assert len(cats) >= 1, "Expected at least one category"


def test_post_extracts_body_html():
    soup = BeautifulSoup(post_html(), "lxml")
    html_body = _extract_body_html(soup)
    assert len(html_body) > 200, "Article body seems too short"


def test_post_extracts_body_html_across_multiple_wraps():
    """Long posts render the article body as multiple sibling
    blog_post_content_wrap divs (Webflow splits the rich-text CMS field
    around embedded components like testimonials). Both must be captured,
    in document order, or the article is silently truncated."""
    html = """
    <div class="blog_post_content_wrap">
      <div class="u-rich-text-blog"><p>first half</p></div>
    </div>
    <div class="blog_post_content_wrap">
      <div class="w-condition-invisible">
        <div class="u-rich-text-blog"></div>
      </div>
      <div class="u-rich-text-blog"><p>second half</p></div>
    </div>
    """
    soup = BeautifulSoup(html, "lxml")
    html_body = _extract_body_html(soup)
    assert "first half" in html_body
    assert "second half" in html_body
    assert html_body.index("first half") < html_body.index("second half")


# ── authors ──────────────────────────────────────────────────────────────────

def test_post_without_author_returns_empty_list():
    soup = BeautifulSoup(post_html(), "lxml")
    assert _extract_detail_list(soup, "Author(s)") == []


def test_post_with_author_extracts_names():
    soup = BeautifulSoup(post_with_author_html(), "lxml")
    authors = _extract_detail_list(soup, "Author(s)")
    assert authors == ["Clement Peng", "Lily Zhao"]


# ── JSON-LD summary ──────────────────────────────────────────────────────────

def test_extract_summary_unescapes_html_entities():
    soup = BeautifulSoup(post_html(), "lxml")
    summary = _extract_summary(_extract_json_ld(soup))
    assert summary, "Expected a non-empty summary"
    assert "&#x27;" not in summary and "&#39;" not in summary


def test_extract_summary_missing_json_ld_returns_empty():
    assert _extract_summary({}) == ""


# ── per-post image ───────────────────────────────────────────────────────────

def test_post_without_custom_image_falls_back_to_generic():
    """No per-post og:image card → last non-empty og:image is the site's
    generic fallback, per Dave's decision to show it rather than nothing."""
    soup = BeautifulSoup(post_html(), "lxml")
    image = _extract_image(soup, _extract_json_ld(soup))
    assert image.startswith("https://")
    assert "generic" in image


def test_post_with_custom_image_uses_per_post_card():
    soup = BeautifulSoup(post_with_author_html(), "lxml")
    image = _extract_image(soup, _extract_json_ld(soup))
    assert image.startswith("https://")
    assert "generic" not in image


# ── date parser ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("date_str,year", [
    ("April 14, 2026", 2026),
    ("Apr 20, 2026", 2026),
    ("January 1, 2024", 2024),
])
def test_parse_date_formats(date_str, year):
    dt = _parse_date(date_str)
    assert dt is not None
    assert dt.year == year


def test_parse_date_invalid():
    assert _parse_date("") is None
    assert _parse_date("not a date") is None


# ── sitemap ──────────────────────────────────────────────────────────────────

def sitemap_xml() -> bytes:
    return (FIXTURES / "sitemap.xml").read_bytes()


def test_parse_sitemap_extracts_blog_slugs():
    lastmods = _parse_sitemap(sitemap_xml())
    assert set(lastmods) == {"same-day-post", "edited-later-post"}


def test_parse_sitemap_ignores_non_blog_and_localized_urls():
    lastmods = _parse_sitemap(sitemap_xml())
    assert "ja" not in lastmods


def test_parse_sitemap_ignores_entries_without_lastmod():
    lastmods = _parse_sitemap(sitemap_xml())
    assert "no-lastmod-post" not in lastmods


def test_parse_sitemap_parses_timestamps():
    lastmods = _parse_sitemap(sitemap_xml())
    dt = lastmods["same-day-post"]
    assert dt.year == 2026 and dt.month == 8 and dt.day == 4
    assert dt.hour == 22 and dt.minute == 48


def test_parse_sitemap_handles_invalid_xml():
    assert _parse_sitemap(b"not xml") == {}
