# ruff: noqa: E501
"""Curated public feeds. Fixed HTTPS URLs only: no user-supplied addresses, no DOCTYPE feeds."""

# id -> (name, url, category)
FEEDS = {
    "arxiv-ai": ("arXiv · Artificial Intelligence", "https://rss.arxiv.org/rss/cs.AI", "Research"),
    "arxiv-ml": ("arXiv · Machine Learning", "https://rss.arxiv.org/rss/cs.LG", "Research"),
    "mit-tech-review": (
        "MIT Technology Review",
        "https://www.technologyreview.com/feed/",
        "Technology",
    ),
    "bbc-tech": (
        "BBC · Technology",
        "https://feeds.bbci.co.uk/news/technology/rss.xml",
        "Technology",
    ),
    "verge": ("The Verge", "https://www.theverge.com/rss/index.xml", "Technology"),
    "ars-technica": (
        "Ars Technica",
        "https://feeds.arstechnica.com/arstechnica/index",
        "Technology",
    ),
    "nyt-tech": (
        "New York Times · Technology",
        "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml",
        "Technology",
    ),
    "hacker-news": ("Hacker News · Front page", "https://hnrss.org/frontpage", "Technology"),
    "bbc-world": ("BBC · World", "https://feeds.bbci.co.uk/news/world/rss.xml", "News"),
    "guardian-world": ("The Guardian · World", "https://www.theguardian.com/world/rss", "News"),
    "the-hindu": (
        "The Hindu · India",
        "https://www.thehindu.com/news/national/feeder/default.rss",
        "News",
    ),
    "npr": ("NPR · News", "https://feeds.npr.org/1001/rss.xml", "News"),
    "bbc-business": (
        "BBC · Business",
        "https://feeds.bbci.co.uk/news/business/rss.xml",
        "Business",
    ),
    "bbc-science": (
        "BBC · Science and Environment",
        "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
        "Science",
    ),
}
