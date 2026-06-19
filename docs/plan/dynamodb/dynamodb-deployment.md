# DynamoDB Deployment Plan — yt-summarizer-v2

## Overview
5-step deployment of DynamoDB caching for the YouTube Summarizer app.
Each step has 5 sub-steps with explicit validation gates before proceeding.

---

## Step 1 — IAM Setup & AWS Credentials

### 1.1 Create IAM User
- Go to AWS Console → IAM → Users → Create User
- Name: `yt-summarizer-dynamo`
- Access type: Programmatic access only (no console login needed)

### 1.2 Attach Policy
- Attach managed policy: `AmazonDynamoDBFullAccess`
- Scope note: narrow to a custom policy with only `GetItem`, `PutItem`, `DeleteItem`, `Query`, `UpdateItem` on specific table ARNs before going to production

### 1.3 Generate Access Keys
- IAM → User → Security credentials → Create access key
- Choose: Application running outside AWS
- Download the `.csv` immediately — secret key is shown only once

### 1.4 Add Keys to `.env`
```
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION=us-east-1
DYNAMO_TABLE=yt-summarizer-cache
DYNAMO_USERS_TABLE=yt-summarizer-users
```

### 1.5 Validate
```bash
aws sts get-caller-identity
```
Expected: JSON response with `UserId`, `Account`, `Arn` — confirms credentials are valid and active.

---

## Step 2 — Create DynamoDB Tables

### 2.1 Create Cache Table
```bash
aws dynamodb create-table \
  --table-name yt-summarizer-cache \
  --attribute-definitions \
      AttributeName=video_id,AttributeType=S \
      AttributeName=detail,AttributeType=S \
  --key-schema \
      AttributeName=video_id,KeyType=HASH \
      AttributeName=detail,KeyType=RANGE \
  --billing-mode PAY_PER_REQUEST \
  --region us-east-1
```

### 2.2 Create Users Table *(future monetisation — create now, use later)*
```bash
aws dynamodb create-table \
  --table-name yt-summarizer-users \
  --attribute-definitions AttributeName=user_id,AttributeType=S \
  --key-schema AttributeName=user_id,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region us-east-1
```

### 2.3 Enable TTL on Cache Table *(optional but recommended)*
```bash
aws dynamodb update-time-to-live \
  --table-name yt-summarizer-cache \
  --time-to-live-specification Enabled=true,AttributeName=expires_at
```
Add `expires_at` (epoch seconds) to each item to auto-expire stale cache entries.

### 2.4 Add Tags for Cost Tracking
```bash
aws dynamodb tag-resource \
  --resource-arn <table-arn> \
  --tags Key=project,Value=yt-summarizer Key=env,Value=dev
```

### 2.5 Validate
```bash
aws dynamodb describe-table --table-name yt-summarizer-cache --query "Table.TableStatus"
aws dynamodb describe-table --table-name yt-summarizer-users --query "Table.TableStatus"
```
Expected: both return `"ACTIVE"` (may take 10–30 seconds after creation).

---

## Step 3 — Create GSIs *(SKIPPED — deferred)*

> **Skipped intentionally.** Only basic queries (Q1–Q4) are needed for the initial cache implementation. GSIs can be added later via `update-table` with zero downtime when secondary query patterns (channel, language, user history) are required.

### 3.1 Add `channel-index`
```bash
aws dynamodb update-table \
  --table-name yt-summarizer-cache \
  --attribute-definitions \
      AttributeName=channel,AttributeType=S \
      AttributeName=searched_on,AttributeType=S \
  --global-secondary-index-updates '[{
    "Create": {
      "IndexName": "channel-index",
      "KeySchema": [
        {"AttributeName":"channel","KeyType":"HASH"},
        {"AttributeName":"searched_on","KeyType":"RANGE"}
      ],
      "Projection": {"ProjectionType":"ALL"}
    }
  }]'
```

### 3.2 Add `language-index`
```bash
aws dynamodb update-table \
  --table-name yt-summarizer-cache \
  --attribute-definitions AttributeName=language,AttributeType=S \
  --global-secondary-index-updates '[{
    "Create": {
      "IndexName": "language-index",
      "KeySchema": [{"AttributeName":"language","KeyType":"HASH"}],
      "Projection": {"ProjectionType":"ALL"}
    }
  }]'
```

