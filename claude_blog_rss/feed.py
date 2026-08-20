"""Generate Atom 1.0 feeds from post dicts, including RFC 5005 archive documents."""

import os
from datetime import UTC, datetime

from feedgen.ext.base import BaseExtension
from feedgen.feed import FeedGenerator
from feedgen.util import xml_elem

from .models import Post

FEED_TITLE = "Claude Blog (unofficial)"
FEED_DESCRIPTION = (
    "Unofficial feed for https://claude.com/blog. Daily-updated mirror. Content © Anthropic."
)
FEED_LINK = "https://claude.com/blog"
FEED_LANGUAGE = "en"
# Atom 1.0 requires atom:author at feed or entry level (RFC 4287 §4.1.1); most
# posts have no byline, so this feed-level fallback covers those.
FEED_AUTHOR = "Anthropic"
FEED_COPYRIGHT = "Content © Anthropic. Unofficial, unaffiliated feed."
# Largest favicon-family asset claude.com serves (256x256 apple-touch-icon).
FEED_IMAGE_URL = (
    "https://cdn.prod.website-files.com/6889473510b50328dbb70ae6/"
    "68c33859cc6cd903686c66a2_apple-touch-icon.png"
)

# tag: URI (RFC 4151) authority/date for entry ids. Stays a stable,
# URL-independent atom:id across a future domain move (claude.com/blog is
# itself already a migration from claude.ai/blog). The date is simply when
# this identifier scheme was introduced, not a per-post date.
TAG_AUTHORITY = "claude.com"
TAG_DATE = "2026"

# RFC 5005 feed-history namespace, used to mark archive documents.
FH_NS = "http://purl.org/syndication/history/1.0"

# Fixed fallback for any timestamp that can't be derived from post data, so
# archive documents never pick up a wall-clock value and stay byte-stable
# across rebuilds.
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

# Override via FEED_BASE_URL env var (set to your GitHub Pages base URL).
_DEFAULT_BASE_URL = "https://dave-atx.github.io/anthropic-rss"


def base_url() -> str:
    return os.environ.get("FEED_BASE_URL", _DEFAULT_BASE_URL)


def atom_url() -> str:
    return f"{base_url()}/atom.xml"


def atom_archive_url(year: str | int) -> str:
    return f"{base_url()}/archive-{year}.xml"


def _entry_id(slug: str) -> str:
    return f"tag:{TAG_AUTHORITY},{TAG_DATE}:{slug}"


def _parse_pub_date(raw: str | None) -> datetime | None:
    """Parse a post's stored pub_date, or return None if missing/unparseable."""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


class FhExtension(BaseExtension):
    """RFC 5005 feed-history extension: marks a document as a feed archive by
    emitting an empty fh:archive element."""

    def extend_ns(self):
        return {"fh": FH_NS}

    def extend_atom(self, feed):
        xml_elem(f"{{{FH_NS}}}archive", feed)
        return feed

    def extend_rss(self, feed):
        return feed


def _build_feed_generator(
    posts: list[Post],
    self_url: str,
    *,
    archive: bool = False,
    current_url: str | None = None,
    prev_archive_url: str | None = None,
    next_archive_url: str | None = None,
) -> FeedGenerator:
    """Populate a FeedGenerator with the feed + entry data for an Atom document."""
    fg = FeedGenerator()
    fg.load_extension("dc")
    fg.load_extension("media")
    if archive:
        fg.register_extension("fh", FhExtension, atom=True, rss=True)
    fg.id(self_url)
    fg.title(FEED_TITLE)
    fg.description(FEED_DESCRIPTION)
    fg.copyright(FEED_COPYRIGHT)
    fg.author({"name": FEED_AUTHOR})
    # The self link must be added before the alternate link: feedgen keeps
    # atom:link elements in call order, and downstream consumers/tests expect
    # self first. The current/prev-archive/next-archive RFC 5005 paging links
    # (when present) are appended after the alternate link.
    fg.link(href=self_url, rel="self")
    fg.link(href=FEED_LINK, rel="alternate")
    if current_url is not None:
        fg.link(href=current_url, rel="current")
    if prev_archive_url is not None:
        fg.link(href=prev_archive_url, rel="prev-archive")
    if next_archive_url is not None:
        fg.link(href=next_archive_url, rel="next-archive")
    fg.image(url=FEED_IMAGE_URL, title=FEED_TITLE, link=FEED_LINK)
    fg.language(FEED_LANGUAGE)

    # feedgen.add_entry() prepends, so iterate oldest-first to get newest-first output
    sorted_posts = sorted(
        posts,
        key=lambda p: p.get("pub_date") or "",
        reverse=False,
    )

    # Feed-level <updated> must be derived from post data, never from the
    # wall clock, so archive documents are byte-stable across rebuilds.
    feed_updated = EPOCH
    for post in sorted_posts:
        dt = _parse_pub_date(post.get("pub_date"))
        if dt is not None and dt > feed_updated:
            feed_updated = dt
    fg.updated(feed_updated)

    for post in sorted_posts:
        fe = fg.add_entry()
        # A tag: URI is a spec-valid atom:id (RFC 4151); posts.json is
        # already keyed by slug, so this is stable and URL-independent.
        fe.id(_entry_id(post["slug"]))
        fe.title(post["title"] or post["slug"])
        fe.link(href=post["url"])

        # Entry <updated> must always be set explicitly: feedgen defaults it
        # to datetime.now() when omitted, which would break byte-stability.
        # Posts with no parseable pub_date fall back to EPOCH and get no
        # <published> at all, rather than a wall-clock timestamp.
        dt = _parse_pub_date(post.get("pub_date"))
        if dt is not None:
            fe.published(dt)
            fe.updated(dt)
        else:
            fe.updated(EPOCH)

        if post.get("categories"):
            for cat in post["categories"]:
                fe.category({"term": cat})

        if post.get("authors"):
            # dc:creator is included alongside atom:author for readers that
            # only look at Dublin Core; atom:author (name-only, no email)
            # covers readers that follow the Atom spec directly.
            fe.dc.dc_creator(post["authors"])
            fe.author([{"name": a} for a in post["authors"]])

        if post.get("image_url"):
            fe.media.thumbnail(url=post["image_url"])

        summary = (post.get("summary") or "").strip()
        html_body = post.get("html_body") or ""

        if summary:
            # isSummary=True routes this to atom:summary, leaving
            # atom:content for the full body below.
            fe.description(summary, isSummary=True)

        if html_body:
            fe.content(html_body, type="html")
        elif not summary:
            fe.content(f'<p><a href="{post["url"]}">{post["title"]}</a></p>', type="html")

    return fg


def render_atom(
    posts: list[Post],
    *,
    self_url: str | None = None,
    archive: bool = False,
    current_url: str | None = None,
    prev_archive_url: str | None = None,
    next_archive_url: str | None = None,
) -> str:
    """Render posts (most-recent-first) to an Atom 1.0 XML string.

    Pass archive=True to render an RFC 5005 archive document (adds an empty
    fh:archive marker). current_url/prev_archive_url/next_archive_url add the
    corresponding RFC 5005 rel="current"/"prev-archive"/"next-archive" links
    when supplied.
    """
    self_url = self_url or atom_url()
    fg = _build_feed_generator(
        posts,
        self_url,
        archive=archive,
        current_url=current_url,
        prev_archive_url=prev_archive_url,
        next_archive_url=next_archive_url,
    )
    return fg.atom_str(pretty=True).decode("utf-8")
