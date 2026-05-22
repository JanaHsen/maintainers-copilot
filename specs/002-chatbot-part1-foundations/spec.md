# Feature Specification: Chatbot Part 1 — Foundations (auth, memory, tool stubs, upstream services)

**Feature Branch**: `002-chatbot-part1-foundations`

**Created**: 2026-05-22

**Status**: Draft

**Input**: User description: "Ship everything Part 2 (agent loop) and Part 3 (surfaces) of the chatbot slice will need, with nothing built on top yet. Adds authenticated maintainer accounts, persistent per-user memory, short-term conversation memory for anonymous widget visitors, an immutable audit trail, the two memory tool primitives (write_memory, recall_memory), and the two missing upstream services the agent will eventually call (named-entity extraction, issue summarization — classifier and retrieval already exist). Hard requirement: every secret through Vault, refuse-to-boot if a required dependency is unreachable, every behavior backed by a numeric eval floor enforced in CI."

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Maintainer signs in and gets a session (Priority: P1)

A maintainer visits the application, registers an account with email + password, signs in, and is recognized on subsequent requests until they sign out or their session expires. Without this story none of the authenticated-only features (long-term memory, admin panel) can be built on top.

**Why this priority**: Every other authenticated capability — memory, audit, widget management — depends on knowing *which maintainer* is acting. P1 because the rest of the slice cannot be tested or demonstrated without it.

**Independent Test**: Hit registration with a fresh email → expect success; sign in with the same credentials → expect a session cookie returned; call the "who am I" endpoint with that cookie → expect the maintainer's profile; call it without the cookie → expect rejection.

**Acceptance Scenarios**:

1. **Given** no account exists for `alice@example.com`, **When** Alice registers with that email and a password, **Then** her account is created and she can immediately sign in.
2. **Given** Alice is signed in, **When** she calls a protected endpoint, **Then** the system recognizes her and returns her data.
3. **Given** an unauthenticated visitor, **When** they call a protected endpoint, **Then** the system rejects the request with an unauthorized response.
4. **Given** an account with role `user`, **When** they call an admin-only endpoint, **Then** the system rejects the request with a forbidden response.
5. **Given** Alice signs out, **When** she calls a protected endpoint with the now-cleared session, **Then** the system rejects the request.

---

### User Story 2 — Authenticated maintainer's memories persist across conversations (Priority: P1)

A signed-in maintainer can have a fact written to their long-term memory during one conversation and retrieve it during a separate, later conversation. The memory is scoped to that single maintainer — no other account can read it.

**Why this priority**: This is the headline capability that distinguishes the authenticated experience from the widget experience. The chatbot is unusable as "your maintainer copilot" without it. P1 because Parts 2 and 3 both depend on the tool primitives shipped here.

**Independent Test**: As Alice, invoke the write-memory primitive in conversation A with content "Alice prefers Conventional Commits." Start conversation B (different conversation id), invoke the recall-memory primitive with query "what commit style does Alice prefer?" — expect the written memory to appear among the top-ranked recalled items. Repeat the recall as Bob (different maintainer) — expect Alice's memory to be absent.

**Acceptance Scenarios**:

1. **Given** Alice is signed in and conversation A exists, **When** write_memory is called with content X, **Then** the memory is stored, an audit-log entry recording the write is created, and the memory's id is returned.
2. **Given** Alice's earlier memory X exists, **When** recall_memory is called in a *different* conversation with a query semantically matching X, **Then** X appears in the returned top-k results.
3. **Given** Alice wrote memory X, **When** Bob (different maintainer) calls recall_memory with a query matching X, **Then** X does **not** appear in Bob's results.
4. **Given** a write_memory call whose content contains a fake API key like `sk-ant-AAAA0000` or an email address, **When** the memory is stored, **Then** both the persisted content and any log line referencing it have the secret/email replaced with a redaction marker.

---

### User Story 3 — Anonymous widget visitor is isolated from long-term memory (Priority: P1)

