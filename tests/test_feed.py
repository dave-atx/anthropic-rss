"""Tests for feed.py."""

import xml.etree.ElementTree as ET

import feedparser

from claude_blog_rss.feed import FEED_LINK, render, render_atom

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "media": "http://search.yahoo.com/mrss/",
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


def test_feed_is_valid_xml():
    xml_str = render(_make_posts(3))
    root = ET.fromstring(xml_str)
    assert root.tag == "rss"


def test_feed_version():
    xml_str = render(_make_posts(1))
    root = ET.fromstring(xml_str)
    assert root.attrib.get("version") == "2.0"


def test_feed_item_count_capped_at_20():
    posts = _make_posts(30)
    xml_str = render(posts[:20])
    root = ET.fromstring(xml_str)
    channel = root.find("channel")
    items = channel.findall("item")
    assert len(items) == 20


def test_feed_item_has_required_fields():
    xml_str = render(_make_posts(1))
    root = ET.fromstring(xml_str)
    item = root.find("channel/item")
    assert item.find("title").text == "Post 0"
    assert item.find("link").text is not None
    assert item.find("guid").text is not None


def test_feed_sorted_newest_first():
    posts = _make_posts(5)
    xml_str = render(posts)
    root = ET.fromstring(xml_str)
    channel = root.find("channel")
    items = channel.findall("item")
    titles = [item.find("title").text for item in items]
    # Post 4 (Jan 5) should come before Post 0 (Jan 1)
    assert titles.index("Post 4") < titles.index("Post 0")


def test_feed_empty_posts():
    xml_str = render([])
    root = ET.fromstring(xml_str)
    channel = root.find("channel")
    assert len(channel.findall("item")) == 0


# ── channel-level richness ──────────────────────────────────────────────────

def test_channel_link_is_blog_not_feed_url():
    """Regression test: feedgen's link() uses the *last* call for RSS <link>,
    so the alternate (blog) link must survive being added after the self link."""
    xml_str = render(_make_posts(1), feed_url="https://example.github.io/rss.xml")
    root = ET.fromstring(xml_str)
    channel = root.find("channel")
    assert channel.find("link").text == FEED_LINK


def test_channel_self_link_is_feed_url():
    xml_str = render(_make_posts(1), feed_url="https://example.github.io/rss.xml")
    root = ET.fromstring(xml_str)
    channel = root.find("channel")
    self_link = channel.find("atom:link", NS)
    assert self_link.attrib["href"] == "https://example.github.io/rss.xml"
    assert self_link.attrib["rel"] == "self"


def test_channel_has_copyright():
    xml_str = render(_make_posts(1))
    root = ET.fromstring(xml_str)
    assert root.find("channel/copyright").text


def test_channel_has_image():
    xml_str = render(_make_posts(1))
    root = ET.fromstring(xml_str)
    image = root.find("channel/image")
    assert image is not None
    assert image.find("url").text.startswith("https://")
    assert image.find("link").text == FEED_LINK


# ── entry-level richness ─────────────────────────────────────────────────────

def test_entry_guid_is_tag_uri_not_url():
    posts = _make_posts(1)
    xml_str = render(posts)
    root = ET.fromstring(xml_str)
    guid = root.find("channel/item/guid")
    assert guid.text == "tag:claude.com,2026:post-0"
    assert guid.attrib["isPermaLink"] == "false"


def test_entry_description_and_content_both_present_with_summary():
    posts = _make_posts(1)
    posts[0]["summary"] = "A short teaser."
    xml_str = render(posts)
    root = ET.fromstring(xml_str)
    item = root.find("channel/item")
    assert item.find("description").text == "A short teaser."
    content = item.find("content:encoded", NS)
    assert content is not None
    assert "Content of post 0" in content.text


def test_entry_without_summary_falls_back_to_description_only():
    posts = _make_posts(1)
    xml_str = render(posts)
    root = ET.fromstring(xml_str)
    item = root.find("channel/item")
    assert "Content of post 0" in item.find("description").text
    assert item.find("content:encoded", NS) is None


def test_entry_authors_become_dc_creator():
    posts = _make_posts(1)
    posts[0]["authors"] = ["Jane Doe", "John Smith"]
    xml_str = render(posts)
    root = ET.fromstring(xml_str)
    creators = root.findall("channel/item/dc:creator", NS)
    assert [c.text for c in creators] == ["Jane Doe", "John Smith"]


def test_entry_without_authors_has_no_dc_creator():
    xml_str = render(_make_posts(1))
    root = ET.fromstring(xml_str)
    assert root.find("channel/item/dc:creator", NS) is None


def test_entry_image_becomes_media_thumbnail():
    posts = _make_posts(1)
    posts[0]["image_url"] = "https://example.com/img.jpg"
    xml_str = render(posts)
    root = ET.fromstring(xml_str)
    thumb = root.find("channel/item/media:group/media:thumbnail", NS)
    assert thumb is not None
    assert thumb.attrib["url"] == "https://example.com/img.jpg"


def test_entry_without_image_has_no_media_group():
    xml_str = render(_make_posts(1))
    root = ET.fromstring(xml_str)
    assert root.find("channel/item/media:group", NS) is None


# ── Atom 1.0 ─────────────────────────────────────────────────────────────────

def test_atom_feed_is_valid_xml_with_atom_namespace():
    xml_str = render_atom(_make_posts(3))
    root = ET.fromstring(xml_str)
    assert root.tag == "{http://www.w3.org/2005/Atom}feed"


def test_atom_self_and_alternate_links():
    xml_str = render_atom(_make_posts(1), feed_url="https://example.github.io/atom.xml")
    root = ET.fromstring(xml_str)
    links = {l.attrib.get("rel"): l.attrib["href"] for l in root.findall("atom:link", NS)}
    assert links["self"] == "https://example.github.io/atom.xml"
    assert links["alternate"] == FEED_LINK


def test_atom_entry_id_is_same_tag_uri_as_rss_guid():
    posts = _make_posts(1)
    rss_root = ET.fromstring(render(posts))
    atom_root = ET.fromstring(render_atom(posts))
    rss_guid = rss_root.find("channel/item/guid").text
    atom_id = atom_root.find("atom:entry/atom:id", NS).text
    assert rss_guid == atom_id == "tag:claude.com,2026:post-0"


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


def test_rss_entry_author_unaffected_by_name_only_atom_author():
    """FeedEntry.author() only touches rss:author when an email is given, so
    name-only authors must not leak an empty/malformed <author> into RSS."""
    posts = _make_posts(1)
    posts[0]["authors"] = ["Jane Doe"]
    root = ET.fromstring(render(posts))
    assert root.find("channel/item/author") is None


# ── feedparser round-trip (structural validity, not just well-formedness) ────

def _feedparser_posts():
    posts = _make_posts(2)
    posts[0]["authors"] = ["Jane Doe"]
    posts[0]["image_url"] = "https://example.com/img.jpg"
    posts[0]["summary"] = "A short teaser."
    posts[1]["categories"] = ["Product", "Claude Code"]
    return posts


def test_feedparser_parses_rss_without_errors():
    parsed = feedparser.parse(render(_feedparser_posts()))
    assert not parsed.bozo, getattr(parsed, "bozo_exception", None)
    assert len(parsed.entries) == 2


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
