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
            "transcript": item.get("transcript", ""),
            "summary":    item.get("summary", ""),
            "source":     item.get("source", ""),
            "wordCount":  int(item.get("word_count", 0)),
            "language":   item.get("language", ""),
            "error":      "",
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


def save_result(video, detail, result):
    """Upsert a summary result into DynamoDB. Safe to call from a background thread."""
    if result.get("error") or not result.get("summary"):
        return
    table = _dynamo_table()
    try:
        table.put_item(Item={
            "video_id":    video.get("id", ""),
            "detail":      detail,
            "title":       video.get("title", ""),
            "channel":     video.get("channel", ""),
            "views":       video.get("views", 0),
            "date":        video.get("date", ""),
            "duration":    video.get("duration", ""),
            "url":         video.get("url", ""),
            "searched_on": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "transcript":  result.get("transcript", ""),
            "summary":     result.get("summary", ""),
            "source":      result.get("source", ""),
            "word_count":  result.get("wordCount", 0),
            "language":    result.get("language", ""),
            "source_type": "llm",
            "usage_count": 1,
            "user_id":     str(uuid.uuid4()),
            "search_term": result.get("search_term", ""),
            "tags":        result.get("tags", []),
        })
    except Exception as e:
        print(f"[storage] save_result error: {e}")