A widget visitor (no maintainer account) can hold a conversation that survives within a single session window but is never written to the long-term store. Any attempt to invoke the write-memory or recall-memory primitives in a widget-actor context is refused.

**Why this priority**: The privacy contract for the embedded widget — that anonymous visitors cannot leak into or read from maintainer-private memory — is foundational. If this contract leaks, the widget cannot ship in Part 3. P1 because it must be enforced at the primitive layer, not later in the agent layer.

**Independent Test**: Submit a sequence of messages on behalf of a widget actor — verify each is held in short-term conversation context. Attempt to invoke write_memory with a widget actor — expect refusal (domain-level, not transport-level). Verify no row appears in the long-term memory table for that session.

**Acceptance Scenarios**:

1. **Given** an anonymous widget session, **When** messages are appended to the conversation, **Then** they are retrievable from short-term memory within the session's TTL window.
2. **Given** an anonymous widget session, **When** write_memory is invoked, **Then** the primitive refuses with a domain error and no row is written to the long-term memory store.
3. **Given** an anonymous widget session, **When** recall_memory is invoked, **Then** the primitive refuses with a domain error and no rows are returned.
4. **Given** a widget session's TTL has elapsed, **When** the short-term store is queried for the session, **Then** the prior messages are no longer present.

---

### User Story 4 — Operator audits sensitive actions (Priority: P2)

An operator (admin role) can answer "who wrote which memory, when?" and "who created/revoked which widget, when?" by reading an immutable audit log. The log must include actor identity (or "widget session"), action type, target, and timestamp.

**Why this priority**: Required for trust and required for the admin panel in Part 3. Not P1 because the audit log can be added in parallel without blocking the chatbot loop, but it must exist before any memory writes occur — otherwise we lose the first wave of evidence.

**Independent Test**: Trigger a write_memory call by Alice; trigger a widget creation by an admin; trigger a widget revocation. Query the audit log filtered by actor → expect three entries with correct action types, targets, and timestamps. Attempt to mutate or delete an entry → expect rejection.

**Acceptance Scenarios**:

1. **Given** Alice writes a memory, **When** the audit log is read, **Then** it contains a `memory.write` row with Alice's id as actor.
2. **Given** an admin creates a widget, **When** the audit log is read, **Then** it contains a `widget.create` row with the widget id as target.
3. **Given** the audit log holds N rows, **When** any caller attempts to update or delete a row, **Then** the system rejects the operation.

---

### User Story 5 — Upstream issue services exist for the agent to call later (Priority: P2)

For any issue text the agent will later receive, the system can: extract entities (repo names, file paths, error types, package names), summarize the issue into 2-3 sentences, classify the issue's primary label (already shipped), and retrieve relevant context (already shipped). All four behave as standalone HTTP endpoints with their own quality floors before the agent depends on them.

**Why this priority**: Part 2's agent eval cannot test 6-tool selection if half the tools don't exist. Building the missing services here keeps Part 2 focused on the agent loop. P2 because the agent itself is not yet wired — these are dormant capabilities that activate in Part 2.

**Independent Test**: Submit issue text to the entity-extraction endpoint → expect a structured list of entities present in the text. Submit the same text to the summarization endpoint → expect a 2-3 sentence condensed summary. Run each endpoint against its ~10-example eval set → expect metric scores at or above the documented floors.

**Acceptance Scenarios**:

1. **Given** issue text mentioning `acme/widget`, file `src/foo.py`, and `requests` package, **When** the entity-extraction endpoint is called, **Then** the response contains those three items grouped by entity type.
2. **Given** a multi-paragraph issue body, **When** the summarization endpoint is called, **Then** the response is 2-3 sentences capturing the issue's intent.
3. **Given** the entity-extraction eval set, **When** the eval gate runs, **Then** the recorded score is at or above the floor in `eval_thresholds.yaml`.
4. **Given** the summarization eval set, **When** the eval gate runs, **Then** the recorded score is at or above the floor in `eval_thresholds.yaml`.

---

### User Story 6 — System refuses to boot on missing critical dependencies (Priority: P3)

