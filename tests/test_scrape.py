"""Tests for scrape.py using local HTML fixtures (no network)."""

from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from claude_blog_rss.scrape import (
    _clean_external_body,
    _extract_authors,
    _extract_body_html,
    _extract_detail,
    _extract_detail_list,
    _extract_image,
    _extract_json_ld,
    _extract_summary,
    _parse_anthropic,
    _parse_claude_article,
    _parse_claude_dev,
    _parse_date,
    _parse_sitemap,
    _parser_for,
    parse_listing,
)

FIXTURES = Path(__file__).parent / "fixtures"
ARTICLES = "https://claude.com/resources/articles"


def fixture_soup(name: str) -> BeautifulSoup:
    return BeautifulSoup((FIXTURES / name).read_text(), "lxml")


def listing_html() -> str:
    return (FIXTURES / "articles_listing.html").read_text()


def post_html() -> str:
    """Post with no byline and a single category."""
    return (FIXTURES / "post_example.html").read_text()


def post_with_author_html() -> str:
    """Post with a multi-author byline and two categories."""
    return (FIXTURES / "post_example_with_author.html").read_text()


# ── listing page ──────────────────────────────────────────────────────────────


def test_listing_finds_posts_in_page_order():
    assert parse_listing(listing_html()) == [
        ("dashboards-and-motion", f"{ARTICLES}/dashboards-and-motion"),
        (
            "building-effective-agent-automations",
            "https://claude.dev/blog/building-effective-agent-automations/",
        ),
        (
            "how-block-orchestrates-claude-fable-across-thousands-of-pull-requests",
            f"{ARTICLES}/how-block-orchestrates-claude-fable-across-thousands-of-pull-requests",
        ),
        ("claude-haiku-5-5", "https://www.anthropic.com/claude-haiku-5-5"),
        (
            "automating-eval-design-and-hillclimbing",
            "https://claude.dev/blog/automating-eval-design-and-hillclimbing/",
        ),
        (
            "how-comcast-booz-allen-use-claude-mythos-to-secure-their-codebases",
            f"{ARTICLES}/how-comcast-booz-allen-use-claude-mythos-to-secure-their-codebases",
        ),
        (
            "claude-now-works-in-google-docs-sheets-and-slides",
            f"{ARTICLES}/claude-now-works-in-google-docs-sheets-and-slides",
        ),
        (
            "were-expanding-the-claude-startups-program-to-help-founders-build",
            f"{ARTICLES}/were-expanding-the-claude-startups-program-to-help-founders-build",
        ),
        (
            "cyber-verification-program",
            "https://www.anthropic.com/news/cyber-verification-program",
        ),
        ("claude-code-in-the-cloud", "https://claude.dev/blog/claude-code-in-the-cloud/"),
        (
            "how-cresta-turned-cx-expertise-into-an-agent-builder-on-the-claude-agent-sdk",
            f"{ARTICLES}/how-cresta-turned-cx-expertise-into-an-agent-builder-on-the-claude-agent-sdk",
        ),
    ]


CARD = 'class="ResourceCard-module-scss-module__q33Tuq__card"'


def test_listing_matches_off_site_posts_only_on_cards_and_known_hosts():
    html = f"""
        <a href="/resources/articles">All articles</a>
        <a href="https://www.anthropic.com/news/in-the-nav">x</a>
        <a {CARD} href="https://claude.dev/blog/cross-post/">x</a>
        <a {CARD} href="https://www.anthropic.com/engineering/eng-post">x</a>
        <a {CARD} href="https://example.com/blog/elsewhere">x</a>
        <a href="/resources/articles/real-post">x</a>
        <a {CARD} href="/resources/articles/real-post">dup</a>
        <a href="https://claude.com/resources/articles/absolute-post/">x</a>
        <a href="/resources/articles/category/news">x</a>
        <a href="/ja/resources/articles/localized">x</a>
    """
    assert [slug for slug, _url in parse_listing(html)] == [
        "cross-post",
        "eng-post",
        "real-post",
        "absolute-post",
    ]


@pytest.mark.parametrize(
    "url,parser",
    [
        (f"{ARTICLES}/x", _parse_claude_article),
        ("https://claude.dev/blog/x/", _parse_claude_dev),
        ("https://www.anthropic.com/news/x", _parse_anthropic),
        ("https://www.anthropic.com/claude-haiku-5-5", _parse_anthropic),
        ("https://claude.com/customers/x", None),
        ("https://claude.dev/docs/x", None),
    ],
)
def test_parser_is_chosen_by_final_url(url, parser):
    assert _parser_for(url) is parser


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


