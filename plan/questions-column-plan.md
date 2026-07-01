# Questions Column — LLM-Generated Questions per Content Item

## Goal
For every summarised item (YouTube video, LinkedIn post, Google article, and
any future content type), generate 5 questions that the content answers.
Store them in DynamoDB. Designed to serve as the primary input for a future
vector similarity search ("find articles that answer similar questions").

---

## Recommended DynamoDB Datatype: List (L)

| Option | Verdict |
|--------|---------|
| **List `L` of strings** | **Recommended** — same type as `tags`, preserves order, allows `[]` default, no uniqueness constraint |
| String Set `SS` | Rejected — requires all values unique, no guaranteed order |
| Single string (newline-joined) | Rejected — loses structure, harder to embed individually later |
| JSON string | Rejected — extra serialisation, DynamoDB can't filter on contents |

**Why List is right for the vector store use case:**
- Can join all 5 questions into one string for a single embedding (`" ".join(questions)`)
- Can embed each question separately for multi-vector search (5 vectors per item)
- DynamoDB retrieval is identical to `tags` — already handled in `check_cache()` and `save_result()`
- No GSI or schema migration required — DynamoDB is schemaless for non-key attributes

---

## What the 5 Questions Should Be

Generated from the **summary** (not the raw transcript) — same input as `_generate_tags()`.

Prompt intent: questions a researcher would type into a search engine when
looking for this content. This maximises semantic overlap with future queries
and between similar articles.

Example output for a dbt article:
```
1. How does dbt handle incremental models in Snowflake?
2. What is the difference between dbt Core and dbt Cloud?
3. How do you manage data lineage with dbt?
4. What testing strategies does dbt support out of the box?
5. How does dbt integrate with a modern data stack?
```

---

## Pipeline Change (Identical Pattern in All 3 Modules)

All three modules (`summarizer.py`, `linkedin_search.py`, `google_search.py`)
follow the same pattern. The change is identical in each:

### Step 1 — Add `_generate_questions(summary)` function

Same structure as the existing `_generate_tags()` in each module:

```python
def _generate_questions(summary: str) -> list:
    if not summary or summary.startswith("_"):
        return []
    try:
        resp = _anthropic.messages.create(
            model=_MODEL,           # same model constant as _generate_tags in each file
            system="You are a research assistant. Return only a numbered list of 5 questions, nothing else.",
            messages=[{"role": "user", "content": (
                f"Generate exactly 5 questions that this content directly answers. "
                f"Write questions a researcher would search for. Be specific.\n\n"
                f"{summary[:1500]}"
            )}],
            max_tokens=200,
        )
        raw = resp.content[0].text.strip()
        # Parse "1. Q\n2. Q\n..." → list of 5 strings
        questions = []
        for line in raw.splitlines():
            line = line.strip()
            if line and line[0].isdigit():
                # Strip leading "1. " etc.
                q = line.split(".", 1)[-1].strip()
                if q:
                    questions.append(q)
        return questions[:5]
    except Exception:
        return []
```

### Step 2 — Call it in each `summarize_*` function

After the existing `tags = _generate_tags(summary)` line, add:
```python
questions = _generate_questions(summary)
```

### Step 3 — Add to result dict

In each `return { ... }` dict, add:
```python
"questions": questions,
```

### Step 4 — Save to DynamoDB in `storage.save_result()`

In `storage.py` `save_result()`, add one line to the `item` dict:
```python
"questions": result.get("questions", []),
```

### Step 5 — Return from cache in `storage.check_cache()`

In the `return { ... }` dict of `check_cache()`, add:
```python
"questions": list(item.get("questions", [])),
```

---

## Files Modified

| File | Change |
|------|--------|
| `summarizer.py` | Add `_generate_questions()`, call after `_generate_tags()`, add to result dict |
| `linkedin_search.py` | Same pattern |
| `google_search.py` | Same pattern |
| `storage.py` | Add `"questions"` field to `save_result()` item dict and `check_cache()` return dict |

No changes to `app.py`, templates, or DynamoDB table schema.

---

## LLM Cost Impact

One extra LLM call per summarisation (max_tokens=200 vs 60 for tags).
Runs in sequence after `_generate_tags()` — adds ~1-2s to the summarisation
flow which already runs in a background thread, so no user-visible latency.

---

## Future Vector Store Integration

When ready to build vector similarity:

```
DynamoDB item.questions (list of 5 strings)
    │
    ▼
Embed option A: " ".join(questions) → single 1536-dim vector per item
    → Simple cosine similarity: "find items with most similar question space"

Embed option B: embed each question separately → 5 vectors per item
    → Multi-vector search: a user query matches if it's similar to ANY question
    → Better recall for specific sub-topic queries
```

Recommendation when the time comes: **Option A first** (simpler, one vector per
item), upgrade to B if recall is poor.

---

## Verification

1. Summarise one item (any platform)
2. Check DynamoDB via DB Explorer → `questions` field should be a list of 5 strings
3. Re-summarise same item → `check_cache()` should return `questions` in result
4. Inspect question quality: should be specific, searchable, answerable by the content
