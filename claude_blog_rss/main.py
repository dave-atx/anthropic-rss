"""Orchestrate: load state → diff → scrape new → save → render feed."""

import argparse
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path

from .feed import render, render_atom
from .models import Post
from .scrape import (
    REQUEST_DELAY,
    fetch_post,
    fetch_sitemap_lastmods,
    list_slugs,
    scrape_all_pages,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent
POSTS_PATH = ROOT / "data" / "posts.json"
FEED_PATH = ROOT / "docs" / "rss.xml"
ATOM_FEED_PATH = ROOT / "docs" / "atom.xml"

DAILY_PAGES = 2


def load_state() -> dict[str, Post]:
    if POSTS_PATH.exists():
        return json.loads(POSTS_PATH.read_text())
    return {}


def save_state(posts: dict[str, Post]) -> None:
    POSTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    POSTS_PATH.write_text(json.dumps(posts, indent=2, ensure_ascii=False) + "\n")


def save_feed(posts: dict[str, Post], feed_count: int = 20) -> None:
    FEED_PATH.parent.mkdir(parents=True, exist_ok=True)
    post_list = list(posts.values())
    feed_posts = sorted(post_list, key=lambda p: p.get("pub_date") or "", reverse=True)[:feed_count]

    with FEED_PATH.open("w") as f:
        f.write(render(feed_posts))
    logger.info("RSS feed written: %s (%d items)", FEED_PATH, len(feed_posts))

    with ATOM_FEED_PATH.open("w") as f:
        f.write(render_atom(feed_posts))
    logger.info("Atom feed written: %s (%d items)", ATOM_FEED_PATH, len(feed_posts))


def _parse_stored_date(pub_date: str | None) -> datetime | None:
    if not pub_date:
        return None
    try:
        dt = datetime.fromisoformat(pub_date)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def enrich_pub_dates(posts: dict[str, Post], lastmods: dict[str, datetime]) -> int:
    """Upgrade a post's date-only pub_date to a precise time when the
    sitemap's lastmod for it falls on the same UTC day as the page's own
    Date field. Once a post is upgraded it's marked pub_date_precise and
    never re-evaluated, so a later unrelated edit (which bumps lastmod to a
    different day) can't erase a precise time already locked in.

    Returns the number of posts upgraded.
    """
    updated = 0
    for slug, post in posts.items():
        if post.get("pub_date_precise"):
            continue
        lastmod = lastmods.get(slug)
        if lastmod is None:
            continue
        pub_date = _parse_stored_date(post.get("pub_date"))
        if pub_date is None:
            continue
        if lastmod.date() == pub_date.date():
            post["pub_date"] = lastmod.replace(microsecond=0).isoformat()
            post["pub_date_precise"] = True
            updated += 1
    return updated


def _preserve_precise_dates(posts: dict[str, Post], refetched: dict[str, Post]) -> None:
    """A re-fetched post has no pub_date_precise flag and a midnight-UTC
    pub_date, since fetch_post() only reads the page's Date field. Without
    this, --refresh would silently erase every precise timestamp
    enrich_pub_dates previously locked in.
    """
    for slug, post in refetched.items():
        old = posts.get(slug)
        if old and old.get("pub_date_precise"):
            post["pub_date"] = old["pub_date"]
            post["pub_date_precise"] = True


def fetch_new_posts(
    new_slugs: list[tuple[str, str]], delay: float = REQUEST_DELAY
) -> dict[str, Post]:
    fetched: dict[str, Post] = {}
    for i, (slug, _title) in enumerate(new_slugs):
        logger.info("Fetching post %d/%d: %s", i + 1, len(new_slugs), slug)
        post = fetch_post(slug)
        if post:
            fetched[slug] = post
        if i < len(new_slugs) - 1:
            time.sleep(delay)
    return fetched


def main() -> None:
    parser = argparse.ArgumentParser(description="Update claude.com/blog RSS feed")
    parser.add_argument(
        "--backfill", action="store_true", help="Fetch all listing pages (initial run)"
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Re-fetch every already-known post too, overwriting its stored data "
        "(use after a scraper change to backfill new fields onto old posts)",
    )
    parser.add_argument(
        "--feed-count", type=int, default=20, help="Number of items in the feed (default: 20)"
    )
    args = parser.parse_args()

    posts = load_state()
    logger.info("Loaded %d known posts", len(posts))

    if args.backfill:
        listing = scrape_all_pages()
    else:
        listing: list[tuple[str, str]] = []
        for page in range(1, DAILY_PAGES + 1):
            listing.extend(list_slugs(page))
            if page < DAILY_PAGES:
                time.sleep(REQUEST_DELAY)

    if args.refresh:
        discovered_slugs = {slug for slug, _title in listing}
        all_slugs = set(posts) | discovered_slugs
        new_slugs = [(slug, "") for slug in all_slugs]
        logger.info("Refreshing all %d known posts (plus any newly discovered)", len(new_slugs))
    else:
        new_slugs = [(slug, title) for slug, title in listing if slug not in posts]
        logger.info("New posts to fetch: %d", len(new_slugs))

    if new_slugs:
        fetched = fetch_new_posts(new_slugs)
        _preserve_precise_dates(posts, fetched)
        posts.update(fetched)
    else:
        logger.info("No new posts found.")

    lastmods = fetch_sitemap_lastmods()
    enriched = enrich_pub_dates(posts, lastmods) if lastmods else 0
    if enriched:
        logger.info("Enriched %d post(s) with precise publish times from sitemap", enriched)

    if new_slugs or enriched:
        save_state(posts)
        logger.info("State saved: %d total posts", len(posts))

    save_feed(posts, feed_count=args.feed_count)


if __name__ == "__main__":
    main()
