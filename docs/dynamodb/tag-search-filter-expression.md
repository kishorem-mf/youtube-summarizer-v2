# Tag Search — Filter Expression Approach (Chosen)

## Decision

Use DynamoDB's built-in `contains()` filter expression on the existing `tags` list
attribute in the main table. No new tables, no schema changes, no dual writes.

**Why chosen over fan-out:**
- Table size is small (< 500 videos) — scan cost is negligible
- Tag search is occasional, not a primary access pattern
- Fan-out complexity (second table, backfill, orphan management) adds no value at this scale

---

## How it works

DynamoDB cannot index a list attribute, so `contains()` filters after reading.
Always narrow with a key condition or GSI first to keep the scanned set small.

## Code example

```python
from boto3.dynamodb.conditions import Key, Attr

def search_by_tag(table, tag: str, search_term: str = None, limit: int = 50):
    """
    Search videos by tag value inside the tags list.
    Optionally narrow by search_term first to avoid a full table scan.
    """
    filter_expr = Attr("tags").contains(tag)

    if search_term:
        # Narrow to one topic first, then filter tags — scans 20-50 rows, not full table
        return table.query(
            IndexName="search_term-index",
            KeyConditionExpression=Key("search_term").eq(search_term),
            FilterExpression=filter_expr,
            Limit=limit,
        ).get("Items", [])

    # Full table scan — acceptable at < 500 rows
    return table.scan(FilterExpression=filter_expr, Limit=limit).get("Items", [])
```

## Multi-tag OR query

```python
from functools import reduce
from boto3.dynamodb.conditions import Attr

def search_by_tags_or(table, tags: list[str], limit: int = 50):
    """Match items containing ANY of the given tags."""
    filter_expr = reduce(lambda a, b: a | b, [Attr("tags").contains(t) for t in tags])
    return table.scan(FilterExpression=filter_expr, Limit=limit).get("Items", [])
```

## Trade-offs

| Pro | Con |
|-----|-----|
| Zero schema or infrastructure changes | Full scan if no GSI to narrow first |
| All 6 tags searchable equally | Gets slower as table grows beyond ~10k rows |
| No dual writes, no orphan rows | DynamoDB charges per read unit scanned, not matched |
| Works today against existing data | Not suitable as primary access pattern at scale |

## Upgrade path

If tag search becomes a primary access pattern or table exceeds 10k rows,
migrate to a dedicated `yt-tag-index` table using the fan-out pattern
(one row per tag per video). The filter expression approach can run in
parallel during any transition period.
