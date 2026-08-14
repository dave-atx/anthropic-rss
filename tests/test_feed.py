"""Tests for feed.py."""

import xml.etree.ElementTree as ET

from src.feed import FEED_LINK, render

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


def test_channel_link_is_blog_not_feed_url():
    """Regression test: feedgen's link() uses the *last* call for RSS <link>,
    so the alternate (blog) link must survive being added after the self link."""
    xml_str = render(_make_posts(1), feed_url="https://example.github.io/rss.xml")
    root = ET.fromstring(xml_str)
    channel = root.find("channel")
    assert channel.find("link").text == FEED_LINK


def test_entry_guid_is_tag_uri_not_url():
    """Regression test: a bare URL guid marked isPermaLink="false" is a
    contradictory combination; a tag: URI is unambiguously opaque."""
    posts = _make_posts(1)
    xml_str = render(posts)
    root = ET.fromstring(xml_str)
    guid = root.find("channel/item/guid")
    assert guid.text == "tag:claude.com,2026:post-0"
    assert guid.attrib["isPermaLink"] == "false"


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
