"""Render and parse JSON Feed 1.1 documents.

JSON Feed is this project's canonical persisted state: a `render_jsonfeed` →
`parse_jsonfeed` round trip must reproduce every
`Post` field exactly, including the large raw `html_body`. Fields JSON Feed has no native
slot for (slug, date_str, pub_date_precise) live in the spec-sanctioned `_claude_blog_rss`
extension object rather than being lossily folded into a native field.
"""

import json

from .feed import (
    FEED_AUTHOR,
    FEED_DESCRIPTION,
    FEED_IMAGE_URL,
    FEED_LANGUAGE,
    FEED_LINK,
    FEED_TITLE,
    TAG_AUTHORITY,
    TAG_DATE,
    base_url,
)
from .models import Post

JSONFEED_VERSION = "https://jsonfeed.org/version/1.1"


def jsonfeed_url() -> str:
    return f"{base_url()}/feed.json"


def jsonfeed_archive_url(year: str | int) -> str:
    return f"{base_url()}/archive-{year}.json"


def _entry_id(slug: str) -> str:
    return f"tag:{TAG_AUTHORITY},{TAG_DATE}:{slug}"


def _post_to_item(post: Post) -> dict:
    item: dict = {"id": _entry_id(post["slug"])}
    if post.get("url"):
        item["url"] = post["url"]
    if post.get("title"):
        item["title"] = post["title"]
    if post.get("summary"):
        item["summary"] = post["summary"]
    if post.get("html_body"):
        item["content_html"] = post["html_body"]
    if post.get("image_url"):
        item["image"] = post["image_url"]
    if post.get("pub_date"):
        item["date_published"] = post["pub_date"]
    if post.get("authors"):
        item["authors"] = [{"name": a} for a in post["authors"]]
    if post.get("categories"):
        item["tags"] = post["categories"]

    ext: dict = {"slug": post["slug"]}
    if post.get("date_str"):
        ext["date_str"] = post["date_str"]
    if post.get("pub_date_precise"):
        ext["pub_date_precise"] = post["pub_date_precise"]
    item["_claude_blog_rss"] = ext

    return item


def _item_to_post(item: dict) -> Post:
    ext = item.get("_claude_blog_rss") or {}
    slug = ext.get("slug") or item.get("id", "").rsplit(":", 1)[-1]

    post: Post = {
        "slug": slug,
        "url": item.get("url") or "",
        "title": item.get("title") or "",
        "date_str": ext.get("date_str") or "",
        "pub_date": item.get("date_published") or "",
        "categories": item.get("tags") or [],
        "authors": [a["name"] for a in item.get("authors") or []],
        "summary": item.get("summary") or "",
        "image_url": item.get("image") or "",
        "html_body": item.get("content_html") or "",
    }
    if ext.get("pub_date_precise"):
        post["pub_date_precise"] = ext["pub_date_precise"]
    return post


def render_jsonfeed(posts: list[Post], *, feed_url: str, title: str | None = None) -> str:
    """Render posts (most-recent-first, slug-tiebroken) to a JSON Feed 1.1 document."""
    # Stable double sort: slug ascending first, then pub_date descending on top of
    # that, so posts sharing a pub_date keep deterministic slug-ascending order.
    sorted_posts = sorted(posts, key=lambda p: p["slug"])
    sorted_posts.sort(key=lambda p: p.get("pub_date") or "", reverse=True)

    doc = {
        "version": JSONFEED_VERSION,
        "title": title or FEED_TITLE,
        "home_page_url": FEED_LINK,
        "feed_url": feed_url,
        "description": FEED_DESCRIPTION,
        "icon": FEED_IMAGE_URL,
        "language": FEED_LANGUAGE,
        "authors": [{"name": FEED_AUTHOR}],
        "items": [_post_to_item(post) for post in sorted_posts],
    }
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def parse_jsonfeed(text: str) -> dict[str, Post]:
    """Parse a JSON Feed 1.1 document back into a slug-keyed dict of `Post`s."""
    doc = json.loads(text)
    posts: dict[str, Post] = {}
    for item in doc.get("items", []):
        post = _item_to_post(item)
        posts[post["slug"]] = post
    return posts
