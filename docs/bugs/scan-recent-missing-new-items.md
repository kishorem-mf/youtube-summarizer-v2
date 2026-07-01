# Bug: DB Explorer Q3 (Browse Recent) Missing Newly Created Items

## Symptom
Summaries created today did not appear in DB Explorer when using Q3 — Browse recent items.

## Root Cause
`scan_recent` passed `Limit=limit` directly to DynamoDB's `table.scan()`.
In DynamoDB, the `Limit` parameter caps the number of **items evaluated internally**
before the scan stops — it is not a filter on the most recent N items. Because
DynamoDB scans in internal partition order (not by `searched_on`), only the first
N items evaluated were returned, which could exclude recently added items entirely.

```python
# BROKEN — evaluates only `limit` items before stopping, misses newer items
resp = table.scan(Limit=limit)
items = sorted(resp.get("Items", []), key=lambda x: x.get("searched_on", ""), reverse=True)
```

## Fix
Paginate the full table scan, collect all items, sort by `searched_on` descending,
then trim to `limit` in Python — the same pattern already used by `by_topic`,
`by_tag`, and `by_platform`.

```python
# FIXED — fetches all items, sorts newest-first, trims to limit
scan_kwargs = {}
all_rows = []
while True:
    resp = table.scan(**scan_kwargs)
    all_rows.extend(resp.get("Items", []))
    lek = resp.get("LastEvaluatedKey")
    if not lek:
        break
    scan_kwargs["ExclusiveStartKey"] = lek
all_rows.sort(key=lambda x: x.get("searched_on", ""), reverse=True)
ctx.update(rows=all_rows[:limit], ...)
```

## File Changed
`app.py` — `dynamo_explorer()` route, `scan_recent` branch (~line 292)

## Date Fixed
2026-07-01
