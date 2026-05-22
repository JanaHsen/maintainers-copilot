# Implementation Plan: Chatbot Part 2 — Brain

**Branch**: `003-chatbot-part2-brain` | **Date**: 2026-05-23 | **Spec**: [`spec.md`](./spec.md)

**Input**: Feature specification from `specs/003-chatbot-part2-brain/spec.md`

## Summary

Wire the agent loop. The chatbot service composes the six tool wrappers, drives Anthropic's tool-use API through a bounded loop (≤6 iterations), persists every turn, and exposes two routers (`/chat` for authed users, `/widget/chat` for anonymous visitors). The widget path refuses long-term memory at the tool-primitive layer (Part 1 contract); the agent loop catches the typed exception, emits an `is_error=True` tool_result, and Sonnet produces a sanitized refusal in natural language. An eval set of 15 scenarios with four metrics (`tool_selection_accuracy`, `memory_write_rate`, `memory_recall_at_3`, `widget_refusal_rate`) gates CI.

## Technical Context

**Language/Version**: Python 3.12 (project pin).

**Primary Dependencies**:
- Existing: FastAPI, SQLAlchemy 2.x (sync + the async engine from Part 1's R1), Pydantic v2, `anthropic` SDK, OpenTelemetry, Redis.
- No new top-level dependencies. `anthropic` already pinned ≥0.40 — verify it exposes `tool_use` blocks (it does; v0.40+ supports the tool-use API).

**Storage**:
- `conversations` + `messages` (Part 1) for turn persistence.
- `chatbot_memories` (Part 1) for long-term memory accessed via the existing `write_memory` / `recall_memory` tool primitives.
- Redis short-term memory (Part 1) for conversation window.
- `audit_log` (Part 1, evolved) for `memory.write` rows.

**Testing**: pytest + pytest-asyncio. Stubbed Anthropic client for unit tests; recorded fixture for integration tests; live Anthropic for the real-mode eval pilot.

**Performance Goals**: `/chat` p95 ≤ 10 s on dev laptop (one Anthropic call + tools). The 6-tool-iteration loop never produces a runaway >60 s response.

**Constraints**:
- Refuse-to-boot: no new fatal dependencies — Anthropic key already required at Part 1 boot (via the api's first request); we do NOT add it to `REQUIRED_VAULT_KEYS` (calls are lazy + per-request error mapped).
- `eval_thresholds.yaml.chatbot.*` floors MUST be non-zero before merge (Rule 4).

**Decisions deferred to research.md / DECISIONS.md** (operator-allowed picks):

1. **Conversation window strategy**: message-count cap (last N=20 messages) over precise token estimation. Cheap, deterministic, no tokenizer dep. Documented in R1.
2. **Tool-result formatting**: JSON string in the `content` field of the tool_result block. Anthropic's SDK accepts either string or content-blocks; string keeps the dispatch table simple. Documented in R2.
3. **Loop-cap exhaustion behavior**: produce a typed fallback assistant message "I ran out of attempts to finish that — please rephrase or simplify." and append to the messages table + return through ChatResponse. Cleaner than raising; the user sees a polite response, ops sees the trace + span attribute `loop_exhausted=true`. Documented in R3.

## Constitution Check

*GATE: Must pass before research / Phase 1. Re-check post-design.*

- **Rule 1 (Layered architecture).** Verbatim layering: `app/api/routers/chat.py` (HTTP), `app/services/chatbot_service.py` + `app/services/tools/*.py` (orchestration), no new repositories required — existing memory/conversation/widget/audit repos reused. **PASS.**
- **Rule 2 (Secrets discipline).** Anthropic API key continues to load from Vault via `anthropic_client._read_api_key()`. The widget host token is sha256-compared at the router boundary (no plaintext escape). **PASS.**
- **Rule 3 (Storage discipline).** Optional migration `0004_chatbot_part2.py` adds one index on `audit_log(actor_user_id, action, created_at DESC)` for Part 3 admin-panel queries; no schema otherwise. **PASS.**
- **Rule 4 (Refuse to boot).** No new boot dependencies (Anthropic key is already verified at first chat call). New eval floors (`chatbot.tool_selection_accuracy_floor` etc.) MUST be non-zero before merge. **PASS.**
- **Rule 5 (Evals are the grade).** `evals/chatbot/golden.jsonl` (15 scenarios), `eval_chatbot.py` with 4 metrics, floors in `eval_thresholds.yaml`, CI gate after the existing eval steps. **PASS.**
- **Rule 6 (Decisions backed by numbers).** DECISIONS.md entry for: R1 window strategy, R2 tool-result format, R3 loop-cap behavior, and the operator-allowed picks. **PASS.**
- **Rule 7 (Observability).** Phoenix span per chat/anthropic/tool/memory op, prompt-hash attribute on every chat span, redaction at the service boundary before audit/log/trace/memory writes. **PASS.**
- **Rule 8 (Tooling).** No new docker-compose service. No new top-level deps. CI workflow extended with the chatbot eval step. **PASS.**
- **Rule 9 (No vibe coding).** Every new file named for what it holds: `chatbot_service.py`, `chat.py` (router), `classify_issue_tool.py`, etc. No `utils.py` etc. **PASS.**
- **Rule 10 (CI discipline).** Existing CI workflow extended with the chatbot eval gate after the summarize gate. **PASS.**
- **Rule 11 (Resilient tool use).** Each tool wrapper catches its underlying service's typed errors and emits a structured `tool_result` with `is_error=True`. The chat router maps service-layer outcomes to HTTP per the existing `_KIND_TO_STATUS` pattern. No 5xx from the chatbot's own code; only from genuinely failing dependencies. **PASS.**

**Constitution Check verdict**: All 11 rules pass. No Complexity Tracking entries.

## Project Structure

### Documentation (this feature)

```text
specs/003-chatbot-part2-brain/
├── spec.md
├── plan.md              # this file
├── research.md          # R1 window / R2 tool-result / R3 loop-cap
├── data-model.md        # short — one optional index migration
├── quickstart.md        # bring-up + manual smoke per user story
├── contracts/
│   ├── chat.openapi.yaml       # POST /chat + POST /widget/chat
│   └── agent-tools.md          # the 6-tool dispatch contract
└── tasks.md             # commit-per-task list
```

### Source Code (repository root)

```text
app/
├── api/routers/
│   └── chat.py                          # POST /chat + POST /widget/chat
├── services/
│   ├── chatbot_service.py               # the agent loop
│   └── tools/
│       ├── __init__.py                  # extends with TOOLS list + dispatch
│       ├── classify_issue_tool.py
│       ├── extract_entities_tool.py
│       ├── summarize_issue_tool.py
│       ├── retrieve_context_tool.py
│       ├── write_memory_tool.py         # EXISTING (Part 1) — re-exported
│       └── recall_memory_tool.py        # EXISTING (Part 1) — re-exported
└── infra/
    └── anthropic_client.py              # add tool_use_chat(...)

# Prompts.
prompts/
└── chatbot_system.md                    # versioned; sha256 logged with every chat span

# Evals.
evals/chatbot/
├── golden.jsonl                         # 15 scenarios
├── eval_chatbot.py                      # --mode={fixture,real}, --check-thresholds
├── fixture_outputs.jsonl                # regenerated from real-mode pilot
└── README.md                            # selection logic + model pin

# Thresholds + CI.
eval_thresholds.yaml                     # +chatbot: 4 floors
.github/workflows/ci.yml                 # +chatbot eval gate

# (Optional) Migration.
alembic/versions/0004_chatbot_part2.py   # add ix_audit_log_actor_action_created

# Tests.
tests/
├── api/test_chat_router.py              # authed happy + widget refusal + 401/403
├── services/
│   ├── test_chatbot_service.py          # 6 unit tests for the loop branches
│   └── tools/
│       ├── test_classify_issue_tool.py
│       ├── test_extract_entities_tool.py
│       ├── test_summarize_issue_tool.py
│       └── test_retrieve_context_tool.py
└── infra/
    └── test_anthropic_client_tool_use.py
```

**Structure Decision**: Same layered tree as Part 1. The `app/services/tools/` subpackage gains four new wrappers; the two memory tools are re-exported (no rewrite). The chatbot service composes the dispatch. No new top-level directories.

## Complexity Tracking

> No constitution violations to justify in this slice.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|--------------------------------------|
| _(none)_  |            |                                      |
