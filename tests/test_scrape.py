"""Tests for scrape.py using local HTML fixtures (no network)."""

import json
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from claude_blog_rss import scrape
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
    discover,
    parse_anthropic_index,
    parse_anthropic_sitemap,
    parse_claude_dev_index,
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


# ── other indexes ─────────────────────────────────────────────────────────────


def test_index_fixtures_match_the_shared_expectations():
    """index_slugs.json is what both this scraper and the Worker must find in
    each index fixture (worker/test/index.spec.ts reads it too)."""
    expected = json.loads((FIXTURES / "index_slugs.json").read_text())
    found = {
        "articles_listing.html": parse_listing(listing_html()),
        "claude_dev_posts.json": parse_claude_dev_index(
            json.loads((FIXTURES / "claude_dev_posts.json").read_text())
        ),
        "anthropic_news_index.html": parse_anthropic_index(
            (FIXTURES / "anthropic_news_index.html").read_text(), "news"
        ),
        "anthropic_research_index.html": parse_anthropic_index(
            (FIXTURES / "anthropic_research_index.html").read_text(), "research"
        ),
    }
    assert {name: [slug for slug, _url in posts] for name, posts in found.items()} == expected


def test_claude_dev_index_lists_every_post():
    index = json.loads((FIXTURES / "claude_dev_posts.json").read_text())
    posts = parse_claude_dev_index(index)
    assert len(posts) == 15
    assert posts[0] == (
        "building-effective-agent-automations",
        "https://claude.dev/blog/building-effective-agent-automations/",
    )


def test_claude_dev_index_skips_drafts():
    index = {
        "posts": [
            {"slug": "live", "url": "https://claude.dev/blog/live/", "draft": False},
            {"slug": "wip", "url": "https://claude.dev/blog/wip/", "draft": True},
        ]
    }
    assert [slug for slug, _url in parse_claude_dev_index(index)] == ["live"]


def test_anthropic_news_index_reads_posts_from_page_data():
    posts = parse_anthropic_index((FIXTURES / "anthropic_news_index.html").read_text(), "news")
    # The rendered HTML links ~20; the embedded data has more.
    assert len(posts) == 78
    assert ("public-benefit-fund", "https://www.anthropic.com/news/public-benefit-fund") in posts
    slugs = {slug for slug, _url in posts}
    # Research articles in the news page's data, and category pages it links.
    assert "claude-scorpion-toxin-binder-design" not in slugs
    assert not slugs & {"announcements", "partnerships", "policy", "product"}


def test_anthropic_research_index():
    posts = parse_anthropic_index(
        (FIXTURES / "anthropic_research_index.html").read_text(), "research"
    )
    assert len(posts) == 61
    assert (
        "claude-scorpion-toxin-binder-design",
        "https://www.anthropic.com/research/claude-scorpion-toxin-binder-design",
    ) in posts
    # Linked from the hero carousel only, not in the page data as articles.
    assert ("project-vend-2", "https://www.anthropic.com/research/project-vend-2") in posts
    slugs = {slug for slug, _url in posts}
    # A news article in the research page's data.
    assert "public-benefit-fund" not in slugs


def test_anthropic_sitemap_lists_research_posts_only():
    xml = (FIXTURES / "anthropic_sitemap.xml").read_bytes()
    assert parse_anthropic_sitemap(xml, "research") == [
        (
            "building-effective-agents",
            "https://www.anthropic.com/research/building-effective-agents",
        ),
        (
            "claude-scorpion-toxin-binder-design",
            "https://www.anthropic.com/research/claude-scorpion-toxin-binder-design",
        ),
    ]


def next_push(payload: str) -> str:
    """Wrap RSC payload text the way a Next.js page embeds it."""
    return f"<script>self.__next_f.push([1,{json.dumps(payload)}])</script>"


def test_anthropic_index_takes_each_slug_from_its_own_post():
    # The slugless article must not be credited with the `page` slug after it.
    # The payload is split mid-object across two chunks, as Next.js may.
    payload = (
        '[{"_id":"a1","_type":"article","slug":{"_type":"slug","current":"first"},'
        '"articleType":"news"},'
        '{"_id":"a2","_type":"article","title":"no slug","articleType":"news"},'
        '{"_id":"p1","_type":"page","slug":{"_type":"slug","current":"research"}},'
        '{"_id":"a3","_type":"article","slug":{"_type":"slug","current":"a-paper"},'
        '"articleType":"research"},'
        '{"_id":"a4","_type":"article","slug":{"_type":"slug","current":"elsewhere"},'
        '"articleType":"news","linkedUrl":"/features/elsewhere"},'
        '{"_id":"a5","_type":"article","cardTeams":[{"_type":"team"}],'
        '"slug":{"_type":"slug","current":"second"},"articleType":"news"}]'
    )
    html = (
        next_push(payload[:150])
        + next_push(payload[150:])
        + '<a href="/news/linked-only">x</a><a href="/news/first">dup</a>'
        + '<a class="TopicLink-module-scss-module__Ab12Cd__link" href="/news/product">x</a>'
        + '<a href="/research/other-index">x</a>'
    )
    assert [slug for slug, _url in parse_anthropic_index(html, "news")] == [
        "first",
        "second",
        "linked-only",
    ]


