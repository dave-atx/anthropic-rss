"""Tests for splitting the corpus into published documents."""

from claude_blog_rss.archives import (
    MIN_SUBSCRIPTION_ENTRIES,
    current_year,
    partition,
    post_year,
)


def make_post(slug: str, pub_date: str | None) -> dict:
    post = {
        "slug": slug,
        "url": f"https://claude.com/blog/{slug}",
        "title": slug.replace("-", " ").title(),
        "date_str": "",
        "pub_date": pub_date or "",
        "categories": [],
        "authors": [],
        "summary": "",
        "image_url": "",
        "html_body": f"<p>{slug}</p>",
    }
    return post


def make_year(year: int, count: int) -> list[dict]:
    """count posts in `year`, dated on consecutive days from Jan 1."""
    return [
        make_post(f"p{year}-{i:03d}", f"{year}-01-{i + 1:02d}T00:00:00+00:00") for i in range(count)
    ]


def test_current_year_reads_the_clock():
    from datetime import UTC, datetime

    assert current_year(datetime(2027, 3, 4, tzinfo=UTC)) == "2027"


def test_post_year_handles_missing_date():
    assert post_year(make_post("x", None)) == ""
    assert post_year(make_post("x", "2025-06-01T00:00:00+00:00")) == "2025"


def test_closed_years_become_archives_current_does_not():
    posts = make_year(2024, 3) + make_year(2025, 4) + make_year(2026, 25)
    p = partition(posts, year="2026")

    assert p.archive_years == ["2024", "2025"]
    assert len(p.archives["2024"]) == 3
    assert len(p.archives["2025"]) == 4
    assert len(p.current) == 25
    assert all(post_year(x) == "2026" for x in p.current)


def test_json_documents_partition_the_corpus_exactly():
    posts = make_year(2024, 3) + make_year(2025, 4) + make_year(2026, 25)
    p = partition(posts, year="2026")

    seen = [x["slug"] for x in p.current]
    for year_posts in p.archives.values():
        seen.extend(x["slug"] for x in year_posts)

    assert sorted(seen) == sorted(x["slug"] for x in posts)
    assert len(seen) == len(set(seen)), "a post must appear in exactly one JSON document"


def test_subscription_is_current_year_when_large_enough():
    posts = make_year(2025, 10) + make_year(2026, 25)
    p = partition(posts, year="2026")

    assert p.subscription == p.current
    assert len(p.subscription) == 25


def test_subscription_tops_up_from_older_years_in_january():
    posts = make_year(2025, 30) + make_year(2026, 2)
    p = partition(posts, year="2026")

    assert len(p.subscription) == MIN_SUBSCRIPTION_ENTRIES
    assert [x["slug"] for x in p.subscription[:2]] == [x["slug"] for x in p.current]
    # the padding is the newest of the closed years, not the oldest
    assert p.subscription[2]["slug"] == "p2025-029"
    # ...and topping up does not move those posts out of their archive
    assert len(p.archives["2025"]) == 30


def test_subscription_top_up_is_capped_by_what_exists():
    posts = make_year(2025, 3) + make_year(2026, 1)
    p = partition(posts, year="2026")
    assert len(p.subscription) == 4


def test_empty_corpus():
    p = partition([], year="2026")
    assert p.subscription == []
    assert p.current == []
    assert p.archives == {}
    assert p.archive_years == []


def test_accepts_dict_keyed_by_slug():
    posts = {x["slug"]: x for x in make_year(2026, 3)}
    p = partition(posts, year="2026")
    assert len(p.current) == 3


def test_posts_without_pub_date_are_treated_as_current():
    posts = [*make_year(2025, 2), make_post("undated", None)]
    p = partition(posts, year="2026")

    assert [x["slug"] for x in p.current] == ["undated"]
    assert "undated" not in [x["slug"] for x in p.archives["2025"]]


def test_future_dated_posts_stay_out_of_archives():
    posts = make_year(2027, 1) + make_year(2025, 1)
    p = partition(posts, year="2026")

    assert [x["slug"] for x in p.current] == ["p2027-000"]
    assert p.archive_years == ["2025"]


def test_ordering_is_newest_first_and_tie_broken_by_slug():
    same = "2026-05-01T00:00:00+00:00"
    posts = [
        make_post("bbb", same),
        make_post("aaa", same),
        make_post("ccc", "2026-06-01T00:00:00+00:00"),
    ]
    p = partition(posts, year="2026")

    assert [x["slug"] for x in p.current] == ["ccc", "bbb", "aaa"]


def test_ordering_is_stable_across_input_order():
    posts = make_year(2026, 5)
    forward = partition(posts, year="2026")
    backward = partition(list(reversed(posts)), year="2026")

    assert [x["slug"] for x in forward.current] == [x["slug"] for x in backward.current]
