"""Tests for feed.py."""

import time
import xml.etree.ElementTree as ET
from datetime import datetime

import feedparser

from claude_blog_rss.feed import (
    EPOCH,
    FEED_LINK,
    atom_archive_url,
    atom_url,
    render_atom,
)

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "dc": "http://purl.org/dc/elements/1.1/",
    "media": "http://search.yahoo.com/mrss/",
    "fh": "http://purl.org/syndication/history/1.0",
}


def _make_posts(n: int) -> list[dict]:
    return [
        {
            "slug": f"post-{i}",
            "url": f"https://claude.com/blog/post-{i}",
            "title": f"Post {i}",
            "date_str": f"January {i + 1}, 2026",
            "pub_date": f"2026-01-{i + 1:02d}T00:00:00+00:00",
            "categories": ["Test"],
            "authors": [],
            "summary": "",
            "image_url": "",
            "html_body": f"<p>Content of post {i}</p>",
        }
        for i in range(n)
    ]


# ── Atom 1.0 ─────────────────────────────────────────────────────────────────


def test_atom_feed_is_valid_xml_with_atom_namespace():
    xml_str = render_atom(_make_posts(3))
    root = ET.fromstring(xml_str)
    assert root.tag == "{http://www.w3.org/2005/Atom}feed"


def test_atom_self_url_honoured_in_id_and_self_link():
    xml_str = render_atom(_make_posts(1), self_url="https://example.github.io/atom.xml")
    root = ET.fromstring(xml_str)
    assert root.find("atom:id", NS).text == "https://example.github.io/atom.xml"
    links = {link.attrib.get("rel"): link.attrib["href"] for link in root.findall("atom:link", NS)}
    assert links["self"] == "https://example.github.io/atom.xml"
    assert links["alternate"] == FEED_LINK


def test_atom_entry_id_is_tag_uri():
    posts = _make_posts(1)
    atom_root = ET.fromstring(render_atom(posts))
    atom_id = atom_root.find("atom:entry/atom:id", NS).text
    assert atom_id == "tag:claude.com,2026:post-0"


def test_atom_entry_has_summary_and_content_when_summary_present():
    posts = _make_posts(1)
    posts[0]["summary"] = "A short teaser."
    root = ET.fromstring(render_atom(posts))
    entry = root.find("atom:entry", NS)
    assert entry.find("atom:summary", NS).text == "A short teaser."
    content = entry.find("atom:content", NS)
    assert content is not None
    assert "Content of post 0" in content.text


def test_atom_entry_authors_and_thumbnail():
    posts = _make_posts(1)
    posts[0]["authors"] = ["Jane Doe"]
    posts[0]["image_url"] = "https://example.com/img.jpg"
    root = ET.fromstring(render_atom(posts))
    entry = root.find("atom:entry", NS)
    assert entry.find("dc:creator", NS).text == "Jane Doe"
    thumb = entry.find("media:group/media:thumbnail", NS)
    assert thumb.attrib["url"] == "https://example.com/img.jpg"


def test_atom_channel_has_logo_and_rights():
    root = ET.fromstring(render_atom(_make_posts(1)))
    assert root.find("atom:logo", NS).text.startswith("https://")
    assert root.find("atom:rights", NS).text


def test_atom_feed_has_author_when_entries_dont():
    """RFC 4287 4.1.1: atom:feed MUST have atom:author unless every entry
    does. Most posts have no byline, so the feed-level one is load-bearing."""
    root = ET.fromstring(render_atom(_make_posts(1)))
    assert root.find("atom:author/atom:name", NS).text


def test_atom_entry_with_authors_has_own_author_element():
    posts = _make_posts(1)
    posts[0]["authors"] = ["Jane Doe", "John Smith"]
    root = ET.fromstring(render_atom(posts))
    entry = root.find("atom:entry", NS)
    names = [a.find("atom:name", NS).text for a in entry.findall("atom:author", NS)]
    assert names == ["Jane Doe", "John Smith"]


# ── RFC 5005 (feed paging and archiving) ──────────────────────────────────────


def test_subscription_document_has_no_fh_archive():
    root = ET.fromstring(render_atom(_make_posts(1)))
    assert root.find("fh:archive", NS) is None


