# Feature Specification: Chatbot Part 2 — Brain (agent loop + tool calling + eval)

**Feature Branch**: `003-chatbot-part2-brain`

**Created**: 2026-05-23

**Status**: Draft

**Input**: Operator brief "Maintainer's Copilot — Chatbot Slice, Part 2". Builds on Part 1 (`specs/002-chatbot-part1-foundations/`) which shipped auth, memory primitives, NER + summarize, and audit log.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Authenticated maintainer holds a tool-augmented conversation (Priority: P1)

A signed-in maintainer hits `POST /chat`, asks something where a specific tool is the right answer, and gets a coherent reply that visibly used that tool. Multi-turn: the conversation carries forward via short-term memory.

**Why this priority**: This is the headline capability the slice delivers. Without it Part 2 is not done. Memory recall and refusal both build on this base.

**Independent Test**: As Alice, POST `/chat` with `{"message": "Classify this issue: <body>"}`. Expect a `ChatResponse` with `tool_trace` containing one entry where `tool_name=classify_issue` and a non-empty `output.label`. Follow-up message ("can you summarize it?") in the same conversation must reach the model with the prior turn in context.

**Acceptance Scenarios**:

1. **Given** Alice has a valid session, **When** she POSTs to `/chat` with a question that maps clearly to a tool, **Then** the response includes a tool_trace entry for the correct tool and a natural-language answer that uses the tool output.
2. **Given** a conversation with one user/assistant turn already, **When** Alice sends a follow-up referencing "the previous answer", **Then** the model receives the prior turn(s) as context and responds coherently.
3. **Given** Alice sends a question the model can answer without tools, **When** the loop runs, **Then** stop_reason='end_turn' on the first iteration and tool_trace is empty.
4. **Given** the agent enters a runaway tool-use loop, **When** 6 iterations elapse without `end_turn`, **Then** the response carries the fallback message and tool_trace lists 6 entries.

---

### User Story 2 — Cross-conversation memory recall is model-decided (Priority: P1)

A signed-in maintainer mentions a fact in conversation A. In conversation B (later, same user), the agent recalls and uses that fact — initiated by its own decision to call `recall_memory`, not by auto-injection.

**Why this priority**: This is the agentic part. Without it the chatbot is a stateless wrapper around Anthropic.

**Independent Test**: Two-turn scenario across two conversation_ids. Turn 1 plants a fact; assert `write_memory` fires. Turn 2 (new conversation_id, same user) references the fact; assert `recall_memory` fires AND the planted memory appears in the top-3.

**Acceptance Scenarios**:

1. **Given** an authed user shares a memorable fact, **When** the agent decides to save it, **Then** `write_memory` is called once with content containing the key phrase, an audit_log row lands, and the memory_id is returned to the agent loop.
2. **Given** a memory exists for the user, **When** a later conversation references it, **Then** the agent calls `recall_memory` and the planted memory is in the top-3 hits.
3. **Given** trivial chitchat ("hello"), **When** the agent processes it, **Then** `write_memory` is NOT called.

---

### User Story 3 — Widget session refusal at the agent layer (Priority: P1)

An anonymous widget visitor hits `POST /widget/chat`, says "remember this for next time," and gets a polite refusal. The memory tools refuse for widget actors; the agent loop catches the typed error and produces a sanitized user-facing response. Nothing lands in `chatbot_memories`.

**Why this priority**: This is the privacy contract the widget exists for. If it leaks, the widget can't ship.

**Independent Test**: Widget actor hits `/widget/chat` with a "remember this" message. Verify no row inserted into `chatbot_memories`, no row in `audit_log` with `action='memory.write'`, and the assistant response text matches a small set of refusal patterns.

**Acceptance Scenarios**:

1. **Given** a widget session, **When** the user tries to make the agent persist long-term memory, **Then** the agent receives `is_error=True` on the tool_result and produces a refusal that doesn't expose internal details.
2. **Given** a widget session, **When** the agent tries `recall_memory`, **Then** the tool_result carries `is_error=True` with kind="widget_actor_forbidden".
3. **Given** an invalid `X-Widget-Token`, **When** the request reaches `/widget/chat`, **Then** the response is 401 with no agent invocation.
4. **Given** the request `Origin` is not in `widget.allowed_origins`, **When** `/widget/chat` is hit, **Then** the response is 403.

---

### User Story 4 — CI eval gate catches regressions (Priority: P2)

CI runs the chatbot eval set in fixture mode on every push. A regression below the floor on any of four metrics blocks merge.

**Why this priority**: Operationally critical — without it the agent's behavior can drift silently. P2 because the agent's first-contact behavior is correctness; the gate is its sustained guarantee.

**Independent Test**: Run the eval harness in fixture mode against the committed fixture file; assert all 4 metrics ≥ their floors. Tamper with a fixture row (degrade a tool selection) and re-run; assert the harness exits non-zero with a clear floor-breach message.

**Acceptance Scenarios**:

1. **Given** the committed fixtures, **When** `python -m evals.chatbot.eval_chatbot --mode=fixture --check-thresholds` runs, **Then** exit code 0 and all 4 metrics reported.
2. **Given** a tampered fixture that drops `tool_selection_accuracy` below its floor, **When** the harness runs, **Then** exit non-zero with a message naming the floor and observed value.

---

### Edge Cases