If a required dependency — secret-store access, database, embedding service, model server, low-latency store — is unreachable at process start, the system logs a specific actionable error identifying the missing dependency and exits with a non-zero status rather than starting in a broken state.

**Why this priority**: Operationally critical (constitution requirement) but does not block functional development. P3 because the existing boot-check pattern in the codebase already covers most dependencies — this story extends it to cover the new ones added in Part 1 (auth secret, users table).

**Independent Test**: Disable the secret-store path that holds the auth signing secret → start the service → expect a log line naming that secret path and a non-zero exit. Restore the path → expect a clean startup.

**Acceptance Scenarios**:

1. **Given** the secret-store path holding the auth signing key is missing or unreachable, **When** the service starts, **Then** it logs an error naming that path and exits non-zero.
2. **Given** the database does not have the users table, **When** the service starts, **Then** it logs an error naming the missing table and exits non-zero.
3. **Given** all dependencies are healthy, **When** the service starts, **Then** the boot-check passes and the service accepts requests.

---

### Edge Cases

- **Duplicate registration**: A second attempt to register with an existing email is rejected with a clear conflict response; the original account is unaffected.
- **Weak passwords**: Passwords below a stated minimum length are rejected at registration with an actionable message.
- **Cross-conversation memory recall ordering**: When multiple memories of similar relevance exist, the top-ranked memory must be the one most semantically related to the query, not merely the most recent.
- **Memory store empty for new account**: Recall on an account with zero stored memories returns an empty result set, not an error.
- **Widget session re-use after TTL**: If a widget session id is presented after its TTL has elapsed, the prior conversation context is gone but the session is not "stolen" — the next message starts a fresh window for that session id.
- **Audit-log write failure during memory write**: If the audit-log write fails after the memory write, the memory write is rolled back so the audit record always reflects ground truth.
- **Embedding service slow on memory write**: If embedding the to-be-stored content exceeds a documented budget, the write fails fast rather than blocking the conversation indefinitely.
- **Concurrent recall during write**: A recall issued the instant after a write completes must be able to see the just-written memory (read-after-write consistency for the same maintainer).
- **NER on text with no entities**: The entity-extraction endpoint returns an empty result for each entity type, not an error.
- **Summarization on very short input**: Input below a documented length threshold is returned roughly as-is rather than padded.
- **Secret in conversation text**: A maintainer that intentionally or accidentally writes a `sk-ant-…` key or email into a message that becomes a memory write — the persisted memory has the secret/email redacted before storage and before any log emission.

## Requirements *(mandatory)*

### Functional Requirements

**Authentication & authorization**

- **FR-001**: System MUST allow a visitor to register a maintainer account with email and password.
- **FR-002**: System MUST allow a registered maintainer to sign in and obtain a session credential.
- **FR-003**: System MUST allow a signed-in maintainer to sign out and invalidate their session.
- **FR-004**: System MUST identify the acting maintainer on every authenticated request.
- **FR-005**: System MUST reject unauthenticated requests to maintainer-only endpoints with an unauthorized response.
- **FR-006**: System MUST support two roles, `user` and `admin`, and MUST reject `user`-role requests to admin-only endpoints with a forbidden response.
- **FR-007**: System MUST source the credential-signing secret exclusively from the central secret store; the system MUST NOT accept an environment-variable fallback for this secret.

**Long-term memory (authenticated only)**

- **FR-008**: System MUST provide a primitive that, given content and an acting maintainer, embeds the content and stores it as a memory associated with that maintainer.
- **FR-009**: System MUST provide a primitive that, given a query and an acting maintainer, returns up to k memories belonging to that maintainer ranked by semantic similarity to the query.
- **FR-010**: System MUST scope every memory query to the acting maintainer; cross-account leakage is forbidden by construction.
- **FR-011**: System MUST refuse to perform write or recall against the long-term store when the acting party is a widget session (non-maintainer).
- **FR-012**: System MUST allow recall to return zero results without raising an error.
- **FR-013**: System MUST guarantee read-after-write visibility of a memory to the same maintainer immediately after the write returns success.
- **FR-014**: System MUST tag each memory with a source classification, defaulting to `episodic`, so future memory types can coexist without schema migration.

