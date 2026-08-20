"""Split the post corpus into the documents that get published.

Posts are bucketed by the calendar year of their `pub_date`. Years before the
current one are *closed*: no post can ever be added to them, so their documents
are immutable and are the ones committed to git. The current year is still
open, so its document is rebuilt on every run.

Two document sets are derived from the same buckets:

    docs/archive-YYYY.json   closed years, committed  -- canonical state
    docs/feed.json           current year, rebuilt    -- canonical state
    docs/archive-YYYY.xml    closed years             -- RFC 5005 archives
    docs/atom.xml            subscription document    -- RFC 5005 current

The JSON documents partition the corpus exactly: every post appears in exactly
one of them, so `archives + feed.json` round-trips the full state. The Atom
subscription document does *not* follow that rule -- it tops up from older
years to stay useful (see `partition`), so an entry can appear both in
atom.xml and in an archive. RFC 5005 tolerates that; clients dedupe on the
`atom:id` tag URI.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime

from .models import Post

# Floor for the subscription document. Bucketing by year means that on Jan 1
# the current year is empty, which would leave a new subscriber with nothing
# to read; topping up from the previous year avoids that cliff.
MIN_SUBSCRIPTION_ENTRIES = 20


def current_year(now: datetime | None = None) -> str:
    """The open year. Everything before it is frozen."""
    return str((now or datetime.now(UTC)).year)


def post_year(post: Post) -> str:
    """Calendar year of a post's pub_date, or "" if it has none."""
    return (post.get("pub_date") or "")[:4]


def _sort_key(post: Post) -> tuple[str, str]:
    # Tie-break on slug so ordering is total and stable: two posts sharing a
    # pub_date must not swap places between runs and dirty the output.
    return (post.get("pub_date") or "", post.get("slug") or "")


def _newest_first(posts: list[Post]) -> list[Post]:
    return sorted(posts, key=_sort_key, reverse=True)


@dataclass(frozen=True)
class Partition:
    """What each published document should contain."""

    subscription: list[Post]
    """Entries for atom.xml -- current year, topped up to MIN_SUBSCRIPTION_ENTRIES."""

    current: list[Post]
    """Entries for feed.json -- current year only, no top-up. State, so kept disjoint."""

    archives: dict[str, list[Post]]
    """year -> entries, for closed years only. Immutable once written."""

    @property
    def archive_years(self) -> list[str]:
        """Closed years, oldest first."""
        return sorted(self.archives)


def partition(
    posts: dict[str, Post] | list[Post],
    *,
    year: str | None = None,
    minimum: int = MIN_SUBSCRIPTION_ENTRIES,
) -> Partition:
    """Split posts into the current-year document and the closed-year archives.

    A post dated in the current year or later lands in the current bucket;
    anything older belongs to a closed year. Posts with no parseable pub_date
    are treated as current, since we cannot prove which year they froze in and
    an immutable archive is the wrong place to guess.
    """
    year = year or current_year()
    values = list(posts.values()) if isinstance(posts, dict) else list(posts)

    buckets: dict[str, list[Post]] = defaultdict(list)
    current: list[Post] = []
    for post in values:
        py = post_year(post)
        if not py or py >= year:
            current.append(post)
        else:
            buckets[py].append(post)

    current = _newest_first(current)

    subscription = list(current)
    if len(subscription) < minimum:
        older = _newest_first([p for ps in buckets.values() for p in ps])
        subscription.extend(older[: minimum - len(subscription)])

    archives = {y: _newest_first(ps) for y, ps in buckets.items()}
    return Partition(subscription=subscription, current=current, archives=archives)
