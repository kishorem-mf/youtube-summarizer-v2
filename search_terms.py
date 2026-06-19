"""Search terms — loaded from search_terms.json if present, else hardcoded defaults.

Edit terms via the /terms UI instead of this file.
"""

import json
import os

_TERMS_FILE = os.path.join(os.path.dirname(__file__), "search_terms.json")

_DEFAULTS = {
    "groups": {
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
    },
    "group_context": {
        "Claude / Cowork": "",
        "Data / Tech": "data engineering",
        "Interoperability": "data catalog",
    },
}


def _load():
    if os.path.exists(_TERMS_FILE):
        with open(_TERMS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return _DEFAULTS


def save_terms(groups, group_context):
    with open(_TERMS_FILE, "w", encoding="utf-8") as f:
        json.dump({"groups": groups, "group_context": group_context}, f, indent=2, ensure_ascii=False)


def get_groups():
    return _load()["groups"]


def get_context():
    return _load()["group_context"]


# Legacy module-level names — kept for any direct imports; use get_groups() for live data.
SEARCH_GROUPS = _load()["groups"]
GROUP_CONTEXT  = _load()["group_context"]
TERM_TO_GROUP  = {term: g for g, terms in SEARCH_GROUPS.items() for term in terms}


def all_terms():
    return [t for terms in get_groups().values() for t in terms]


def build_query(term):
    """Return the search query: term + group context suffix."""
    data = _load()
    term_to_group = {t: g for g, terms in data["groups"].items() for t in terms}
    suffix = data["group_context"].get(term_to_group.get(term, ""), "")
    return f"{term} {suffix}".strip()
