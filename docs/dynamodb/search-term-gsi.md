# GSI — search_term Index

## Purpose

Enable efficient lookup of all videos for a given topic (`search_term`),
combined with a `contains(tags, :tag)` filter for subtopic search.
Without this GSI, both filters require a full table scan.

---

## GSI Design

| Attribute | Role | Type |
|-----------|------|------|
| `search_term` | GSI Partition Key | String |
| `searched_on` | GSI Sort Key | String (ISO 8601 timestamp) |

- **Sort by `searched_on`** gives recency ordering within a topic for free.
- `searched_on` format: `2026-06-23T10:45:00Z` (UTC timestamp, set on every write).

---

## Create the GSI — AWS Console (2 minutes)

1. Open **AWS Console → DynamoDB → Tables → yt-summarizer-cache**
2. Click the **Indexes** tab
3. Click **Create index**
4. Fill in:
   - **Partition key:** `search_term` (String)
   - **Sort key:** `searched_on` (String)
   - **Index name:** `search_term-index`
   - **Attribute projections:** All (projects all attributes onto the index)
5. Click **Create index**
6. Wait ~2 minutes for status to change from `CREATING` to `ACTIVE`

---

## Create the GSI — AWS CLI

```bash
aws dynamodb update-table \
  --table-name yt-summarizer-cache \
  --attribute-definitions \
    AttributeName=search_term,AttributeType=S \
    AttributeName=searched_on,AttributeType=S \
  --global-secondary-index-updates '[{
    "Create": {
      "IndexName": "search_term-index",
      "KeySchema": [
        {"AttributeName": "search_term", "KeyType": "HASH"},
        {"AttributeName": "searched_on", "KeyType": "RANGE"}
      ],
      "Projection": {"ProjectionType": "ALL"},
      "BillingMode": "PAY_PER_REQUEST"
    }
  }]'
```

Check status:
```bash
aws dynamodb describe-table --table-name yt-summarizer-cache \
  --query "Table.GlobalSecondaryIndexes[?IndexName=='search_term-index'].IndexStatus"
```

---

## Query: search_term + tag filter

```python
from boto3.dynamodb.conditions import Key, Attr

def search_by_term_and_tag(table, search_term: str, tag: str, limit: int = 50):
    """Fast: GSI lookup by topic, filter expression by subtopic."""
    return table.query(
        IndexName="search_term-index",
        KeyConditionExpression=Key("search_term").eq(search_term),
        FilterExpression=Attr("tags").contains(tag),
        Limit=limit,
    ).get("Items", [])
```

## Query: tag only (no search_term known)

```python
def search_by_tag_only(table, tag: str, limit: int = 50):
    """Full scan with tag filter — acceptable at < 500 rows."""
    return table.scan(
        FilterExpression=Attr("tags").contains(tag),
        Limit=limit,
    ).get("Items", [])
```

---

## Note on existing rows

Existing rows have `searched_on` stored as a date string (`2026-06-23`).
New rows will store a full timestamp (`2026-06-23T10:45:00Z`).
Both are valid sort key values — DynamoDB sorts them lexicographically,
so old date-only rows will sort before same-day timestamp rows.
To normalise, run a one-time backfill or leave as-is (mixed format still sorts correctly).