### 3.3 Add `user-index`
```bash
aws dynamodb update-table \
  --table-name yt-summarizer-cache \
  --attribute-definitions \
      AttributeName=user_id,AttributeType=S \
      AttributeName=searched_on,AttributeType=S \
  --global-secondary-index-updates '[{
    "Create": {
      "IndexName": "user-index",
      "KeySchema": [
        {"AttributeName":"user_id","KeyType":"HASH"},
        {"AttributeName":"searched_on","KeyType":"RANGE"}
      ],
      "Projection": {"ProjectionType":"ALL"}
    }
  }]'
```

### 3.4 Add `searched_on-index`
```bash
aws dynamodb update-table \
  --table-name yt-summarizer-cache \
  --attribute-definitions AttributeName=searched_on,AttributeType=S \
  --global-secondary-index-updates '[{
    "Create": {
      "IndexName": "searched_on-index",
      "KeySchema": [{"AttributeName":"searched_on","KeyType":"HASH"}],
      "Projection": {"ProjectionType":"KEYS_ONLY"}
    }
  }]'
```

### 3.5 Validate
```bash
aws dynamodb describe-table --table-name yt-summarizer-cache \
  --query "Table.GlobalSecondaryIndexes[*].{Name:IndexName,Status:IndexStatus}"
```
Expected: all 4 GSIs show `"ACTIVE"`. GSI creation can take 1–5 minutes — re-run until all are active before proceeding.

---

## Step 4 — Integrate `storage.py` into the App

### 4.1 Install boto3
```bash
pip install boto3
pip freeze > requirements.txt
```

### 4.2 Create `storage.py`
Implement three functions:
- `_dynamo_table()` — lazy boto3 client, reads `DYNAMO_TABLE` from env
- `check_cache(video_id, detail)` → calls `get_item`, returns item dict or `None`
- `save_result(video, detail, result)` → calls `put_item` with all attributes including `user_id` (anonymous UUID), `source_type="llm"`, `usage_count=1`, `searched_on=today`

### 4.3 Wire into `app.py` — `/video_summary` route
```python
# Before fetch_and_summarize():
cached = storage.check_cache(video_id, detail)
if cached:
    return jsonify(cached)

# After fetch_and_summarize() succeeds:
threading.Thread(target=storage.save_result, args=(video, detail, result), daemon=True).start()
```

### 4.4 Add `usage_count` increment on cache hit
```python
# In check_cache(), after confirming hit:
table.update_item(
    Key={"video_id": video_id, "detail": detail},
    UpdateExpression="SET usage_count = usage_count + :inc",
    ExpressionAttributeValues={":inc": 1}
)
```

### 4.5 Validate Integration
```bash
python -c "from storage import _dynamo_table; t = _dynamo_table(); print('Connected:', t.table_name)"
```
Expected: `Connected: yt-summarizer-cache` — confirms boto3 client initialises, env vars are loaded, and the table exists.

---

## Step 5 — End-to-End Test & Verification

### 5.1 Cache Miss Test
- Start the app: `python app.py`
- Search for a YouTube video and click "Get Summary"
- Observe: summary is generated (LLM called), response takes normal time (~5–15s)

### 5.2 Verify Item in DynamoDB Console
- AWS Console → DynamoDB → Tables → `yt-summarizer-cache` → Explore items
- Confirm item exists with correct `video_id`, `detail`, `title`, `summary`, `source_type="llm"`, `usage_count=1`

### 5.3 Cache Hit Test
- Click "Get Summary" again on the same video with the same detail level
- Observe: response is instant (<200ms), no LLM call made
- Confirm: `source_type` in the returned item is `"cache"` (or check app logs)

### 5.4 Validate `usage_count` Increment
- In DynamoDB Console, re-check the same item
- Confirm: `usage_count` is now `2` after the second request

### 5.5 GSI Query Test
```bash
aws dynamodb query \
  --table-name yt-summarizer-cache \
  --index-name channel-index \
  --key-condition-expression "channel = :ch" \
  --expression-attribute-values '{":ch": {"S": "<channel-name>"}}'
```
Expected: returns the cached item(s) for that channel, confirming GSIs are queryable.

---

## Summary

| Step | Focus | Key Validation |
|------|-------|----------------|
| 1 | IAM + credentials | `aws sts get-caller-identity` |
| 2 | Create tables | Both tables `ACTIVE` |
| 3 | Create GSIs | **SKIPPED** — deferred until secondary queries needed |
| 4 | App integration | boto3 connects, `put_item`/`get_item` work |
| 5 | End-to-end | Cache miss → save → cache hit → usage_count increments |

**Do not proceed to the next step until the validation for the current step passes.**