- **Empty assistant response** from Anthropic on tool-use turn: treat as protocol error, surface 502, log, do not append empty assistant message to messages table.
- **Tool wrapper raises** for a real service failure (e.g., model_server unreachable inside `classify_issue_tool`): convert to tool_result with `is_error=True` so the agent can adapt; do not crash the loop.
- **Anthropic rate-limit** mid-loop: surface 429 to the caller, persist any partial messages, do not retry inside the loop.
- **Cross-conversation memory write race**: if two simultaneous `write_memory` calls fire for the same user, both land — no dedupe; the agent's "do not write the same fact twice" obligation is prompt-level, not enforced at the repo.
- **Widget actor presented after `Origin` whitelist mutation**: the origin check uses live `widget.allowed_origins`; revoking an origin while a request is mid-flight may still let that request through (acceptable for Part 2).
- **System-prompt hash drift**: rebuilding `prompts/chatbot_system.md` produces a different SHA-256; the new hash appears in every chat span. Operator can correlate behavior changes to prompt changes via the trace store.

## Requirements *(mandatory)*

### Functional Requirements

**Agent + tool calling**

- **FR-001**: System MUST expose `POST /chat` accepting `{conversation_id, message}` from an authenticated maintainer.
- **FR-002**: System MUST expose `POST /widget/chat` accepting `{widget_id, session_id, message}` with `X-Widget-Token` header.
- **FR-003**: System MUST validate the widget host token against `widget_repository.get_by_token_hash` and the request `Origin` against `widget.allowed_origins`.
- **FR-004**: The agent loop MUST support exactly six tools: `classify_issue`, `extract_entities`, `summarize_issue`, `retrieve_context`, `write_memory`, `recall_memory`.
- **FR-005**: Tool wrappers MUST call the existing services directly (`classifier_service.classify`, `ner_service.extract`, `summarize_service.summarize`, `retrieve_service.retrieve`, plus the two Part 1 memory tool primitives), not via the system's own HTTP API.
- **FR-006**: The agent loop MUST cap at 6 tool-use iterations per `/chat` call; on exhaustion it MUST produce a typed fallback message.
- **FR-007**: The memory tools MUST refuse for `WidgetSession` actors via a typed exception that the agent loop converts to a `tool_result` with `is_error=True`.
- **FR-008**: Every user, assistant, and tool message MUST be appended to the `messages` table and to short-term memory in Redis.

**Observability + audit**

- **FR-009**: System MUST emit a Phoenix span for every chat() call, every Anthropic call, every tool execution, and every memory operation.
- **FR-010**: Every span MUST carry: trace_id, actor_type, actor_id, conversation_id, tokens_in, tokens_out, latency_ms, prompt_hash.
- **FR-011**: Every `write_memory` call MUST land one `audit_log` row with `action='memory.write'` and payload `{conversation_id, memory_id, content_hash}` (sha256 over the redacted content, not the raw).
- **FR-012**: Redaction MUST apply at the service boundary before any content enters logs, traces, audit payloads, or memory writes (Part 1 R6 contract).

**Eval gate**

- **FR-013**: System MUST ship `evals/chatbot/golden.jsonl` with at least 15 scenarios spanning tool selection, memory write, memory recall, widget refusal.
- **FR-014**: `evals/chatbot/eval_chatbot.py` MUST compute four metrics: `tool_selection_accuracy`, `memory_write_rate`, `memory_recall_at_3`, `widget_refusal_rate`.
- **FR-015**: `eval_thresholds.yaml` MUST carry non-zero floors for all four metrics.
- **FR-016**: CI MUST run the chatbot eval gate in fixture mode and fail merge on any floor breach.

**System prompt**

- **FR-017**: `prompts/chatbot_system.md` MUST exist and be version-controlled; its SHA-256 hash MUST be logged with every chat span.

### Key Entities

- **ChatRequest / ChatResponse** — Pydantic shapes for the two endpoints; `ChatResponse` carries the final assistant text + a `tool_trace`.
- **ChatTurn** — single message persisted to `messages` (FK to `conversations`).
- **ToolDispatch** — internal name→callable registry the agent loop uses.
- **PromptHash** — sha256 of `prompts/chatbot_system.md` computed at import.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An authed maintainer's first `/chat` round-trip completes in under 10 s p95 on the dev laptop (includes one Anthropic call).
- **SC-002**: `tool_selection_accuracy` ≥ floor on the 15-scenario golden set (floor set ~5pt below observed pilot).
- **SC-003**: `memory_write_rate` ≥ floor.
- **SC-004**: `memory_recall_at_3` ≥ floor.
- **SC-005**: `widget_refusal_rate` ≥ floor (target 1.0 on the 3 widget refusal scenarios).
- **SC-006**: Zero `chatbot_memories` rows ever land for a widget actor across the widget refusal test suite.
- **SC-007**: Every `/chat` call produces ≥ 1 Phoenix span at the top level plus child spans for each Anthropic + tool execution.
- **SC-008**: The redaction test extended for memory.write asserts `content_hash` is over the redacted content; ground-truth `sk-ant-…` / email patterns never persist.
- **SC-009**: CI's chatbot eval gate exits non-zero when a tampered fixture drops any metric below floor.

## Assumptions

- Part 1 is merged to `main` (commit `b942422f` or descendant). Six fix-pass commits land: T-stack tip + Fix 1/2/3 + Doc + ruff chore.
- Anthropic API key is in Vault and reachable from the api container.
- Claude Sonnet 4 model identifier at implementation time: `claude-sonnet-4-5-20250929` (verify; if a newer Sonnet-4 is available, prefer it).
- Existing services (`classifier_service`, `ner_service`, `summarize_service`, `retrieve_service`) are stable and return typed outcomes — wrappers convert those to tool_result payloads.
- `write_memory` and `recall_memory` from Part 1 are fully implemented (no stub completion required).
- Streamlit, React widget bundle, and demo host site are Part 3 — out of scope.
- The eval gate uses fixture mode in CI to avoid real API calls; operator runs `--mode=real` to (re)derive floors.