def test_archive_document_has_exactly_one_fh_archive():
    root = ET.fromstring(render_atom(_make_posts(1), archive=True))
    archives = root.findall("fh:archive", NS)
    assert len(archives) == 1


def test_current_prev_next_archive_links_present_when_supplied():
    xml_str = render_atom(
        _make_posts(1),
        current_url="https://example.github.io/atom.xml",
        prev_archive_url="https://example.github.io/archive-2025.xml",
        next_archive_url="https://example.github.io/archive-2027.xml",
    )
    root = ET.fromstring(xml_str)
    links = {link.attrib.get("rel"): link.attrib["href"] for link in root.findall("atom:link", NS)}
    assert links["current"] == "https://example.github.io/atom.xml"
    assert links["prev-archive"] == "https://example.github.io/archive-2025.xml"
    assert links["next-archive"] == "https://example.github.io/archive-2027.xml"


def test_current_prev_next_archive_links_absent_when_not_supplied():
    root = ET.fromstring(render_atom(_make_posts(1)))
    rels = {link.attrib.get("rel") for link in root.findall("atom:link", NS)}
    assert "current" not in rels
    assert "prev-archive" not in rels
    assert "next-archive" not in rels


def test_feed_base_url_env_override(monkeypatch):
    monkeypatch.setenv("FEED_BASE_URL", "https://example.org/feeds")
    assert atom_url() == "https://example.org/feeds/atom.xml"
    assert atom_archive_url(2025) == "https://example.org/feeds/archive-2025.xml"
    assert atom_archive_url("2025") == "https://example.org/feeds/archive-2025.xml"


# ── deterministic timestamps ──────────────────────────────────────────────────


def test_feed_updated_equals_newest_post_pub_date():
    posts = _make_posts(3)
    root = ET.fromstring(render_atom(posts))
    updated = root.find("atom:updated", NS).text
    newest = max(p["pub_date"] for p in posts)
    assert datetime.fromisoformat(updated) == datetime.fromisoformat(newest)


def test_feed_updated_is_epoch_for_empty_feed():
    root = ET.fromstring(render_atom([]))
    updated = root.find("atom:updated", NS).text
    assert datetime.fromisoformat(updated) == EPOCH


def test_entry_without_pub_date_gets_epoch_not_now():
    posts = _make_posts(1)
    posts[0]["pub_date"] = ""
    root = ET.fromstring(render_atom(posts))
    entry = root.find("atom:entry", NS)
    updated = entry.find("atom:updated", NS).text
    assert datetime.fromisoformat(updated) == EPOCH
    assert entry.find("atom:published", NS) is None


def test_render_atom_is_byte_identical_across_renders_over_time():
    posts = _make_posts(3)
    first = render_atom(posts)
    time.sleep(1.1)
    second = render_atom(posts)
    assert first == second


# ── feedparser round-trip (structural validity, not just well-formedness) ────


def _feedparser_posts():
    posts = _make_posts(2)
    posts[0]["authors"] = ["Jane Doe"]
    posts[0]["image_url"] = "https://example.com/img.jpg"
    posts[0]["summary"] = "A short teaser."
    posts[1]["categories"] = ["Product", "Claude Code"]
    return posts


def test_feedparser_parses_atom_without_errors():
    parsed = feedparser.parse(render_atom(_feedparser_posts()))
    assert not parsed.bozo, getattr(parsed, "bozo_exception", None)
    assert len(parsed.entries) == 2


def test_feedparser_atom_round_trips_author_and_summary():
    parsed = feedparser.parse(render_atom(_feedparser_posts()))
    assert parsed.feed.get("author") == "Anthropic"
    by_title = {e.title: e for e in parsed.entries}
    assert by_title["Post 0"].get("author") == "Jane Doe"
    assert by_title["Post 0"].get("summary") == "A short teaser."
    assert by_title["Post 1"].get("author") is None


def test_feedparser_parses_archive_document_without_errors():
    parsed = feedparser.parse(render_atom(_feedparser_posts(), archive=True))
    assert not parsed.bozo, getattr(parsed, "bozo_exception", None)
    assert len(parsed.entries) == 2
