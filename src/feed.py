"""Generate RSS 2.0 and Atom 1.0 feeds from post dicts."""

import os
from datetime import datetime, timezone

from feedgen.feed import FeedGenerator

FEED_TITLE = "Claude Blog (unofficial)"
FEED_DESCRIPTION = (
    "Unofficial feed for https://claude.com/blog. "
    "Daily-updated mirror. Content © Anthropic."
)
FEED_LINK = "https://claude.com/blog"
FEED_LANGUAGE = "en"
# Atom 1.0 requires atom:author at feed or entry level (RFC 4287 §4.1.1); most
# posts have no byline, so this feed-level fallback covers those. Harmless
# for RSS: FeedGenerator.author() only touches rss:channel/author when an
# email is supplied, which this isn't.
FEED_AUTHOR = "Anthropic"
FEED_COPYRIGHT = "Content © Anthropic. Unofficial, unaffiliated feed."
# Largest favicon-family asset claude.com serves (256x256 apple-touch-icon).
FEED_IMAGE_URL = (
    "https://cdn.prod.website-files.com/6889473510b50328dbb70ae6/"
    "68c33859cc6cd903686c66a2_apple-touch-icon.png"
)

# tag: URI (RFC 4151) authority/date for entry ids. Valid as both a
# spec-conformant atom:id and a stable, URL-independent RSS guid. The date is
# simply when this identifier scheme was introduced, not a per-post date.
TAG_AUTHORITY = "claude.com"
TAG_DATE = "2026"

# Override via FEED_URL / ATOM_FEED_URL env vars (set to your GitHub Pages
# URLs once known)
_DEFAULT_FEED_URL = "https://tim-hilde.github.io/anthropic-rss/rss.xml"
_DEFAULT_ATOM_FEED_URL = "https://tim-hilde.github.io/anthropic-rss/atom.xml"


def _entry_id(slug: str) -> str:
    return f"tag:{TAG_AUTHORITY},{TAG_DATE}:{slug}"


def _build_feed_generator(posts: list[dict], feed_url: str) -> FeedGenerator:
    """Populate a FeedGenerator with channel + entry data shared by RSS/Atom."""
    fg = FeedGenerator()
    fg.load_extension("dc")
    fg.load_extension("media")
    fg.id(feed_url)
    fg.title(FEED_TITLE)
    fg.description(FEED_DESCRIPTION)
    fg.copyright(FEED_COPYRIGHT)
    fg.author({"name": FEED_AUTHOR})
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
        # A tag: URI is a spec-valid atom:id and doubles as a stable,
        # URL-independent RSS guid: posts.json is already keyed by slug, and
        # this survives a future domain move the way a URL-based guid wouldn't
        # (claude.com/blog is itself already a migration from claude.ai/blog).
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

        if post.get("authors"):
            # dc:creator is what RSS readers show; atom:author (name-only,
            # no email) is silently dropped from RSS but populates the
            # per-entry author Atom readers look for.
            fe.dc.dc_creator(post["authors"])
            fe.author([{"name": a} for a in post["authors"]])

        if post.get("image_url"):
            fe.media.thumbnail(url=post["image_url"])

        summary = (post.get("summary") or "").strip()
        html_body = post.get("html_body") or ""

        if summary:
            # isSummary=True routes this to atom:summary (leaving atom:content
            # for the full body below) as well as rss:description.
            fe.description(summary, isSummary=True)

        if html_body:
            fe.content(html_body, type="html")
        elif not summary:
            fe.content(
                f'<p><a href="{post["url"]}">{post["title"]}</a></p>', type="html"
            )

    return fg


def render(posts: list[dict], feed_url: str | None = None) -> str:
    """Render posts (most-recent-first) to an RSS 2.0 XML string."""
    feed_url = feed_url or os.environ.get("FEED_URL", _DEFAULT_FEED_URL)
    fg = _build_feed_generator(posts, feed_url)
    return fg.rss_str(pretty=True).decode("utf-8")


def render_atom(posts: list[dict], feed_url: str | None = None) -> str:
    """Render posts (most-recent-first) to an Atom 1.0 XML string."""
    feed_url = feed_url or os.environ.get("ATOM_FEED_URL", _DEFAULT_ATOM_FEED_URL)
    fg = _build_feed_generator(posts, feed_url)
    return fg.atom_str(pretty=True).decode("utf-8")
