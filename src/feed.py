"""Generate RSS 2.0 feed from post dicts."""

import os
from datetime import datetime, timezone

from feedgen.feed import FeedGenerator

FEED_TITLE = "Claude Blog (unofficial RSS)"
FEED_DESCRIPTION = (
    "Unofficial RSS feed for https://claude.com/blog. "
    "Daily-updated mirror. Content © Anthropic."
)
FEED_LINK = "https://claude.com/blog"
FEED_LANGUAGE = "en"

# tag: URI (RFC 4151) authority/date for entry ids. Gives a stable,
# URL-independent guid instead of the post URL, which has already moved once
# (claude.ai/blog -> claude.com/blog) and could again.
TAG_AUTHORITY = "claude.com"
TAG_DATE = "2026"

# Override via FEED_URL env var (set to your GitHub Pages URL once known)
_DEFAULT_FEED_URL = "https://tim-hilde.github.io/anthropic-rss/rss.xml"


def _entry_id(slug: str) -> str:
    return f"tag:{TAG_AUTHORITY},{TAG_DATE}:{slug}"


def render(posts: list[dict], feed_url: str | None = None) -> str:
    """Render posts (most-recent-first) to RSS 2.0 XML string."""
    feed_url = feed_url or os.environ.get("FEED_URL", _DEFAULT_FEED_URL)

    fg = FeedGenerator()
    fg.id(feed_url)
    fg.title(FEED_TITLE)
    fg.description(FEED_DESCRIPTION)
    # feedgen's RSS <link> takes whichever link() call happens last, so the
    # self-referencing atom:link must be added first and the human-facing
    # alternate link (what RSS readers show as "visit site") added last.
    fg.link(href=feed_url, rel="self")
    fg.link(href=FEED_LINK, rel="alternate")
    fg.language(FEED_LANGUAGE)

    # feedgen.add_entry() prepends, so iterate oldest-first to get newest-first output
    sorted_posts = sorted(
        posts,
        key=lambda p: p.get("pub_date") or "",
        reverse=False,
    )

    for post in sorted_posts:
        fe = fg.add_entry()
        # A tag: URI is a stable, URL-independent guid: posts.json is already
        # keyed by slug, and this survives a future domain move the way a
        # URL-based guid wouldn't.
        fe.id(_entry_id(post["slug"]))
        fe.title(post["title"] or post["slug"])
        fe.link(href=post["url"])

        if post.get("pub_date"):
            try:
                dt = datetime.fromisoformat(post["pub_date"])
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                fe.published(dt)
                fe.updated(dt)
            except ValueError:
                pass

        if post.get("categories"):
            for cat in post["categories"]:
                fe.category({"term": cat})

        if post.get("html_body"):
            fe.content(post["html_body"], type="html")
        elif post.get("title"):
            fe.content(
                f'<p><a href="{post["url"]}">{post["title"]}</a></p>', type="html"
            )

    return fg.rss_str(pretty=True).decode("utf-8")
