"""Typed shape of a scraped blog post.

This is what jsonfeed.py serializes to and from: the published JSON Feed
documents in docs/ are the stored form, keyed by slug once loaded. `pub_date`
is midnight UTC on the page's date until `main.stamp_discovery_times` or
`enrich_pub_dates` gives it a time of day. `pub_date_precise` is only present
once a post has a real publish time: from the sitemap's lastmod via
`enrich_pub_dates`, or from an off-site page that publishes one. All other
fields are always present, set by `scrape.fetch_post`. `url` is where the post
lives, which for a cross-post is claude.dev or anthropic.com.
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