def test_discover_merges_indexes_listing_first(monkeypatch):
    monkeypatch.setattr(scrape.time, "sleep", lambda _s: None)
    monkeypatch.setattr(
        scrape, "list_slugs", lambda: [("moved", f"{ARTICLES}/moved"), ("a", f"{ARTICLES}/a")]
    )
    monkeypatch.setattr(scrape, "scrape_all_pages", lambda: pytest.fail("not a backfill"))
    monkeypatch.setattr(
        scrape, "list_claude_dev", lambda: [("moved", "https://claude.dev/blog/moved/")]
    )
    monkeypatch.setattr(
        scrape, "list_anthropic", lambda d: [(f"{d}-post", f"https://www.anthropic.com/{d}/x")]
    )
    assert discover() == [
        ("moved", f"{ARTICLES}/moved"),
        ("a", f"{ARTICLES}/a"),
        ("news-post", "https://www.anthropic.com/news/x"),
        ("research-post", "https://www.anthropic.com/research/x"),
    ]


def test_backfill_adds_research_from_the_sitemap(monkeypatch):
    monkeypatch.setattr(scrape.time, "sleep", lambda _s: None)
    monkeypatch.setattr(scrape, "scrape_all_pages", lambda: [("a", f"{ARTICLES}/a")])
    monkeypatch.setattr(scrape, "list_claude_dev", lambda: [])
    monkeypatch.setattr(scrape, "list_anthropic", lambda d: [])
    monkeypatch.setattr(
        scrape,
        "list_anthropic_sitemap",
        lambda d: [("old-paper", f"https://www.anthropic.com/{d}/old-paper")],
    )
    assert discover(backfill=True) == [
        ("a", f"{ARTICLES}/a"),
        ("old-paper", "https://www.anthropic.com/research/old-paper"),
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


def test_anthropic_news_post_takes_categories_from_the_hero():
    # The October 2026 template: no PostDetail subjects, and no time of day.
    url = "https://www.anthropic.com/news/public-benefit-fund"
    post = _parse_anthropic(fixture_soup("anthropic_news_post_hero.html"), url)
    assert post["title"] == "Investing in the public benefit"
    assert post["pub_date"] == "2026-10-18T00:00:00+00:00"
    assert "pub_date_precise" not in post
    assert post["categories"] == [
        "Announcements",
        "Funding",
        "Economic Futures",
        "Labor market",
        "Nonprofits",
        "Leadership",
    ]
    assert post["html_body"].startswith('<div class="Body-module-scss-module__')


def test_anthropic_research_paper_leads_with_the_link_to_the_paper():
    url = "https://www.anthropic.com/research/in-context-learning-and-induction-heads"
    post = _parse_anthropic(fixture_soup("anthropic_research_paper.html"), url)
    assert post["title"] == "In-context learning and induction heads"
    assert post["pub_date"] == "2022-03-08T00:00:00+00:00"
    # Deduped: the team link and the first topic are both "Interpretability".
    assert post["categories"] == ["Interpretability", "Training", "Scaling laws"]
    assert post["html_body"].startswith(
        '<p><a href="https://transformer-circuits.pub/2022/'
        'in-context-learning-and-induction-heads/index.html">Read the paper</a></p>'
    )


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
    # Four benchmark charts, each now a link; the tab icons are just dropped.
    assert "<svg" not in body
    assert body.count(f'<p><a href="{url}">View this chart on anthropic.com</a></p>') == 4


def test_svg_figures_become_links_and_decoration_is_dropped():
    soup = BeautifulSoup(
        "<div>"
        '<figure><div role="img" aria-label="Score rises with effort.">'
        '<svg aria-hidden="true" class="wide"><text>1</text></svg>'
        '<svg aria-hidden="true" class="narrow"><text>1</text></svg></div>'
        "<figcaption>Fig 1</figcaption></figure>"
        '<figure><svg aria-label="OSWorld"><text>72%</text></svg></figure>'
        '<p><span aria-hidden="true"><svg><circle r="1"/></svg></span>Claude</p>'
        '<p><svg aria-hidden="true"><path d=""/></svg>icon</p>'
        "</div>",
        "lxml",
    )
    html = _clean_external_body(soup.div, "https://claude.dev/blog/post/")
    assert html == (
        "<div>"
        '<figure><p><em>Score rises with effort.</em> <a href="https://claude.dev/blog/post/">'
        "View this chart on claude.dev</a></p><figcaption>Fig 1</figcaption></figure>"
        '<figure><p><a href="https://claude.dev/blog/post/">View this chart on claude.dev</a></p>'
        "</figure>"
        '<p><span aria-hidden="true"></span>Claude</p>'
        "<p>icon</p>"
        "</div>"
    )


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
