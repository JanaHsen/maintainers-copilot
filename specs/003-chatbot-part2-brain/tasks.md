---
description: "Task list for Chatbot Part 2 — Brain (agent loop + tool calling + eval)"
---

# Tasks: Chatbot Part 2 — Brain

**Input**: Design documents from `specs/003-chatbot-part2-brain/`
**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/](./contracts/)
**Conventions**: each task is a logical commit boundary. `[P]` = parallelizable. Constitution rule references inline.

---

## Phase A — Foundations

- [ ] T001 Add `tool_use_chat(messages, tools, system, model, max_tokens) -> ToolUseResponse` to `app/infra/anthropic_client.py`. Wraps the Anthropic SDK's tool-use API; returns a typed wrapper around the SDK response so the caller can inspect `stop_reason` + tool_use blocks. Maps SDK error variants to the existing `AnthropicError` family. (Rule 1, Rule 11.) Tests: `tests/infra/test_anthropic_client_tool_use.py` with `httpx.MockTransport` returning a recorded tool-use response.

- [ ] T002 [P] Create `prompts/chatbot_system.md` per spec §5. Version-header line 1: `# Prompt version: chatbot-system-2026-05-23-001`. Body: role, tools (one line each), when-to-recall, when-to-write, never-mention, refusal patterns. (Rule 9 — versioned, named for content.)

- [ ] T003 [P] Add migration `alembic/versions/0004_chatbot_part2.py`: `CREATE INDEX ix_audit_log_actor_action_time ON audit_log (actor_user_id, action, timestamp DESC) WHERE actor_user_id IS NOT NULL`. Downgrade drops it. (Rule 3.) Test: existing migration round-trip pattern.

---

## Phase B — Tool wrappers (4 new + 2 re-exports)

Each tool wrapper has: (a) an Anthropic tool definition (name/description/input_schema), (b) an `execute(input: dict, actor: Actor) -> dict` dispatch function, (c) a unit test stubbing the underlying service.

- [ ] T004 [P] `app/services/tools/classify_issue_tool.py`. Calls `classifier_service.classify_issue(title, body)`. Tool def + execute + unit test. (Rule 1, Rule 11.)

- [ ] T005 [P] `app/services/tools/extract_entities_tool.py`. Calls `ner_service.extract(text)`. Tool def + execute + unit test.

- [ ] T006 [P] `app/services/tools/summarize_issue_tool.py`. Calls `summarize_service.summarize(text, max_sentences)`. Tool def + execute + unit test.

- [ ] T007 [P] `app/services/tools/retrieve_context_tool.py`. Calls `retrieve_service.retrieve(req)`. Tool def + execute + unit test.

- [ ] T008 Update `app/services/tools/__init__.py` to export `TOOLS` (Anthropic tool definitions list) and `TOOLS_DISPATCH` (dict mapping tool name → execute callable). Re-export the existing `write_memory_tool.write_memory` and `recall_memory_tool.recall_memory` for the dispatch. (Rule 9.)

---

## Phase C — Chatbot service (the agent loop)

- [ ] T009 `app/services/chatbot_service.py` per spec §3 + research R1/R2/R3. The public surface: `chat(conversation_id: UUID | None, user_message: str, actor: Actor) -> ChatResponse`. Composes anthropic_client.tool_use_chat with the loop. Computes `PROMPT_HASH = sha256(open(prompts/chatbot_system.md).read())` at import. (Rule 1, Rule 7, Rule 11.)

- [ ] T010 [P] Domain shapes in `app/domain/chat.py`: `ChatRequest`, `ChatResponse`, `ToolTraceEntry`. Pydantic. (Rule 1, Rule 9.)

- [ ] T011 Unit tests `tests/services/test_chatbot_service.py` covering the six scenarios from spec §3:
  - single-turn no-tool
  - single tool call
  - multi-tool call in one assistant turn
  - memory write then recall across two `chat()` calls
  - widget actor calling memory tool → typed error → refusal
  - loop cap exhaustion → fallback message
  Anthropic client is stubbed to return scripted tool-use sequences. (Rule 5 sibling.)

