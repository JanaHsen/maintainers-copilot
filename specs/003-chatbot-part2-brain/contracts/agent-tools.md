# Agent tool dispatch — internal contract

The agent loop in `app/services/chatbot_service.py` looks up tools by `name` in the `TOOLS_DISPATCH` registry exported from `app/services/tools/__init__.py`. Each tool wrapper has:

1. An **Anthropic tool definition** (a dict with `name`, `description`, `input_schema`) included in the `TOOLS` list passed to `anthropic_client.tool_use_chat`.
2. A **dispatch function** `execute(input: dict, actor: Actor) -> dict` that converts the underlying service's typed outcome into a JSON-serializable dict. On the service's error path, returns `{"error": {"kind": "...", "detail": "..."}}` and the caller (the loop) sets `is_error=True` on the tool_result.

## The six tools

### `classify_issue`

- **input**: `{title: str, body: str}`
- **description**: "Classify a GitHub issue into bug / feature / documentation / question. Use when the user provides an issue's title and body and asks for its category."
- **dispatch**: `classifier_service.classify_issue(title=..., body=...)` → on `ClassifyOk`, return `{"label": ..., "confidence": ..., "label_scores": ...}`; on `ClassifyError`, return `{"error": {"kind": kind, "detail": detail}}`.

### `extract_entities`

- **input**: `{text: str}`
- **description**: "Extract repo names, file paths, error types, and package names from issue text. Use when the user gives you a chunk of text and you need its named entities."
- **dispatch**: `ner_service.extract(text=...)` → on `NerOk`, return `{"entities": {...4 buckets...}}`; on `NerError`, return `{"error": {"kind": ..., "detail": ...}}`.

### `summarize_issue`

- **input**: `{text: str, max_sentences?: int = 3}`
- **description**: "Produce a 2-3 sentence summary of an issue body. Use when the user pastes a long issue and asks for the gist."
- **dispatch**: `summarize_service.summarize(text=..., max_sentences=...)` → on `SummarizeOk`, return `{"summary": ...}`; on `SummarizeError`, return `{"error": ...}`.

### `retrieve_context`

- **input**: `{query: str, k?: int = 5}`
- **description**: "Retrieve up to k documentation/issue chunks relevant to the query. Use when the user asks a question that depends on project knowledge."
- **dispatch**: `retrieve_service.retrieve(query=..., k=...)` → on `RetrieveOk`, return `{"chunks": [{"id": ..., "content_snippet": ..., "source_type": ..., "source_id": ...}]}`; on `RetrieveError`, return `{"error": ...}`.

### `write_memory`

- **input**: `{content: str}`
- **description**: "Save a fact about the user for future conversations. Use sparingly: only when the user shares a preference, an identity, or context likely to be useful later. Do not write trivial chat."
- **dispatch**: `write_memory_tool.write_memory(content=..., actor=..., conversation_id=...)` (Part 1) → on `WriteMemoryOk`, return `{"memory_id": ...}`; on `WriteMemoryError(kind="widget_actor_forbidden")`, return `{"error": {"kind": "widget_actor_forbidden", "detail": "long-term memory not available in this context"}}`; on other errors, return `{"error": ...}`.

### `recall_memory`

- **input**: `{query: str, k?: int = 5}`
- **description**: "Retrieve up to k prior facts the user has shared. Use at the start of a conversation, or when the user references something you don't recognize but might have been told before."
- **dispatch**: `recall_memory_tool.recall_memory(query=..., actor=..., k=...)` (Part 1) → on `RecallMemoryOk`, return `{"hits": [{"memory_id": ..., "content": ..., "similarity": ...}]}`; on `RecallMemoryError(kind="widget_actor_forbidden")`, same shape as write_memory.

## Tool-result envelope

For each tool_use block in an Anthropic response:

```python
{
  "type": "tool_result",
  "tool_use_id": <id from the model's tool_use block>,
  "content": json.dumps(dispatch_output, default=str),
  "is_error": "error" in dispatch_output,
}
```

The `is_error` boolean lets Sonnet adapt; it sees a structured error blob in `content` and the natural response is to acknowledge the failure to the user and continue the conversation (Rule 11).

## Loop-cap fallback

After `MAX_TOOL_ITERATIONS = 6` iterations without `stop_reason='end_turn'`, the loop:

1. Appends an assistant message `"I ran out of attempts to finish that — please rephrase or simplify."` to the messages table and to short-term memory.
2. Sets the top-level chat span attribute `loop_exhausted=true`.
3. Returns a normal `ChatResponse` with that text as `assistant_message` and the full `tool_trace` accumulated up to that point.
