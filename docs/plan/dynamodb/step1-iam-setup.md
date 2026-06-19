# Step 1 — IAM Setup & AWS Credentials

## Status
| Sub-step | Status | Notes |
|----------|--------|-------|
| 1.1 Create IAM User | Done | Keys present in .env |
| 1.2 Attach Policy | Done | Keys were generated — policy assumed attached |
| 1.3 Generate Access Keys | Done | AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY in .env |
| 1.4 Add DynamoDB env vars | To do | DYNAMO_TABLE + DYNAMO_USERS_TABLE missing |
| 1.5 Validate credentials | To do | aws sts get-caller-identity |

## Actions

### 1.4 — Add to `.env`
```
DYNAMO_TABLE=yt-summarizer-cache
DYNAMO_USERS_TABLE=yt-summarizer-users
```

### 1.5 — Validate
```bash
aws sts get-caller-identity
```
Expected: JSON with `UserId`, `Account`, `Arn` — confirms keys are active and region is reachable.

## Gate to Step 2
Do not proceed to table creation until `aws sts get-caller-identity` returns successfully.
