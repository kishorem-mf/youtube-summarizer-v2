"""Predefined search terms, taken verbatim from yt-summarizer.md.

Grouped exactly as in the source document so the digest mirrors the
existing daily watch workflow.
"""

SEARCH_GROUPS = {
    "Claude / Cowork": [
        "Claude for Business",
        "Claude for Small Business",
        "Claude Co-work for Business",
        "Claude Cowork for Small Business",
    ],
    "Data / Tech": [
        "s3 tables",
        "iceberg tables",
        "metadata driven framework",
        "observability",
        "aws lambda",
        "duckdb",
        "snowflake",
        "databricks",
        "microsoft fabric",
        "azure functions",
        "data ingestion",
        "scrape data",
    ],
    "Interoperability": [
        "data interoperability",
        "polaris catalog",
    ],
}


# Short topic context appended to each term in a group so literal keyword
# searches stay on-topic. "snowflake" alone returns kids' songs; with the
# "data engineering" suffix it returns the data platform.
GROUP_CONTEXT = {
    "Claude / Cowork": "",          # already specific, no suffix needed
    "Data / Tech": "data engineering",
    "Interoperability": "data catalog",
}

# Reverse lookup: display term -> group name.
TERM_TO_GROUP = {
    term: group for group, terms in SEARCH_GROUPS.items() for term in terms
}


def all_terms():
    terms = []
    for group in SEARCH_GROUPS.values():
        terms.extend(group)
    return terms


def build_query(term):
    """Return the actual search query for a display term: the term plus its
    group's topic context. Unknown / custom terms are returned unchanged.
    """
    suffix = GROUP_CONTEXT.get(TERM_TO_GROUP.get(term, ""), "")
    return f"{term} {suffix}".strip()
