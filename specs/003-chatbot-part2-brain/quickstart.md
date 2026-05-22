# Quickstart — Chatbot Part 2 Brain

## Prerequisites

- Part 1 merged to `main` (commit `b942422f` or descendant). Local main is fast-forwarded.
- Docker stack already brought up via `docker compose up -d`. `/health` returns 200. The dev-stack `admin@example.com` admin exists (bootstrap from Part 1 Fix 2).
- Vault has `anthropic_api_key` populated (from Part 1 stack-up).

## Bring up Part 2 changes

```bash
git checkout 003-chatbot-part2-brain
docker compose build api
docker compose up -d api
curl -fsS http://localhost:8000/health > /dev/null && echo healthy
```

## Story 1 — Authed maintainer holds a tool-augmented conversation

```bash
# Login as the admin seeded by Part 1 Fix 2.
curl -s -c /tmp/mc.cookies -X POST http://localhost:8000/auth/login \
  -d 'username=admin@example.com&password=changeme-please' \
  -o /dev/null

# First chat turn — ask for an issue classification.
curl -s -b /tmp/mc.cookies -X POST http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{
    "message": "Please classify this issue: title=DataFrame.groupby crashes on empty input; body=Calling df.groupby() on an empty DataFrame raises ValueError instead of returning an empty grouped object."
  }' | jq

# Expect: assistant_message describes a category; tool_trace contains
# one entry with tool_name='classify_issue'.

# Follow-up in the same conversation_id from the response above.
curl -s -b /tmp/mc.cookies -X POST http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"conversation_id": "<UUID from above>", "message": "Now summarize it."}' | jq

# Expect: short-term memory carries the prior turn; assistant produces a coherent summary.
```

## Story 2 — Cross-conversation memory recall

Run via the integration test rather than curl (memory operations are easier to assert from inside the test fixture):

```bash
docker compose exec api pytest tests/integration/test_chatbot_memory_recall.py -v
# Expect: PASS
# What it does:
#   - As Alice, /chat in conversation A planting fact "Alice prefers Conventional Commits".
#   - Asserts the trace contains a write_memory call.
#   - As Alice, /chat in conversation B (new id) asking "what commit style do I use?"
#   - Asserts the trace contains a recall_memory call; assistant text references "Conventional Commits".
```

## Story 3 — Widget refusal

```bash
# Use the demo widget seeded by Part 1.
WIDGET_TOKEN="$(curl -s -b /tmp/mc.cookies http://localhost:8000/admin/widgets/new | jq -r .plaintext_token)"  # if such an admin path exists; otherwise read from vault_seed run logs

curl -s -X POST http://localhost:8000/widget/chat \
  -H "X-Widget-Token: $WIDGET_TOKEN" \
  -H 'Origin: http://localhost:8080' \
  -H 'Content-Type: application/json' \
  -d '{
    "widget_id": "<UUID>",
    "session_id": "visitor-1",
    "message": "Please remember that my repo is acme/widget."
  }' | jq

# Expect:
#   assistant_message contains a refusal pattern (e.g., "can't save things from this session").
#   tool_trace shows a write_memory attempt with is_error=true and kind=widget_actor_forbidden.
#   SELECT count(*) FROM chatbot_memories WHERE conversation_id IN (this widget conversation) = 0.
```

## Run the chatbot eval set

```bash
docker compose exec api python -m evals.chatbot.eval_chatbot --mode=fixture --check-thresholds
# Expect: exit 0, per-metric scores ≥ floors in eval_thresholds.yaml.chatbot

# Real-mode (operator-only — burns Anthropic API):
docker compose exec api python -m evals.chatbot.eval_chatbot --mode=real
```

## CI

After the existing eval steps, the new step "Chatbot eval gate" runs
`python -m evals.chatbot.eval_chatbot --mode=fixture --check-thresholds`
and exits the workflow with non-zero on any floor breach.
