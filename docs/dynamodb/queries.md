# DynamoDB Query Patterns — yt-summarizer-cache

## Table Key Design

### yt-summarizer-cache
| Key | Type | Purpose |
|-----|------|---------|
| PK  | `video_id` (String) | Unique YouTube video ID |
| SK  | `detail` (String) | Summary level: `short` / `medium` / `long` |
| Attr | `user_id` (String) | Anonymous UUID today; real user ID when accounts land |
| Attr | `usage_count` (Number) | Cache hit counter — billing signal |
| Attr | `source_type` (String) | `"cache"` or `"llm"` — tracks LLM spend per user |

### yt-summarizer-users *(add when accounts land)*
| Key | Type | Purpose |
|-----|------|---------|
| PK  | `user_id` (String) | User identifier |
| Attr | `plan_tier` (String) | `"free"` / `"pro"` / `"enterprise"` |
| Attr | `created_on`, `email` | Standard user metadata |

---

## Core Queries (Today)

### Q1 — Cache Lookup (most frequent)
> "Has this video been summarized at this detail level?"
```python
table.get_item(Key={"video_id": "abc123", "detail": "short"})
```
**Access pattern:** PK + SK exact match → single-digit ms, no GSI needed.

---

### Q2 — Get All Detail Levels for a Video
> "Show all cached summaries (short/medium/long) for a given video"
```python
table.query(KeyConditionExpression=Key("video_id").eq("abc123"))
```
**Access pattern:** PK only → returns up to 3 items (one per detail level).

---

### Q3 — Upsert / Save Result
> "Store or overwrite a summary after LLM call"
```python
table.put_item(Item={...})
```
**Access pattern:** PK + SK write → native overwrite, no extra logic needed.

---

### Q4 — Delete Cache Entry
> "Invalidate a stale or incorrect summary"
```python
table.delete_item(Key={"video_id": "abc123", "detail": "short"})
```
**Access pattern:** PK + SK exact delete.

---

## Secondary Queries (Need GSI)

### Q5 — All Videos by Channel
> "Show everything cached from a given YouTube channel"
- **GSI:** `channel-index` → PK: `channel`, SK: `searched_on`
```python
table.query(IndexName="channel-index", KeyConditionExpression=Key("channel").eq("Fireship"))
```

---

### Q6 — Recently Searched Videos
> "What have users looked up in the last 7 days?" (admin/analytics)
- **GSI:** `searched_on-index` → PK: `searched_on` (date string `YYYY-MM-DD`)
```python
table.query(IndexName="searched_on-index", KeyConditionExpression=Key("searched_on").eq("2026-06-19"))
```

---

### Q7 — Videos by Language
> "Filter cached summaries by transcript language"
- **GSI:** `language-index` → PK: `language`
```python
table.query(IndexName="language-index", KeyConditionExpression=Key("language").eq("en"))
```

---

## Monetisation Queries (Need yt-summarizer-users table + user_id GSI)

### Q10 — All Summaries by User
> "Show history for a logged-in user"
- **GSI:** `user-index` → PK: `user_id`, SK: `searched_on`
```python
table.query(IndexName="user-index", KeyConditionExpression=Key("user_id").eq("u-xyz"))
```

---

### Q11 — LLM Usage Count per User
> "How many LLM calls has this user triggered?" (billing/credit model)
```python
# Filter on source_type="llm" via FilterExpression on user-index query
table.query(IndexName="user-index",
    KeyConditionExpression=Key("user_id").eq("u-xyz"),
    FilterExpression=Attr("source_type").eq("llm"))
```

---

### Q12 — User Plan Tier Lookup
> "Is this user on free or pro?" (feature gating)
```python
users_table.get_item(Key={"user_id": "u-xyz"})
```
**Access pattern:** PK exact match on `yt-summarizer-users` table.

---

## Future Queries (Recommendation Layer)

### Q8 — Videos by Tag/Keyword *(requires `tags` attribute as StringSet)*
> "Find other cached videos with the same hashtag or keyword"
- **GSI:** `tag-index` → requires flattening tags into a separate lookup table or using FilterExpression (costly scan)
- **Better approach:** offload to OpenSearch/vector DB when this requirement arrives

---

### Q9 — Similar Videos by Channel + Recent
> "Recommend videos from same channel, most recently cached"
- **GSI:** `channel-index` with SK `searched_on` sorted descending → already covered by Q5 GSI with `ScanIndexForward=False`

---

## Query Summary Table

| # | Query | Access Method | GSI Required |
|---|-------|---------------|--------------|
| Q1 | Cache lookup by video + detail | PK + SK | No |
| Q2 | All detail levels for a video | PK only | No |
| Q3 | Save/upsert summary | put_item | No |
| Q4 | Delete cache entry | delete_item | No |
| Q5 | All videos by channel | GSI | Yes — `channel-index` |
| Q6 | Recently searched videos | GSI | Yes — `searched_on-index` |
| Q7 | Videos by language | GSI | Yes — `language-index` |
| Q8 | Videos by tag/keyword | External search | Avoid scan |
| Q9 | Recommendations by channel + recency | GSI (Q5 reuse) | Reuses `channel-index` |
| Q10 | All summaries by user | GSI | Yes — `user-index` |
| Q11 | LLM call count per user | GSI + filter | Yes — `user-index` |
| Q12 | User plan tier lookup | PK exact match | No (users table) |

---

## Notes
- Q1–Q4 are zero-cost to add — no GSI, uses base table keys
- Each GSI adds ~same write cost as base table (doubles writes for that attribute)
- Q8 (tag similarity) should **not** be solved with DynamoDB — use a vector store when needed
- Avoid `scan` on the full table in production; always prefer `query` with a key condition