# ── off-site posts ────────────────────────────────────────────────────────────


def test_claude_dev_post():
    url = "https://claude.dev/blog/claude-code-in-the-cloud/"
    post = _parse_claude_dev(fixture_soup("claude_dev_post.html"), url)
    body = post.pop("html_body")
    assert post == {
        "slug": "",
        "url": url,
        "title": "Claude Code in the cloud: a field guide to cloud sessions",
        "date_str": "October 6, 2026",
        "pub_date": "2026-10-06T00:00:00+00:00",
        "categories": ["Playbooks"],
        "authors": ["Addy Osmani"],
        "summary": (
            "Cloud sessions run Claude Code on a fresh VM for each task. Four real sessions, "
            "seven workflows that suit them, and how to connect GitHub without getting stuck."
        ),
        "image_url": "https://claude.dev/blog/claude-code-in-the-cloud/og.png",
    }
    assert body.startswith('<div class="prose" id="body">')
    assert "You probably run Claude Code in a terminal" in body
    assert "<button" not in body
    assert 'src="https://claude.dev/media/' in body
    assert 'src="/' not in body and 'poster="/' not in body


def test_anthropic_news_post_has_a_precise_time_and_no_org_byline():
    url = "https://www.anthropic.com/news/cyber-verification-program"
    post = _parse_anthropic(fixture_soup("anthropic_news_post.html"), url)
    assert post["title"] == "Expanding the Cyber Verification Program"
    assert post["pub_date"] == "2026-10-06T19:00:00+00:00"
    assert post["pub_date_precise"] is True
    assert post["date_str"] == "October 6, 2026"
    assert post["categories"] == ["Announcements"]
    assert post["authors"] == []
    assert post["html_body"].count("launching a new, expanded version") == 1
    assert "Related content" not in post["html_body"]
    assert 'src="https://www.anthropic.com/_next/image?' in post["html_body"]


def test_anthropic_launch_page_takes_outer_body_and_footnotes():
    url = "https://www.anthropic.com/claude-haiku-5-5"
    post = _parse_anthropic(fixture_soup("anthropic_launch_page.html"), url)
    body = post["html_body"]
    assert post["title"] == "Introducing Claude Haiku 5.5"
    assert post["pub_date"] == "2026-10-07T00:00:00+00:00"
    assert "pub_date_precise" not in post
    # Testimonial cards nest their own Body divs; taking those too would repeat text.
    assert body.count("Introducing Claude Haiku 5.5: the cheapest") == 1
    assert "MooFootnotes" in body and body.index("Introducing") < body.index("MooFootnotes")
    assert "<button" not in body


def test_clean_external_body_absolutizes_urls():
    soup = BeautifulSoup(
        '<div><a href="#s1">x</a><a href="/docs">x</a><img src="/m.png" '
        'srcset="/a.png 1x, /b.png 2x"><video poster="/p.png"></video><button>Copy</button></div>',
        "lxml",
    )
    html = _clean_external_body(soup.div, "https://claude.dev/blog/post/")
    assert html == (
        '<div><a href="#s1">x</a><a href="https://claude.dev/docs">x</a>'
        '<img src="https://claude.dev/m.png" '
        'srcset="https://claude.dev/a.png 1x, https://claude.dev/b.png 2x"/>'
        '<video poster="https://claude.dev/p.png"></video></div>'
    )


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


def test_anthropic_page_falls_back_to_eyebrow_date():
    """Some launch pages have no JSON-LD, no published_time, and no <time>."""
    soup = BeautifulSoup(
        '<p class="eyebrow">September 28, 2026</p><article>'
        '<div class="Body-module-scss-module__z40yvW__body"><p>Hi</p></div></article>',
        "lxml",
    )
    post = _parse_anthropic(soup, "https://www.anthropic.com/claude-sonnet-5-5")
    assert post["pub_date"] == "2026-09-28T00:00:00+00:00"
    assert post["date_str"] == "September 28, 2026"
    assert post["html_body"] == '<div class="Body-module-scss-module__z40yvW__body"><p>Hi</p></div>'
