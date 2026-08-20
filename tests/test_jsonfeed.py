"""Tests for jsonfeed.py."""

import json
import pathlib

import pytest

from claude_blog_rss.jsonfeed import JSONFEED_VERSION, parse_jsonfeed, render_jsonfeed

FEED_URL = "https://example.github.io/anthropic-rss/feed.json"

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCS_PATH = ROOT / "docs"
LEGACY_POSTS_PATH = ROOT / "data" / "posts.json"


def _make_post(i: int, **overrides) -> dict:
    post = {
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
    post.update(overrides)
    return post


def _make_posts(n: int) -> list[dict]:
    return [_make_post(i) for i in range(n)]


# ── full-corpus round-trip ───────────────────────────────────────────────────

# Schema defaults for the always-present Post fields (everything but
# pub_date_precise, which is genuinely optional). A couple of legacy entries
# in data/posts.json predate authors/summary/image_url being added to the
# schema and simply lack those keys rather than storing them empty;
# parse_jsonfeed (per spec) always fills always-present fields back in, so
# the round trip legitimately normalizes those two stale entries to the full
# schema shape. Applying the same defaults to the "expected" side keeps this
# test strict about real field/key loss while not failing on that known,
# pre-existing data anomaly.
_POST_DEFAULTS = {
    "slug": "",
    "url": "",
    "title": "",
    "date_str": "",
    "pub_date": "",
    "categories": [],
    "authors": [],
    "summary": "",
    "image_url": "",
    "html_body": "",
}


def _real_corpus() -> dict[str, dict]:
    """Every post this checkout has on disk, whichever store it lives in.

    Prefers the committed JSON Feed archives, so this keeps exercising a real
    corpus after the legacy posts.json seed is deleted.
    """
    posts: dict[str, dict] = {}
    for path in sorted(DOCS_PATH.glob("archive-*.json")):
        posts.update(parse_jsonfeed(path.read_text()))
    if not posts and LEGACY_POSTS_PATH.exists():
        posts = json.loads(LEGACY_POSTS_PATH.read_text())
    return posts


def test_full_corpus_round_trip():
    posts = _real_corpus()
    if not posts:
        pytest.skip("no stored corpus in this checkout")
    post_list = list(posts.values())
    doc = render_jsonfeed(post_list, feed_url=FEED_URL)
    parsed = parse_jsonfeed(doc)
    expected = {slug: {**_POST_DEFAULTS, **p} for slug, p in posts.items()}
    assert parsed == expected


# ── document shape ───────────────────────────────────────────────────────────


def test_output_is_valid_json():
    doc = render_jsonfeed(_make_posts(3), feed_url=FEED_URL)
    parsed = json.loads(doc)
    assert isinstance(parsed, dict)


def test_version_is_1_1():
    doc = json.loads(render_jsonfeed(_make_posts(1), feed_url=FEED_URL))
    assert doc["version"] == JSONFEED_VERSION == "https://jsonfeed.org/version/1.1"


def test_feed_url_is_passed_through():
    doc = json.loads(render_jsonfeed(_make_posts(1), feed_url=FEED_URL))
    assert doc["feed_url"] == FEED_URL


def test_title_defaults_to_feed_title():
    doc = json.loads(render_jsonfeed(_make_posts(1), feed_url=FEED_URL))
    assert doc["title"] == "Claude Blog (unofficial)"


def test_title_override():
    doc = json.loads(render_jsonfeed(_make_posts(1), feed_url=FEED_URL, title="Custom Title"))
    assert doc["title"] == "Custom Title"


# ── ordering ──────────────────────────────────────────────────────────────────


def test_items_sorted_newest_first():
    posts = _make_posts(5)
    doc = json.loads(render_jsonfeed(posts, feed_url=FEED_URL))
    slugs = [item["_claude_blog_rss"]["slug"] for item in doc["items"]]
    assert slugs == ["post-4", "post-3", "post-2", "post-1", "post-0"]


def test_render_is_deterministic_byte_identical():
    posts = _make_posts(10)
    doc1 = render_jsonfeed(posts, feed_url=FEED_URL)
    doc2 = render_jsonfeed(posts, feed_url=FEED_URL)
    assert doc1 == doc2


def test_tie_break_by_slug_ascending():
    posts = [
        _make_post(2, slug="charlie", pub_date="2026-01-01T00:00:00+00:00"),
        _make_post(0, slug="alpha", pub_date="2026-01-01T00:00:00+00:00"),
        _make_post(1, slug="bravo", pub_date="2026-01-01T00:00:00+00:00"),
    ]
    doc = json.loads(render_jsonfeed(posts, feed_url=FEED_URL))
    slugs = [item["_claude_blog_rss"]["slug"] for item in doc["items"]]
    assert slugs == ["alpha", "bravo", "charlie"]


# ── omission of empty/falsy fields ──────────────────────────────────────────


def test_empty_fields_omitted_from_item():
    post = _make_post(0, summary="", image_url="", authors=[], categories=[], html_body="<p>x</p>")
    doc = json.loads(render_jsonfeed([post], feed_url=FEED_URL))
    item = doc["items"][0]
    assert "summary" not in item
    assert "image" not in item
    assert "authors" not in item
    assert "tags" not in item
    assert item["id"]  # always present


def test_present_fields_included_when_truthy():
    post = _make_post(
        0,
        summary="A teaser",
        image_url="https://example.com/img.jpg",
        authors=["Jane Doe"],
        categories=["Product"],
    )
    doc = json.loads(render_jsonfeed([post], feed_url=FEED_URL))
    item = doc["items"][0]
    assert item["summary"] == "A teaser"
    assert item["image"] == "https://example.com/img.jpg"
    assert item["authors"] == [{"name": "Jane Doe"}]
    assert item["tags"] == ["Product"]


# ── pub_date_precise presence/absence ───────────────────────────────────────


def test_post_without_pub_date_precise_round_trips_without_key():
    post = _make_post(0)
    assert "pub_date_precise" not in post
    doc = render_jsonfeed([post], feed_url=FEED_URL)
    item = json.loads(doc)["items"][0]
    assert "pub_date_precise" not in item["_claude_blog_rss"]
    parsed = parse_jsonfeed(doc)
    assert "pub_date_precise" not in parsed["post-0"]
    assert parsed["post-0"] == post


def test_post_with_pub_date_precise_round_trips_with_key():
    post = _make_post(0, pub_date_precise=True)
    doc = render_jsonfeed([post], feed_url=FEED_URL)
    item = json.loads(doc)["items"][0]
    assert item["_claude_blog_rss"]["pub_date_precise"] is True
    parsed = parse_jsonfeed(doc)
    assert parsed["post-0"]["pub_date_precise"] is True
    assert parsed["post-0"] == post


# ── content_html fidelity ───────────────────────────────────────────────────


def test_content_html_preserves_special_characters():
    tricky_html = "<p>Tags &amp; \"quotes\" &lt;escaped&gt; 'apostrophes' & raw ampersand</p>"
    post = _make_post(0, html_body=tricky_html)
    doc = render_jsonfeed([post], feed_url=FEED_URL)
    parsed = parse_jsonfeed(doc)
    assert parsed["post-0"]["html_body"] == tricky_html


def test_content_html_preserves_large_body():
    big_html = "<p>" + ("word " * 2000) + "</p>"  # multi-KB
    post = _make_post(0, html_body=big_html)
    doc = render_jsonfeed([post], feed_url=FEED_URL)
    parsed = parse_jsonfeed(doc)
    assert parsed["post-0"]["html_body"] == big_html
    assert len(big_html.encode()) > 2000


# ── unicode ──────────────────────────────────────────────────────────────────


def test_unicode_preserved_and_not_escaped():
    post = _make_post(
        0,
        title="An em—dash and café naïve résumé 日本語",
        html_body="<p>— 日本語 éèê</p>",
    )
    doc = render_jsonfeed([post], feed_url=FEED_URL)
    assert "\\u" not in doc  # not escaped to \uXXXX
    assert "—" in doc
    assert "日本語" in doc
    parsed = parse_jsonfeed(doc)
    assert parsed["post-0"] == post


# ── parsing robustness ───────────────────────────────────────────────────────


def test_parse_tolerates_unknown_extra_keys():
    doc = json.loads(render_jsonfeed(_make_posts(1), feed_url=FEED_URL))
    doc["items"][0]["some_unknown_field"] = "surprise"
    doc["some_other_unknown_top_level"] = 42
    parsed = parse_jsonfeed(json.dumps(doc))
    assert "post-0" in parsed
    assert parsed["post-0"]["title"] == "Post 0"


def test_slug_falls_back_to_id_tail_when_extension_missing():
    doc = json.loads(render_jsonfeed(_make_posts(1), feed_url=FEED_URL))
    del doc["items"][0]["_claude_blog_rss"]
    parsed = parse_jsonfeed(json.dumps(doc))
    assert "post-0" in parsed
    assert parsed["post-0"]["slug"] == "post-0"


# ── empty feed ───────────────────────────────────────────────────────────────


def test_empty_feed_renders_and_parses_to_empty_dict():
    doc = render_jsonfeed([], feed_url=FEED_URL)
    parsed_doc = json.loads(doc)
    assert parsed_doc["items"] == []
    assert parse_jsonfeed(doc) == {}
