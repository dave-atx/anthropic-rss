"""Tests for main.py's sitemap-based pub_date enrichment."""

from datetime import datetime, timezone

from claude_blog_rss.main import enrich_pub_dates, _preserve_precise_dates


def _post(pub_date: str, precise: bool = False) -> dict:
    return {"slug": "x", "pub_date": pub_date, "pub_date_precise": precise}


def test_enrich_upgrades_same_day_match():
    posts = {"a": _post("2026-08-04T00:00:00+00:00")}
    lastmods = {"a": datetime(2026, 8, 4, 22, 48, 15, 722000, tzinfo=timezone.utc)}

    updated = enrich_pub_dates(posts, lastmods)

    assert updated == 1
    assert posts["a"]["pub_date"] == "2026-08-04T22:48:15+00:00"
    assert posts["a"]["pub_date_precise"] is True


def test_enrich_leaves_different_day_untouched():
    posts = {"a": _post("2026-08-04T00:00:00+00:00")}
    lastmods = {"a": datetime(2026, 8, 10, 15, 40, 35, tzinfo=timezone.utc)}

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
    lastmods = {"a": datetime(2026, 8, 10, 9, 0, 0, tzinfo=timezone.utc)}

    updated = enrich_pub_dates(posts, lastmods)

    assert updated == 0
    assert posts["a"]["pub_date"] == "2026-08-04T22:48:15+00:00"


def test_enrich_skips_posts_with_no_pub_date():
    posts = {"a": _post("")}

    updated = enrich_pub_dates(posts, {"a": datetime(2026, 8, 4, tzinfo=timezone.utc)})

    assert updated == 0


# ── --refresh merge (preserving a locked-in precise date) ─────────────────────

def test_preserve_precise_dates_survives_refetch():
    """--refresh re-fetches the page (midnight, no flag); the previously
    locked-in precise pub_date must carry over rather than being lost."""
    posts = {"a": _post("2026-08-04T22:48:15+00:00", precise=True)}
    refetched = {"a": _post("2026-08-04T00:00:00+00:00", precise=False)}

    _preserve_precise_dates(posts, refetched)
    posts.update(refetched)

    assert posts["a"]["pub_date"] == "2026-08-04T22:48:15+00:00"
    assert posts["a"]["pub_date_precise"] is True


def test_preserve_precise_dates_leaves_non_precise_refetch_alone():
    posts = {"a": _post("2026-08-04T00:00:00+00:00", precise=False)}
    refetched = {"a": _post("2026-08-04T00:00:00+00:00", precise=False)}

    _preserve_precise_dates(posts, refetched)
    posts.update(refetched)

    assert posts["a"]["pub_date_precise"] is False


def test_preserve_precise_dates_handles_new_slug_not_in_posts():
    posts = {}
    refetched = {"a": _post("2026-08-04T00:00:00+00:00", precise=False)}

    _preserve_precise_dates(posts, refetched)  # should not raise

    assert refetched["a"]["pub_date_precise"] is False
