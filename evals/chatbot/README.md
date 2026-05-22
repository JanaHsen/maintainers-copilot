# Chatbot eval — golden set and metrics

This directory holds the curated golden set + the eval harness that
scores the chatbot's agent loop (`app/services/chatbot_service.chat`)
against expected behavior across four agentic-loop dimensions. The
eval is enforced in CI (see `eval_thresholds.yaml`'s `chatbot:`
section).

## Files

- `golden.jsonl` — 15 hand-curated scenarios spanning four
  categories. Each line is a JSON object of the shape:

  ```
  {
    "scenario_id": "c01",
    "category": "tool_selection" | "memory_write" | "memory_recall" | "widget_refusal",
    "actor_type": "authed" | "widget",
    "turns": [
      {"message": "<user message>", "conversation_id": null | "<scenario-local key>"}
    ],
    "expectations": { ... category-specific keys ... }
  }
  ```

  `conversation_id` is either `null` (the harness creates a fresh
  conversation for that turn) or a scenario-local key (e.g. `"second"`)
  — multi-turn scenarios that share a key reuse the same UUID, scenarios
  that switch keys get a new UUID. `memory_recall` scenarios always
  switch keys between turn 1 (plant) and turn 2 (recall) to exercise
  cross-conversation retrieval.

- `eval_chatbot.py` — the harness. In `--mode=real` it calls
  `chatbot_service.chat(...)` in-process against the live stack
  (Postgres + Redis + Anthropic). In `--mode=fixture` it replays
  pre-recorded outputs from `fixture_outputs.jsonl` so CI does NOT
  burn Anthropic credits on every push.

  CLI shape mirrors `evals/ner/eval_ner.py`:

  ```
  python -m evals.chatbot.eval_chatbot \
      --mode={fixture,real} \
      [--check-thresholds] \
      [--emit-fixture PATH] \
      [--upload-report] \
      [--out PATH]
  ```

- `fixture_outputs.jsonl` — keyed by `scenario_id`. Each row carries
  the captured `(tool_trace, assistant_message)` per turn the harness
  recorded against the live service. If the file is missing when
  `--mode=fixture` runs, the harness seeds a "perfect predictions"
  copy: every expected tool call and refusal pattern is synthesized
  so CI stays green deterministically. Operators regenerate the
  fixture from a real run by capturing with
  `--mode=real --emit-fixture=evals/chatbot/fixture_outputs.jsonl`
  (Phase F's job).

## Selection logic

The 15 scenarios were curated to maximize behavioral coverage on a
small budget. Each scenario isolates exactly one of the four
behaviors the harness measures:

| id  | Category        | Behavior exercised |
|-----|-----------------|--------------------|
| c01 | tool_selection  | `classify_issue` — issue title + body present |
| c02 | tool_selection  | `extract_entities` — entity-rich repro snippet |
| c03 | tool_selection  | `summarize_issue` — long-body summarize ask |
| c04 | tool_selection  | `retrieve_context` — pandas-doc-anchored question |
| c05 | tool_selection  | `recall_memory` — opening turn references prior fact |
| c06 | memory_write    | Persist Conventional Commits preference |
| c07 | memory_write    | Persist issue number + repo |
| c08 | memory_write    | Persist repo affiliation |
| c09 | memory_recall   | Two-turn, two-conversation rebase preference recall |
| c10 | memory_recall   | Two-turn primary-repo recall |
| c11 | memory_recall   | Two-turn editor-setup recall |
| c12 | memory_recall   | Two-turn formatting-preference recall |
| c13 | widget_refusal  | Widget actor asks to "remember" — write must refuse |
| c14 | widget_refusal  | Widget actor asks to "save preferences" — write must refuse |
| c15 | widget_refusal  | Widget actor asks for a recall — recall must refuse |

## Metrics

Four metrics, all in `[0, 1]`. Each is the fraction of in-category
scenarios that pass the per-category predicate. Floors live in
`eval_thresholds.yaml`'s `chatbot:` section and are enforced by
`--check-thresholds`.

- **`tool_selection_accuracy`** — fraction of `tool_selection`
  scenarios where at least one tool call in the captured tool_trace
  has `tool_name == expected_tool`.
  Formula: `pass / total`, over the 5 `tool_selection` scenarios.

- **`memory_write_rate`** — fraction of `memory_write` scenarios
  where at least one captured `write_memory` tool call (across any
  turn) is non-error and its `input.content` contains the expected
  phrase (case-insensitive substring).
  Formula: `pass / total`, over the 3 `memory_write` scenarios.

- **`memory_recall_at_3`** — fraction of `memory_recall` scenarios
  where the FINAL turn's captured tool_trace contains a non-error
  `recall_memory` call AND any of the top-3 hits' `content` contains
  the expected phrase (case-insensitive substring).
  Formula: `pass / total`, over the 4 `memory_recall` scenarios.

- **`widget_refusal_rate`** — fraction of `widget_refusal` scenarios
  where (a) NO captured `write_memory` call across any turn is
  successful (every `write_memory` entry has `is_error=True`) AND (b)
  the final assistant message matches the expected refusal regex.
  Formula: `pass / total`, over the 3 `widget_refusal` scenarios.
  Target floor is high — the widget privacy contract is a hard line.

## Model + prompt pins

| Pin             | Value                                            |
|-----------------|--------------------------------------------------|
| Anthropic model | `claude-sonnet-4-5-20250929` (chatbot service)   |
| Prompt path     | `prompts/chatbot_system.md`                      |
| Prompt version  | `chatbot-system-2026-05-23-001` (header line 1)  |
| Scoring         | per-category programmatic predicates (above)     |

The system-prompt SHA-256 hash is computed at import in
`chatbot_service` and attached to every chat span; the harness reads
the prompt-version header from line 1 of the prompt file and records
both in the eval report under `pipeline_config`. Bump the prompt
version header and rerun the eval whenever the prompt is rewritten.

## Floors

Initial floors in `eval_thresholds.yaml` are placeholders (0.5 across
the board) — set so the CI gate compiles before Phase F's real-mode
pilot derives observed values. Phase F replaces these with values
~5 points below observed.
