"""Tests for feed.py."""

import xml.etree.ElementTree as ET

from src.feed import FEED_LINK, render


def _make_posts(n: int) -> list[dict]:
    return [
        {
            "slug": f"post-{i}",
            "url": f"https://claude.com/blog/post-{i}",
            "title": f"Post {i}",
            "date_str": f"January {i + 1}, 2026",
            "pub_date": f"2026-01-{i + 1:02d}T00:00:00+00:00",
            "categories": ["Test"],
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
