import os
import datetime
import uuid
import boto3

_table_client = None


def _dynamo_table():
    global _table_client
    if _table_client is None:
        ca_bundle = os.getenv("AWS_CA_BUNDLE")
        dynamo = boto3.resource(
            "dynamodb",
            region_name=os.getenv("AWS_REGION", "us-east-1"),
            verify=ca_bundle if ca_bundle else True,
        )
        _table_client = dynamo.Table(os.getenv("DYNAMO_TABLE", "yt-summarizer-cache"))
    return _table_client


def check_cache(video_id, detail):
    """Return cached result dict or None on miss."""
    table = _dynamo_table()
    try:
        resp = table.get_item(Key={"video_id": video_id, "detail": detail})
        item = resp.get("Item")
        if not item:
            return None
        table.update_item(
            Key={"video_id": video_id, "detail": detail},
            UpdateExpression="SET usage_count = usage_count + :inc, source_type = :st",
            ExpressionAttributeValues={":inc": 1, ":st": "cache"},
        )
        return {
            "transcript":      item.get("transcript", ""),
            "summary":         item.get("summary", ""),
            "source":          item.get("fetch_method") or item.get("source", ""),
            "wordCount":       int(item.get("word_count", 0)),
            "language":        item.get("language", ""),
            "tags":            list(item.get("tags", [])),
            "questions":       list(item.get("questions", [])),
            "source_platform": item.get("source_platform", "youtube"),
            "error":           "",
        }
    except Exception as e:
        print(f"[storage] check_cache error: {e}")
        return None


