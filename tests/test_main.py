"""Tests for main.py's pub_date time-of-day handling: discovery stamps and sitemap enrichment."""

from datetime import UTC, datetime

from claude_blog_rss.main import _preserve_pub_times, enrich_pub_dates, stamp_discovery_times


def _post(pub_date: str, precise: bool = False) -> dict:
    return {"slug": "x", "pub_date": pub_date, "pub_date_precise": precise}


def test_enrich_upgrades_same_day_match():
    posts = {"a": _post("2026-08-04T00:00:00+00:00")}
    lastmods = {"a": datetime(2026, 8, 4, 22, 48, 15, 722000, tzinfo=UTC)}

    updated = enrich_pub_dates(posts, lastmods)

    assert updated == 1
    assert posts["a"]["pub_date"] == "2026-08-04T22:48:15+00:00"
    assert posts["a"]["pub_date_precise"] is True


def test_enrich_leaves_different_day_untouched():
    posts = {"a": _post("2026-08-04T00:00:00+00:00")}
    lastmods = {"a": datetime(2026, 8, 10, 15, 40, 35, tzinfo=UTC)}

    updated = enrich_pub_dates(posts, lastmods)

    assert updated == 0
    assert posts["a"]["pub_date"] == "2026-08-04T00:00:00+00:00"
    assert "pub_date_precise" not in posts["a"] or posts["a"]["pub_date_precise"] is False


def test_enrich_skips_posts_missing_from_sitemap():
    posts = {"a": _post("2026-08-04T00:00:00+00:00")}

    updated = enrich_pub_dates(posts, {})

    assert updated == 0
    assert posts["a"]["pub_date"] == "2026-08-04T00:00:00+00:00"


def test_enrich_never_reverts_locked_in_precise_date():
    posts = {"a": _post("2026-08-04T22:48:15+00:00", precise=True)}
    # A later unrelated edit bumped lastmod to a different day.
    lastmods = {"a": datetime(2026, 8, 10, 9, 0, 0, tzinfo=UTC)}

    updated = enrich_pub_dates(posts, lastmods)

    assert updated == 0
    assert posts["a"]["pub_date"] == "2026-08-04T22:48:15+00:00"


def test_enrich_skips_posts_with_no_pub_date():
    posts = {"a": _post("")}

    updated = enrich_pub_dates(posts, {"a": datetime(2026, 8, 4, tzinfo=UTC)})

    assert updated == 0


# ── --refresh merge (preserving a locked-in precise date) ─────────────────────


def test_preserve_pub_times_survives_refetch():
    """--refresh re-fetches the page (midnight, no flag); the previously
    locked-in precise pub_date must carry over rather than being lost."""
    posts = {"a": _post("2026-08-04T22:48:15+00:00", precise=True)}
    refetched = {"a": _post("2026-08-04T00:00:00+00:00", precise=False)}

    _preserve_pub_times(posts, refetched)
    posts.update(refetched)

    assert posts["a"]["pub_date"] == "2026-08-04T22:48:15+00:00"
    assert posts["a"]["pub_date_precise"] is True


def test_preserve_pub_times_leaves_non_precise_refetch_alone():
    posts = {"a": _post("2026-08-04T00:00:00+00:00", precise=False)}
    refetched = {"a": _post("2026-08-04T00:00:00+00:00", precise=False)}

    _preserve_pub_times(posts, refetched)
    posts.update(refetched)

    assert posts["a"]["pub_date_precise"] is False


def test_preserve_pub_times_handles_new_slug_not_in_posts():
    posts = {}
    refetched = {"a": _post("2026-08-04T00:00:00+00:00", precise=False)}

    _preserve_pub_times(posts, refetched)  # should not raise

    assert refetched["a"]["pub_date_precise"] is False


def test_preserve_pub_times_keeps_same_day_discovery_time():
    posts = {"a": _post("2026-08-04T17:56:12+00:00")}
    refetched = {"a": _post("2026-08-04T00:00:00+00:00")}

    _preserve_pub_times(posts, refetched)

    assert refetched["a"]["pub_date"] == "2026-08-04T17:56:12+00:00"
    assert refetched["a"]["pub_date_precise"] is False


def test_preserve_pub_times_drops_discovery_time_when_page_date_changes():
    posts = {"a": _post("2026-08-04T17:56:12+00:00")}
    refetched = {"a": _post("2026-08-05T00:00:00+00:00")}

    _preserve_pub_times(posts, refetched)

    assert refetched["a"]["pub_date"] == "2026-08-05T00:00:00+00:00"


# ── discovery-time stamping ───────────────────────────────────────────────────


def test_stamp_uses_discovery_time_on_same_day():
    fetched = {"a": _post("2026-09-22T00:00:00+00:00")}

    stamped = stamp_discovery_times(fetched, datetime(2026, 9, 22, 17, 56, 57, 123, tzinfo=UTC))

    assert stamped == 1
    assert fetched["a"]["pub_date"] == "2026-09-22T17:56:57+00:00"
    # Not locked in: a same-day sitemap lastmod may still replace it.
    assert fetched["a"]["pub_date_precise"] is False


def test_stamp_skips_post_seen_on_a_later_day():
    fetched = {"a": _post("2026-09-21T00:00:00+00:00")}

    stamped = stamp_discovery_times(fetched, datetime(2026, 9, 22, 0, 30, tzinfo=UTC))

    assert stamped == 0
    assert fetched["a"]["pub_date"] == "2026-09-21T00:00:00+00:00"


def test_stamp_skips_missing_or_already_timed_pub_date():
    fetched = {"a": _post(""), "b": _post("2026-09-22T09:15:00+00:00")}

    stamped = stamp_discovery_times(fetched, datetime(2026, 9, 22, 17, 0, tzinfo=UTC))

    assert stamped == 0
    assert fetched["b"]["pub_date"] == "2026-09-22T09:15:00+00:00"


def test_enrich_replaces_discovery_time_with_sitemap_time():
    posts = {"a": _post("2026-09-22T17:56:57+00:00")}
    lastmods = {"a": datetime(2026, 9, 22, 17, 31, 4, tzinfo=UTC)}

    assert enrich_pub_dates(posts, lastmods) == 1
    assert posts["a"]["pub_date"] == "2026-09-22T17:31:04+00:00"
    assert posts["a"]["pub_date_precise"] is True