**Short-term memory (per conversation)**

- **FR-015**: System MUST hold an ordered window of recent messages per conversation in a low-latency store.
- **FR-016**: System MUST expire a widget conversation's short-term window after a documented TTL (default 1 hour).
- **FR-017**: System MUST expire an authenticated maintainer's short-term window after a longer documented TTL (default 24 hours).
- **FR-018**: System MUST return the available window when asked, bounded by a configured token budget.

**Conversations & messages**

- **FR-019**: System MUST associate every conversation with exactly one actor: either a maintainer (by id) or a widget session (by widget id + session id). Both cannot be set; neither cannot be set.
- **FR-020**: System MUST append messages to a conversation with role (`user`, `assistant`, `tool`), content, and, for tool messages, the tool name plus input and output payloads.

**Audit log**

- **FR-021**: System MUST record an audit entry for every long-term memory write, capturing actor, action, target, payload, and timestamp.
- **FR-022**: System MUST record an audit entry for every widget create and revoke action.
- **FR-023**: System MUST record an audit entry for every role change.
- **FR-024**: System MUST reject updates and deletes against audit-log rows (append-only).

**Widget administration scaffolding** *(no widget UI yet — that is Part 3)*

- **FR-025**: System MUST allow an admin to create a widget record (name, owner, allowed origins) and receive a one-time host-token plaintext value; the system MUST persist only the hash.
- **FR-026**: System MUST allow an admin to revoke a widget by setting a revocation timestamp; the record MUST remain for audit.
- **FR-027**: System MUST allow the system to look up a non-revoked widget by host-token hash.

**Upstream issue services (the agent will consume these in Part 2)**

- **FR-028**: System MUST expose an entity-extraction endpoint that returns, for given issue text, structured lists of repo names, file paths, error types, and package names present in that text.
- **FR-029**: System MUST expose a summarization endpoint that returns, for given issue text, a 2-3 sentence summary of the issue's intent.
- **FR-030**: Each of the entity-extraction and summarization endpoints MUST have a documented eval set and a numeric floor in `eval_thresholds.yaml` enforced in CI.

**Redaction**

- **FR-031**: System MUST redact secret patterns (model API keys, signed session credentials, widget host tokens) and personal email addresses before any text reaches the log stream, the trace store, or the long-term memory store.
- **FR-032**: Redaction MUST be tested with a fixture that asserts the persisted content and the log line both contain the redaction marker rather than the secret.

**Observability**

- **FR-033**: System MUST emit a trace span for every memory write, memory recall, and call to an external language-model service, including model identifier, token counts, latency, and redacted I/O.
- **FR-034**: System MUST propagate a request-correlation identifier through every structured log line emitted while handling a request.

**Boot-time validation**

- **FR-035**: System MUST verify on startup that the secret store, the database, the embedding service, the model server, and the short-term store are reachable; if any is not, the system MUST log a specific error naming the failing dependency and exit non-zero.
- **FR-036**: System MUST verify on startup that the users table is present in the database; if not, the system MUST refuse to boot.

**Layering & secret discipline** *(architectural constraints; these are testable)*

- **FR-037**: Routers MUST NOT execute database queries directly; database access MUST occur through the repository layer.
- **FR-038**: Services MUST NOT execute database queries directly; services orchestrate repositories.
- **FR-039**: No source file under `app/` MUST read secret values directly from environment variables; secrets MUST be retrieved from the secret-store client.

### Key Entities

