"""Tests for scrape.py using local HTML fixtures (no network)."""

from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from claude_blog_rss.scrape import (
    _extract_authors,
    _extract_body_html,
    _extract_detail,
    _extract_detail_list,
    _extract_image,
    _extract_json_ld,
    _extract_summary,
    _parse_date,
    _parse_sitemap,
    parse_listing,
)

FIXTURES = Path(__file__).parent / "fixtures"


def listing_html() -> str:
    return (FIXTURES / "articles_listing.html").read_text()


def post_html() -> str:
    """Post with no byline and a single category."""
    return (FIXTURES / "post_example.html").read_text()


def post_with_author_html() -> str:
    """Post with a multi-author byline and two categories."""
    return (FIXTURES / "post_example_with_author.html").read_text()


# ── listing page ──────────────────────────────────────────────────────────────


def test_listing_finds_on_site_articles_in_page_order():
    slugs = [slug for slug, _title in parse_listing(listing_html())]
    assert slugs == [
        "dashboards-and-motion",
        "how-block-orchestrates-claude-fable-across-thousands-of-pull-requests",
        "how-comcast-booz-allen-use-claude-mythos-to-secure-their-codebases",
        "claude-now-works-in-google-docs-sheets-and-slides",
        "were-expanding-the-claude-startups-program-to-help-founders-build",
        "how-cresta-turned-cx-expertise-into-an-agent-builder-on-the-claude-agent-sdk",
    ]


def test_listing_extracts_card_titles():
    titles = dict(parse_listing(listing_html()))
    assert titles["claude-now-works-in-google-docs-sheets-and-slides"] == (
        "Claude now works with Google Docs, Sheets, and Slides"
    )


def test_listing_ignores_off_site_index_and_nested_links():
    html = """
        <a href="/resources/articles">All articles</a>
        <a href="https://claude.dev/blog/cross-post/">x</a>
        <a href="/resources/articles/real-post"><h3>Real</h3></a>
        <a href="/resources/articles/real-post">dup</a>
        <a href="https://claude.com/resources/articles/absolute-post/">x</a>
        <a href="/resources/articles/category/news">x</a>
        <a href="/ja/resources/articles/localized">x</a>
    """
    assert parse_listing(html) == [("real-post", "Real"), ("absolute-post", "")]


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
    assert _extract_detail_list(soup, "Category", "Categories") == ["Announcements"]


def test_post_extracts_multiple_categories():
    soup = BeautifulSoup(post_with_author_html(), "lxml")
    cats = _extract_detail_list(soup, "Category", "Categories")
    assert cats == ["Best practices", "Perspectives"]


def test_post_extracts_body_html():
    soup = BeautifulSoup(post_html(), "lxml")
    html_body = _extract_body_html(soup)
    assert html_body.startswith('<div class="text-rich-text text-rich-text--article')
    assert "Starting today" in html_body
    assert "Related articles" not in html_body


def test_post_extracts_body_html_across_multiple_divs():
    """If a body is ever split into several rich-text divs, all of them must
    be captured, in document order, or the article is silently truncated."""
    html = """
    <div class="text-rich-text text-rich-text--article"><p>first half</p></div>
    <figure>embedded component</figure>
    <div class="text-rich-text text-rich-text--article"><p>second half</p></div>
    """
    soup = BeautifulSoup(html, "lxml")
    html_body = _extract_body_html(soup)
    assert "first half" in html_body
    assert "second half" in html_body
    assert "embedded component" not in html_body
    assert html_body.index("first half") < html_body.index("second half")


# ── authors ──────────────────────────────────────────────────────────────────


def test_post_without_author_returns_empty_list():
    soup = BeautifulSoup(post_html(), "lxml")
    assert _extract_authors(soup, _extract_json_ld(soup)) == []


def test_post_with_author_extracts_names():
    soup = BeautifulSoup(post_with_author_html(), "lxml")
    authors = _extract_authors(soup, _extract_json_ld(soup))
    assert authors == ["Jonah Ezekiel", "Lexie Tonelli"]


def test_authors_fall_back_to_sidebar_without_json_ld():
    soup = BeautifulSoup(post_with_author_html(), "lxml")
    assert _extract_authors(soup, {}) == ["Jonah Ezekiel", "Lexie Tonelli"]


# ── JSON-LD summary ──────────────────────────────────────────────────────────


def test_extract_summary_unescapes_html_entities():
    soup = BeautifulSoup(post_html(), "lxml")
    summary = _extract_summary(_extract_json_ld(soup))
    assert summary, "Expected a non-empty summary"
    assert "&#x27;" not in summary and "&#39;" not in summary


def test_extract_summary_missing_json_ld_returns_empty():
    assert _extract_summary({}) == ""


# ── per-post image ───────────────────────────────────────────────────────────


def test_post_image_is_per_post_og_card():
    soup = BeautifulSoup(post_html(), "lxml")
    image = _extract_image(soup, _extract_json_ld(soup))
    assert image == "https://claude.com/resources/articles/dashboards-and-motion/opengraph-image/og"


def test_post_image_skips_empty_og_placeholder():
    soup = BeautifulSoup(
        '<meta property="og:image" content=""><meta property="og:image" content="https://x/y.png">',
        "lxml",
    )
    assert _extract_image(soup, {}) == "https://x/y.png"


# ── date parser ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "date_str,year",
    [
        ("April 14, 2026", 2026),
        ("Apr 20, 2026", 2026),
        ("January 1, 2024", 2024),
    ],
)
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


def test_parse_sitemap_extracts_article_slugs():
    lastmods = _parse_sitemap(sitemap_xml())
    assert set(lastmods) == {"same-day-post", "edited-later-post"}


def test_parse_sitemap_ignores_non_article_and_localized_urls():
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
