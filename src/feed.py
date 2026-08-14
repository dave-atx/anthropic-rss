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
FEED_COPYRIGHT = "Content © Anthropic. Unofficial, unaffiliated feed."
# Largest favicon-family asset claude.com serves (256x256 apple-touch-icon).
FEED_IMAGE_URL = (
    "https://cdn.prod.website-files.com/6889473510b50328dbb70ae6/"
    "68c33859cc6cd903686c66a2_apple-touch-icon.png"
)

# Override via FEED_URL env var (set to your GitHub Pages URL once known)
_DEFAULT_FEED_URL = "https://tim-hilde.github.io/anthropic-rss/rss.xml"


def render(posts: list[dict], feed_url: str | None = None) -> str:
    """Render posts (most-recent-first) to RSS 2.0 XML string."""
    feed_url = feed_url or os.environ.get("FEED_URL", _DEFAULT_FEED_URL)

    fg = FeedGenerator()
    fg.load_extension("dc")
    fg.load_extension("media")
    fg.id(feed_url)
    fg.title(FEED_TITLE)
    fg.description(FEED_DESCRIPTION)
    fg.copyright(FEED_COPYRIGHT)
    # feedgen's RSS <link> takes whichever link() call happens last, so the
    # self-referencing atom:link must be added first and the human-facing
    # alternate link (what RSS readers show as "visit site") added last.
    fg.link(href=feed_url, rel="self")
    fg.link(href=FEED_LINK, rel="alternate")
    fg.image(url=FEED_IMAGE_URL, title=FEED_TITLE, link=FEED_LINK)
    fg.language(FEED_LANGUAGE)

    # feedgen.add_entry() prepends, so iterate oldest-first to get newest-first output
    sorted_posts = sorted(
        posts,
        key=lambda p: p.get("pub_date") or "",
        reverse=False,
    )

    for post in sorted_posts:
        fe = fg.add_entry()
        # Opaque, URL-independent id: posts.json is already keyed by slug, and
        # this survives a future domain move the way a URL-based guid wouldn't
        # (claude.com/blog is itself already a migration from claude.ai/blog).
        fe.id(post["slug"])
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

        if post.get("authors"):
            fe.dc.dc_creator(post["authors"])

        if post.get("image_url"):
            fe.media.thumbnail(url=post["image_url"])

        summary = (post.get("summary") or "").strip()
        html_body = post.get("html_body") or ""

        if summary:
            fe.description(summary)

        if html_body:
            fe.content(html_body, type="html")
        elif not summary:
            fe.content(
                f'<p><a href="{post["url"]}">{post["title"]}</a></p>', type="html"
            )

    return fg.rss_str(pretty=True).decode("utf-8")