- **Maintainer (User)**: A signed-up human acting in the application. Holds an identifier, an email, a hashed password, active/verified flags, and a role (`user` or `admin`). Owns memories, conversations, audit entries, and may own widgets.
- **Widget**: A configured embed point for a maintainer's site. Holds an identifier, a display name, a hash of its host token, a list of allowed origins, an owner maintainer, a creation timestamp, and an optional revocation timestamp.
- **Conversation**: A bounded interaction context. Belongs to *either* a maintainer (authenticated) *or* a (widget, session) pair (anonymous), never both. Holds a created-at and last-message-at timestamp.
- **Message**: A single turn within a conversation. Holds a role (`user`/`assistant`/`tool`), content, optional tool name + tool input + tool output, and a timestamp.
- **Memory (long-term)**: A maintainer-scoped piece of stored knowledge. Holds an identifier, the owning maintainer, the originating conversation, content, an embedding vector, a `source` classification (defaulting to `episodic`), and a created-at timestamp.
- **Short-term Window**: The recent slice of a conversation kept in a fast store, expiring on a TTL. Not a row-shaped entity — a logical container.
- **Audit Entry**: An immutable record of a sensitive action. Holds an actor (maintainer id or widget id), an action type, a target type, a target identifier, a payload, and a timestamp.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A new maintainer can register, sign in, and reach a protected endpoint in under 5 seconds end-to-end on a developer laptop.
- **SC-002**: Cross-conversation memory recall surfaces a previously written memory in the top-3 results in at least 80% of curated recall scenarios; the floor is recorded in `eval_thresholds.yaml`.
- **SC-003**: Zero memories ever appear in another maintainer's recall results across the seeded multi-account integration test (100% isolation).
- **SC-004**: Zero attempts to invoke the long-term memory primitives from a widget-actor context succeed across the widget refusal test suite (100% refusal).
- **SC-005**: Entity-extraction and summarization each meet a documented quality floor on their ~10-example eval sets, enforced as a CI gate.
- **SC-006**: 100% of memory writes and 100% of widget create/revoke actions appear in the audit log within the same request.
- **SC-007**: Across the redaction fixture, 100% of injected secret patterns and email patterns are replaced with the redaction marker in both stored memory content and log output.
- **SC-008**: With any one of the critical dependencies disabled, the service exits non-zero at startup within 30 seconds and emits a log line that names the failing dependency.
- **SC-009**: No source file under `app/` reads a secret value from an environment variable; a static check enforces this in CI.
- **SC-010**: An authenticated maintainer can read their own recall results; a non-authenticated request to the same endpoint is rejected — both verified in the same integration test.

## Assumptions

- The existing RAG slice (commit `274afd6f`) is the integration baseline. Existing patterns to reuse: language-model client, embedding client, low-latency store client, secret-store client, redaction layer, tracing, database, model-server client. This spec does not relitigate any of these.
- Long-term memory is restricted to a single classification (`episodic`) for this Part. The `source` field exists to admit future classifications without a migration; no other classification is built in Part 1.
- The chatbot agent loop itself is **out of scope** for this Part; this Part ships *primitives* that the Part 2 agent will compose.
- The Streamlit UI, the embedded widget bundle, and the demo host site are **out of scope** for this Part; they are Part 3.
- The two upstream services added here (entity extraction, summarization) are scope additions to the brief, taken in this Part to keep Part 2 focused on the agent loop. The classifier and retrieval services already exist on the `rag` branch and are not rebuilt here. A decision-log entry records this scope expansion with its justification.
- Authenticated session credentials are conveyed as an HTTP-only cookie. Visitors are assumed to use a current standards-compliant browser.
- The audit log is append-only by design; reporting and search tooling on top of it are Part 3 concerns.
- Embedding dimensionality matches the dimensionality already used by the RAG slice (768) so the same embedding client and the same vector index strategy apply.
- The widget primitives shipped here (record + host-token verification) are present so the agent's actor-context refusal can be tested end-to-end. The widget UI and the iframe-loader live in Part 3.
- The system's eval gate already exists from the RAG slice. Adding eval sets and floors for entity extraction and summarization extends that gate; it does not stand up a new mechanism.
- The current date is 2026-05-22. Dates referenced in the brief (the Friday demo, the `v0.1.0-week7` tag) sit outside this Part's scope.