def create_search_term_gsi():
    """Create the search_term-index GSI on the main table if it does not exist.
    Blocks until the index becomes ACTIVE. Safe to call multiple times."""
    ca_bundle  = os.getenv("AWS_CA_BUNDLE")
    region     = os.getenv("AWS_REGION", "us-east-1")
    table_name = os.getenv("DYNAMO_TABLE", "yt-summarizer-cache")
    client     = boto3.client(
        "dynamodb",
        region_name=region,
        verify=ca_bundle if ca_bundle else True,
    )

    # Check if GSI already exists
    desc    = client.describe_table(TableName=table_name)
    indexes = [i["IndexName"] for i in desc["Table"].get("GlobalSecondaryIndexes", [])]
    if "search_term-index" in indexes:
        print("[storage] search_term-index already exists — nothing to do.")
        return

    print("[storage] creating search_term-index GSI…")
    client.update_table(
        TableName=table_name,
        AttributeDefinitions=[
            {"AttributeName": "search_term", "AttributeType": "S"},
            {"AttributeName": "searched_on", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexUpdates=[{
            "Create": {
                "IndexName": "search_term-index",
                "KeySchema": [
                    {"AttributeName": "search_term", "KeyType": "HASH"},
                    {"AttributeName": "searched_on", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
        }],
    )

    # Wait for ACTIVE
    import time
    while True:
        desc    = client.describe_table(TableName=table_name)
        indexes = {i["IndexName"]: i["IndexStatus"]
                   for i in desc["Table"].get("GlobalSecondaryIndexes", [])}
        status  = indexes.get("search_term-index", "UNKNOWN")
        print(f"[storage] GSI status: {status}")
        if status == "ACTIVE":
            print("[storage] search_term-index is ACTIVE.")
            break
        time.sleep(5)


def save_result(video, detail, result, *, source_platform="youtube", content_type="video"):
    """Upsert a summary result into DynamoDB. Safe to call from a background thread."""
    if result.get("error") or not result.get("summary"):
        return
    table = _dynamo_table()
    try:
        item = {
            "video_id":        video.get("id", ""),
            "detail":          detail,
            "title":           video.get("title", ""),
            "channel":         video.get("channel", ""),
            "views":           video.get("views", 0),
            "date":            video.get("date", ""),
            "duration":        video.get("duration", ""),
            "url":             video.get("url", ""),
            "searched_on":     datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "transcript":      result.get("transcript", ""),
            "summary":         result.get("summary", ""),
            "fetch_method":    result.get("source", ""),
            "word_count":      result.get("wordCount", 0),
            "language":        result.get("language", ""),
            "source_type":     "llm",
            "source_platform": source_platform,
            "content_type":    content_type,
            "usage_count":     1,
            "user_id":         str(uuid.uuid4()),
            "search_term":     result.get("search_term", ""),
            "tags":            result.get("tags", []),
            "questions":       result.get("questions", []),
        }
        # Sparse platform-specific fields
        if source_platform == "google_search":
            if video.get("domain"):
                item["domain"] = video["domain"]
            if video.get("author"):
                item["author"] = video["author"]
        elif source_platform == "linkedin_post":
            if video.get("author"):
                item["author"] = video["author"]
            elif video.get("channel"):
                item["author"] = video["channel"]
            if video.get("headline"):
                item["headline"] = video["headline"]
        table.put_item(Item=item)
    except Exception as e:
        print(f"[storage] save_result error: {e}")


def create_source_platform_gsi():
    """Create the source_platform-index GSI on the main table if it does not exist.
    Blocks until the index becomes ACTIVE. Safe to call multiple times."""
    import time
    ca_bundle  = os.getenv("AWS_CA_BUNDLE")
    region     = os.getenv("AWS_REGION", "us-east-1")
    table_name = os.getenv("DYNAMO_TABLE", "yt-summarizer-cache")
    client     = boto3.client(
        "dynamodb",
        region_name=region,
        verify=ca_bundle if ca_bundle else True,
    )

    desc    = client.describe_table(TableName=table_name)
    indexes = [i["IndexName"] for i in desc["Table"].get("GlobalSecondaryIndexes", [])]
    if "source_platform-index" in indexes:
        print("[storage] source_platform-index already exists — nothing to do.")
        return

    print("[storage] creating source_platform-index GSI…")
    client.update_table(
        TableName=table_name,
        AttributeDefinitions=[
            {"AttributeName": "source_platform", "AttributeType": "S"},
            {"AttributeName": "searched_on",     "AttributeType": "S"},
        ],
        GlobalSecondaryIndexUpdates=[{
            "Create": {
                "IndexName": "source_platform-index",
                "KeySchema": [
                    {"AttributeName": "source_platform", "KeyType": "HASH"},
                    {"AttributeName": "searched_on",     "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
        }],
    )

    while True:
        desc    = client.describe_table(TableName=table_name)
        indexes = {i["IndexName"]: i["IndexStatus"]
                   for i in desc["Table"].get("GlobalSecondaryIndexes", [])}
        status  = indexes.get("source_platform-index", "UNKNOWN")
        print(f"[storage] source_platform-index GSI status: {status}")
        if status == "ACTIVE":
            print("[storage] source_platform-index is ACTIVE.")
            break
        time.sleep(5)


def backfill_source_platform(dry_run=False):
    """Set source_platform='youtube' and content_type='video' on all existing rows
    that are missing these fields. Additive only — never overwrites existing values."""
    table   = _dynamo_table()
    updated = 0
    scanned = 0
    last    = None
    while True:
        kwargs = {"ProjectionExpression": "video_id, detail, source_platform"}
        if last:
            kwargs["ExclusiveStartKey"] = last
        resp  = table.scan(**kwargs)
        items = resp.get("Items", [])
        scanned += len(items)
        for item in items:
            if item.get("source_platform"):
                continue
            if not dry_run:
                table.update_item(
                    Key={"video_id": item["video_id"], "detail": item["detail"]},
                    UpdateExpression="SET source_platform = :sp, content_type = :ct",
                    ConditionExpression="attribute_not_exists(source_platform)",
                    ExpressionAttributeValues={":sp": "youtube", ":ct": "video"},
                )
            updated += 1
        last = resp.get("LastEvaluatedKey")
        if not last:
            break
    action = "Would update" if dry_run else "Updated"
    print(f"[storage] backfill_source_platform: scanned={scanned}, {action}={updated}")
    return updated
