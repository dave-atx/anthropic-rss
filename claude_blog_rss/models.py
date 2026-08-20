"""Typed shape of a scraped blog post.

This is what jsonfeed.py serializes to and from: the published JSON Feed
documents in docs/ are the stored form, keyed by slug once loaded. `pub_date_precise` is only
present once `enrich_pub_dates` has upgraded a post's timestamp from the
sitemap's lastmod; all other fields are always present, set by
`scrape.fetch_post`.
"""

from typing import NotRequired, TypedDict


class Post(TypedDict):
    slug: str
    url: str
    title: str
    date_str: str
    pub_date: str
    categories: list[str]
    authors: list[str]
    summary: str
    image_url: str
    html_body: str
    pub_date_precise: NotRequired[bool]