---

## Phase D — Routers

- [ ] T012 `app/api/routers/chat.py` with `POST /chat` (authed; require `current_active_user`) and `POST /widget/chat` (X-Widget-Token + Origin checks). Maps service outcomes to HTTP per Rule 11. Mounts into `app/api/routers/__init__.py`. (Rule 1.)

- [ ] T013 [P] Integration test `tests/api/test_chat_router.py`:
  - authed happy path (one /chat call with a stubbed Anthropic returning end_turn immediately)
  - widget refusal path (one /widget/chat call where Anthropic tries to write_memory)
  - 401 (no session for /chat)
  - 401 (bad token for /widget/chat)
  - 403 (origin not in allowed_origins)

---

## Phase E — Evals

- [ ] T014 [P] `evals/chatbot/golden.jsonl` — 15 scenarios. Coverage: 5 tool-selection, 3 memory-write, 4 memory-recall (two-turn), 3 widget-refusal. (Rule 5.)

- [ ] T015 [P] `evals/chatbot/README.md` — selection logic, model pin, judge notes.

- [ ] T016 `evals/chatbot/eval_chatbot.py` — 4 metrics: `tool_selection_accuracy`, `memory_write_rate`, `memory_recall_at_3`, `widget_refusal_rate`. CLI: `--mode={fixture,real}`, `--check-thresholds`, `--emit-fixture`, `--upload-report`. Same pattern as the Part 1 NER/summarize harnesses. (Rule 5, Rule 10.)

- [ ] T017 Add `chatbot:` section to `eval_thresholds.yaml` with placeholder floors (e.g., 0.5 across the board) so the gate compiles; the real-mode pilot in Phase F replaces them. (Rule 4.)

- [ ] T018 `.github/workflows/ci.yml`: add "Chatbot eval gate" step after the Summarize eval gate, runs `python -m evals.chatbot.eval_chatbot --mode=fixture --check-thresholds`. (Rule 10.)

---

## Phase F — Pilot + finalize

- [ ] T019 Run `python -m evals.chatbot.eval_chatbot --mode=real` against the live stack. Record observed scores. Adjust `eval_thresholds.yaml` to ~5pt below observed. Regenerate `evals/chatbot/fixture_outputs.jsonl` via `--mode=real --emit-fixture`. (Rule 4, Rule 5.)

- [ ] T020 Redaction extension: extend `tests/infra/test_log_redaction.py` (or a new memory-write-path test) asserting that a `write_memory` call from the chatbot service with content containing `sk-ant-…` + an email results in: (a) `chatbot_memories.content` redacted, (b) `audit_log.payload.content_hash` computed over the redacted content, (c) the assistant message + tool_trace passed to the model carry no raw secret. (Rule 7.)

---

## Phase G — Polish

- [ ] T021 DECISIONS.md entries: R1 window, R2 tool-result format, R3 loop-cap fallback, plus the four chatbot-eval floors with observed/buffer/gap. (Rule 6.)

- [ ] T022 RUNBOOK update — the `/chat` + `/widget/chat` curl walk-throughs from quickstart.md; any new failure modes operators should know about. (Rule 8.)

- [ ] T023 Final verification: `ruff app/` + `mypy app/` clean. New Part 2 tests pass. Part 1 tests still pass. Real-mode eval ran. Fixture-mode CI gate passes.

---

## Dependencies

A → B → C → D → E → F → G. Within phases, `[P]` items can land in parallel. T002 (prompt) and T003 (migration) can land any time after T001.

## Story → tasks map

| Story | Tasks |
|-------|-------|
| US1 — Authed maintainer tool-augmented chat | T001, T002, T004–T008, T009, T010, T011, T012, T013 |
| US2 — Cross-conversation memory recall | T009, T011 (recall scenario), T014–T016 (memory-recall metric) |
| US3 — Widget refusal | T012, T013, T014–T016 (widget-refusal metric) |
| US4 — CI eval gate catches regressions | T014–T019 |
| Cross-cutting | T020 (redaction), T021–T023 (polish) |
