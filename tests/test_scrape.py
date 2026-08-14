"""Tests for scrape.py using local HTML fixtures (no network)."""

from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from src.scrape import (
    _extract_detail,
    _extract_detail_list,
    _extract_image,
    _extract_json_ld,
    _extract_summary,
    _parse_date,
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
    body_div = soup.find("div", class_="blog_post_content_wrap")
    assert body_div is not None, "blog_post_content_wrap not found"
    text = body_div.get_text()
    assert len(text) > 200, "Article body seems too short"


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
