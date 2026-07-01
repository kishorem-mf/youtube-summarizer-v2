# Bug: Questions Column Empty for Recent Items (High Detail)

## Symptom
Questions column shows "—" for newly created items, particularly at `high` detail level.

## Root Cause
The questions section is appended to the summarisation prompt after a `---QUESTIONS---`
delimiter. The LLM must output the full summary *and* the questions within a single
`max_tokens` budget.

At `high` detail, `max_tokens = 1100`, and the call used `max_tokens + 200 = 1300`.
The instruction ("8-12 detailed bullet points") routinely fills ~1100 tokens for
rich content. With only 200 tokens left, the LLM runs out before reaching the
`---QUESTIONS---` section. The response is truncated, `_split_questions()` finds
no delimiter, and silently returns `[]` — no questions saved to DynamoDB.

```python
# Before (too tight for high detail)
max_tokens=max_tokens + 200   # 1100+200=1300 for high

# After (gives ~400 tokens of headroom for questions)
max_tokens=max_tokens + 400   # 1100+400=1500 for high
```

5 questions × ~50 tokens = ~250 tokens minimum. The extra 400 covers that comfortably
even when the summary runs to the full instruction budget.

## Files Fixed
All three summariser modules updated — identical one-line change in each:
- `summarizer.py` (YouTube)
- `linkedin_search.py` (LinkedIn)
- `google_search.py` (Google)

## Date Fixed
2026-07-01

## Note on Existing Items
Items already saved with empty questions will keep showing "—". Delete and
re-summarise them to backfill questions.
