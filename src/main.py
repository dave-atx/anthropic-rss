"""Orchestrate: load state → diff → scrape new → save → render feed."""

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from .scrape import fetch_post, list_slugs, scrape_all_pages, REQUEST_DELAY
from .feed import render

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

POSTS_PATH = Path(__file__).parent.parent / "data" / "posts.json"
FEED_PATH = Path(__file__).parent.parent / "docs" / "rss.xml"

DAILY_PAGES = 2


def load_state() -> dict[str, dict]:
    if POSTS_PATH.exists():
        with POSTS_PATH.open() as f:
            return json.load(f)
    return {}


def save_state(posts: dict[str, dict]) -> None:
    POSTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with POSTS_PATH.open("w") as f:
        json.dump(posts, f, indent=2, ensure_ascii=False)
        f.write("\n")


def save_feed(posts: dict[str, dict], feed_count: int = 20) -> None:
    FEED_PATH.parent.mkdir(parents=True, exist_ok=True)
    post_list = list(posts.values())
    feed_posts = sorted(post_list, key=lambda p: p.get("pub_date") or "", reverse=True)[:feed_count]
    xml = render(feed_posts)
    with FEED_PATH.open("w") as f:
        f.write(xml)
    logger.info("Feed written: %s (%d items)", FEED_PATH, len(feed_posts))


def fetch_new_posts(new_slugs: list[tuple[str, str]], delay: float = REQUEST_DELAY) -> dict[str, dict]:
    fetched: dict[str, dict] = {}
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
    parser.add_argument("--backfill", action="store_true", help="Fetch all listing pages (initial run)")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Re-fetch every already-known post too, overwriting its stored data "
        "(use after a scraper change to backfill new fields onto old posts)",
    )
    parser.add_argument("--feed-count", type=int, default=20, help="Number of items in the feed (default: 20)")
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
        posts.update(fetched)
        save_state(posts)
        logger.info("State saved: %d total posts", len(posts))
    else:
        logger.info("No new posts found.")

    save_feed(posts, feed_count=args.feed_count)


if __name__ == "__main__":
    main()
