# Phase 0 — Research: Chatbot Part 2 Brain

Three load-bearing decisions the operator deliberately left open. Each: Decision → Rationale → Alternatives.

## R1 — Conversation window strategy

**Decision**: Message-count cap. Take the last N = **20** messages from short-term memory (Redis list `convo:{conversation_id}`). System prompt + last 20 messages + new user message goes to Anthropic.

**Rationale**: Token-accurate windows need a tokenizer dep (tiktoken-shaped, not bundled by `anthropic`) plus content-length per message. Message-count is one LRANGE + a slice — no tokenizer, deterministic, easy to reason about. 20 messages ≈ 10 user/assistant pairs; with average message length around 200-400 tokens, that lands at 4000-8000 tokens — well inside Sonnet 4's context window even after the system prompt + tool definitions. The Part 1 short-term memory service's `get_window(max_tokens=4000)` does crude `len(content)//4` token estimation; we keep that helper and pass `max_tokens=4000` as a safety net but the primary cap is message-count.

**Alternatives considered**:

- **Precise token count via tiktoken**. Rejected: adds a transitive dep, the Anthropic tokenizer is not 1:1 with OpenAI's, and Sonnet 4 has a 200k window — precision isn't load-bearing here.
- **No cap (send everything)**. Rejected: an unbounded conversation eventually overflows the context window or costs runaway tokens. Even a 200k context is not free.
- **Sliding-window summarization** (LLM-compress older turns). Rejected: out of Part 2's scope. Adds an Anthropic call per chat turn, more spans, more failure modes. Revisit in Part 3 if conversations get long.

## R2 — Tool-result formatting

**Decision**: JSON string in the `content` field of the tool_result block. The dispatcher serializes each tool's return value (typed dataclass or dict) via `json.dumps(..., default=str)` and passes the string to Anthropic. Errors come back as `{"error": {"kind": "...", "detail": "..."}}` with `is_error=True`.

**Rationale**: The Anthropic SDK accepts either a string OR a list of content blocks for tool_result. String is simpler: one consistent shape for every tool, no per-tool content-block bookkeeping. Sonnet parses JSON strings reliably and the strings are short (the longest expected payload is `retrieve_context` returning ~5 chunks worth of snippets, ~2-3 kB). Structured content blocks would buy us image/PDF support — not needed by any of the 6 tools.

**Alternatives considered**:

- **Structured content blocks** with type=text. Rejected: same effect on Sonnet's behavior, more code at the dispatch boundary.
- **Pass typed Python objects directly** and let the SDK serialize. Rejected: SDK accepts only `str` or `list[ContentBlock]`. Typed objects would need a transform anyway.
- **Custom JSON schema per tool** with response wrappers. Rejected: adds complexity without observable benefit; tools already have typed-outcome inputs at the service layer.

## R3 — Loop-cap exhaustion behavior

**Decision**: At the 6th iteration without `stop_reason="end_turn"`, append an assistant message `"I ran out of attempts to finish that — please rephrase or simplify."` to the conversation history (both `messages` table and short-term memory), set span attribute `loop_exhausted=true`, and return a normal `ChatResponse` with that message as the final text + the full tool_trace. Operator sees the exhaustion via Phoenix; user sees a polite response.

**Rationale**: Raising an exception would be operationally noisy (would surface as 5xx — violating Rule 11). A fallback message keeps the chat flow recoverable and the trace store is the canonical operator surface for diagnosing why the loop didn't converge. Six iterations is conservative — empirically Sonnet uses ≤2 tool-use rounds for most tool-augmented questions; six leaves room for chained tool calls without enabling runaway.

**Alternatives considered**:

- **Raise `LoopExhaustedError`** mapped to 500. Rejected: violates Rule 11 (chatbot's own code paths failing). The cause is model behavior, not infra.
- **Force a final synthesis call** with `tool_choice="none"`. Rejected: adds a 7th Anthropic call after exhaustion, doubles the latency cost of the bad case, adds another failure mode. The fallback string is honest and cheap.
- **Configurable cap** via env / setting. Rejected: yet another knob. 6 stays a constant in `chatbot_service.MAX_TOOL_ITERATIONS` and can change via PR if Part 3 needs a different number.

## Cross-cutting: prompt hash propagation

Computed once at module import via `hashlib.sha256(PROMPT_PATH.read_bytes()).hexdigest()` and exposed as `PROMPT_HASH` in `chatbot_service`. Every chat span gets `prompt_hash` as an attribute. Cost: one file read at import. Benefit: operator can correlate behavior changes to prompt-version changes in Phoenix without grepping git.

No NEEDS CLARIFICATION markers remain.
