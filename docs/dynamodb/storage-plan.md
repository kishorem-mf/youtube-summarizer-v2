# Plan: Persistent Storage — DynamoDB

## Context
Currently every "Get Summary" click re-fetches the transcript and re-calls the LLM. There is no persistence. This adds latency and burns API credits on repeated views of the same video. The goal is to cache transcripts and summaries in DynamoDB so repeated requests are served from storage, not the LLM.

---

## Architecture

```
DynamoDB Table: yt-summarizer-cache
  PK: video_id (String)
  SK: detail (String)          ← "short" | "medium" | "long"
  Attributes:
    title, channel, views, date, duration, url,
    searched_on, word_count, language, source,
    transcript (full text), summary (full markdown),
    user_id,                   ← anonymous UUID today; real user ID when accounts added
    usage_count,               ← incremented on each cache hit (billing signal)
    source_type                ← "cache" | "llm" (tracks LLM spend per user)

DynamoDB Table: yt-summarizer-users  ← future monetisation, add when accounts land
  PK: user_id (String)
  Attributes:
    plan_tier,                 ← "free" | "pro" | "enterprise"
    created_on, email
```

No S3 needed — all content fits well within DynamoDB's 400KB item limit.

---

## Monetisation Flexibility (Future-Proofing)

Three design decisions made now to avoid a schema migration later:

1. **`user_id` on every cache item** — even anonymous users get a UUID. When accounts/subscriptions are introduced, replace the UUID with a real user ID. A GSI `user-index` (PK: `user_id`, SK: `searched_on`) enables "show user history" and "how many summaries has this user generated" queries with no redesign.

2. **`usage_count` + `source_type`** — increment `usage_count` on every hit; record whether result came from cache or LLM. This is the billing signal for any pay-per-use or credit model — you know exactly how many LLM calls each user triggered.

3. **`yt-summarizer-users` table with `plan_tier`** — a separate users table lets you gate features (summary length, history depth, recommendation access) per tier without touching the cache table schema.

---

## New Files

**`storage.py`** — single module for all DynamoDB operations:
- `check_cache(video_id, detail)` → returns cached `{transcript, summary, ...}` or `None`
- `save_result(video, detail, result)` → upserts item to DynamoDB
- `_dynamo_table()` — lazy-init client (reads env vars once)

---

## Modified Files

**`summarizer.py`** — no changes to core logic

**`app.py`** — `/video_summary` route (lines 148–168):
1. Before calling `fetch_and_summarize()`, call `storage.check_cache(video_id, detail)`
2. If cache hit → return cached result immediately (no LLM call)
3. If cache miss → call existing `fetch_and_summarize()` as today
4. After success → call `storage.save_result(video, detail, result)` in a background thread (non-blocking)

---

## DynamoDB Setup (one-time, manual)
```
aws dynamodb create-table \
  --table-name yt-summarizer-cache \
  --attribute-definitions AttributeName=video_id,AttributeType=S AttributeName=detail,AttributeType=S \
  --key-schema AttributeName=video_id,KeyType=HASH AttributeName=detail,KeyType=RANGE \
  --billing-mode PAY_PER_REQUEST \
  --region us-east-1
```

---

## New .env vars
```
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION=us-east-1
DYNAMO_TABLE=yt-summarizer-cache
```

---

## Packages to install
```
pip install boto3
```

---

## Key implementation detail — upsert
DynamoDB `put_item` overwrites the full item, so upsert is native — no delete-then-insert needed.

Cache lookup uses `get_item` with the composite key:
```python
table.get_item(Key={"video_id": video_id, "detail": detail})
```

---

## Cost
On-demand pricing: $0.25/million reads, $1.25/million writes. At 50 reads + 50 writes the cost is effectively $0.

---

## Verification
1. Search for a video, click "Get Summary" → summary appears (cache miss, saved to DynamoDB)
2. Click "Get Summary" again on same video/detail → summary returns instantly (cache hit, no LLM call)
3. Check DynamoDB in AWS Console → see item with transcript + summary stored
