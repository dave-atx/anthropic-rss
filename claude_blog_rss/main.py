"""Orchestrate: load state → diff → scrape new → save → render feeds."""

import argparse
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path

from .archives import partition
from .feed import atom_archive_url, atom_url, render_atom
from .jsonfeed import jsonfeed_archive_url, jsonfeed_url, parse_jsonfeed, render_jsonfeed
from .models import Post
from .scrape import (
    REQUEST_DELAY,
    fetch_post,
    fetch_sitemap_lastmods,
    fetch_text,
    list_slugs,
    scrape_all_pages,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent
DOCS_PATH = ROOT / "docs"
CURRENT_JSON_PATH = DOCS_PATH / "feed.json"
ATOM_PATH = DOCS_PATH / "atom.xml"

DAILY_PAGES = 2


def load_state() -> dict[str, Post]:
    """Rebuild the corpus from the published JSON Feed documents.

    Closed years are committed, so they always come from the working tree.
    The current year is rebuilt every run and therefore isn't committed; it is
    recovered from the local file (restored by actions/cache), falling back to
    the copy already deployed to Pages. Both can miss on a first run, in which
    case the scrape refills it.
    """
    posts: dict[str, Post] = {}

    for path in sorted(DOCS_PATH.glob("archive-*.json")):
        archived = parse_jsonfeed(path.read_text())
        logger.info("Loaded %d post(s) from %s", len(archived), path.name)
        posts.update(archived)

    posts.update(_load_current_year())
    return posts


def _load_current_year() -> dict[str, Post]:
    if CURRENT_JSON_PATH.exists():
        current = parse_jsonfeed(CURRENT_JSON_PATH.read_text())
        logger.info("Loaded %d current-year post(s) from %s", len(current), CURRENT_JSON_PATH.name)
        return current

    url = jsonfeed_url()
    logger.info("No local %s; trying the deployed copy at %s", CURRENT_JSON_PATH.name, url)
    text = fetch_text(url)
    if text:
        try:
            current = parse_jsonfeed(text)
        except json.JSONDecodeError, KeyError, TypeError:
            logger.warning("Deployed %s could not be parsed; ignoring it", CURRENT_JSON_PATH.name)
        else:
            logger.info("Recovered %d current-year post(s) from Pages", len(current))
            return current

    logger.warning("No current-year state recovered; it will be re-scraped")
    return {}


def save_documents(posts: dict[str, Post]) -> None:
    """Write every published document: JSON state plus RFC 5005 Atom feeds."""
    part = partition(posts)
    DOCS_PATH.mkdir(parents=True, exist_ok=True)

    CURRENT_JSON_PATH.write_text(render_jsonfeed(part.current, feed_url=jsonfeed_url()))
    logger.info("Wrote %s (%d items)", CURRENT_JSON_PATH.name, len(part.current))

    for year in part.archive_years:
        path = DOCS_PATH / f"archive-{year}.json"
        rendered = render_jsonfeed(part.archives[year], feed_url=jsonfeed_archive_url(year))
        # Closed years are committed, and CI never commits. If one changes here
        # -- which normally means a --backfill turned up a post nobody had seen
        # -- the new file deploys but the next checkout silently reverts it, so
        # say so loudly rather than losing the post on the following run.
        if path.exists() and path.read_text() != rendered:
            logger.warning(
                "%s changed. Closed-year archives are committed state: commit this file, "
                "or the change is lost at the next checkout.",
                path.name,
            )
        path.write_text(rendered)
        logger.info("Wrote %s (%d items)", path.name, len(part.archives[year]))

    years = part.archive_years  # oldest first
    ATOM_PATH.write_text(
        render_atom(
            part.subscription,
            self_url=atom_url(),
            prev_archive_url=atom_archive_url(years[-1]) if years else None,
        )
    )
    logger.info("Wrote %s (%d entries)", ATOM_PATH.name, len(part.subscription))

    for i, year in enumerate(years):
        path = DOCS_PATH / f"archive-{year}.xml"
        path.write_text(
            render_atom(
                part.archives[year],
                self_url=atom_archive_url(year),
                archive=True,
                current_url=atom_url(),
                prev_archive_url=atom_archive_url(years[i - 1]) if i else None,
                # The newest archive has no next-archive: the document that
                # follows it is the subscription feed, which is not an archive.
                # rel="current" already points there from every archive.
                next_archive_url=atom_archive_url(years[i + 1]) if i + 1 < len(years) else None,
            )
        )
        logger.info("Wrote %s (%d entries)", path.name, len(part.archives[year]))


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
    parser = argparse.ArgumentParser(description="Update the claude.com/blog feeds")
    parser.add_argument(
        "--backfill", action="store_true", help="Fetch all listing pages (initial run)"
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Re-fetch every already-known post too, overwriting its stored data "
        "(use after a scraper change to backfill new fields onto old posts)",
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

    # Documents are written unconditionally: they are both the published feeds
    # and the persisted state, so a run that found nothing new still has to
    # reproduce them (the working tree may have started empty).
    save_documents(posts)
    logger.info("Saved %d total posts", len(posts))


if __name__ == "__main__":
    main()
