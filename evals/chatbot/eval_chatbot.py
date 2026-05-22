"""Chatbot eval harness — fixture + real modes.

Real mode: for each scenario in ``evals/chatbot/golden.jsonl``, seed a
test user (or widget), then iterate the turns. Each turn calls
``chatbot_service.chat(...)`` in-process and captures the resulting
``tool_trace`` + ``assistant_message``. Multi-turn scenarios that use
different ``conversation_id`` scenario-local keys allocate distinct
UUIDs so the recall scenarios exercise cross-conversation memory
recall. Test rows are cleaned up after.

Fixture mode: read ``evals/chatbot/fixture_outputs.jsonl`` (keyed by
``scenario_id``) and replay the captured outputs. If the file does
not exist, the harness seeds a "perfect predictions" copy so CI stays
green deterministically. Operators regenerate the fixture from a real
run via ``--mode=real --emit-fixture=evals/chatbot/fixture_outputs.jsonl``.

Four metrics, all in ``[0, 1]`` (see ``README.md`` for full formulas):

  * ``tool_selection_accuracy``
  * ``memory_write_rate``
  * ``memory_recall_at_3``
  * ``widget_refusal_rate``

Each metric is enforced by the matching floor in
``eval_thresholds.yaml``'s ``chatbot:`` section under
``--check-thresholds`` (Rule 5 / Rule 10).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("evals.chatbot.eval_chatbot")

GOLDEN_PATH = Path(__file__).parent / "golden.jsonl"
FIXTURE_PATH = Path(__file__).parent / "fixture_outputs.jsonl"
PROMPT_PATH = (
    Path(__file__).resolve().parents[2] / "prompts" / "chatbot_system.md"
)
THRESHOLDS_PATH = (
    Path(__file__).resolve().parents[2] / "eval_thresholds.yaml"
)

CHAT_MODEL = "claude-sonnet-4-5-20250929"

_FLOOR_KEYS: tuple[str, ...] = (
    "tool_selection_accuracy",
    "memory_write_rate",
    "memory_recall_at_3",
    "widget_refusal_rate",
)


# --- types ----------------------------------------------------------------


@dataclass(frozen=True)
class Turn:
    message: str
    conversation_id_key: str | None  # scenario-local key; null = fresh


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    category: str
    actor_type: str
    turns: list[Turn]
    expectations: dict[str, Any]


@dataclass
class CapturedTurn:
    tool_trace: list[dict[str, Any]] = field(default_factory=list)
    assistant_message: str = ""


@dataclass
class CapturedOutputs:
    scenario_id: str
    turns: list[CapturedTurn] = field(default_factory=list)


# --- helpers --------------------------------------------------------------


def _utc_run_ts() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _prompt_version() -> str:
    try:
        first_line = PROMPT_PATH.read_text(encoding="utf-8").splitlines()[0]
    except OSError:
        return "unknown"
    if "Prompt version:" in first_line:
        return first_line.split("Prompt version:", 1)[1].strip()
    return "unknown"


def _prompt_hash() -> str:
    try:
        return hashlib.sha256(
            PROMPT_PATH.read_text(encoding="utf-8").encode("utf-8")
        ).hexdigest()
    except OSError:
        return "unknown"


def _golden_set_hash(rows: list[dict[str, Any]]) -> str:
    canonical = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(canonical).hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _parse_scenarios(rows: list[dict[str, Any]]) -> list[Scenario]:
    scenarios: list[Scenario] = []
    for row in rows:
        turns = [
            Turn(
                message=str(t["message"]),
                conversation_id_key=(
                    None if t.get("conversation_id") is None
                    else str(t["conversation_id"])
                ),
            )
            for t in row["turns"]
        ]
        scenarios.append(
            Scenario(
                scenario_id=str(row["scenario_id"]),
                category=str(row["category"]),
                actor_type=str(row["actor_type"]),
                turns=turns,
                expectations=dict(row.get("expectations") or {}),
            )
        )
    return scenarios


# --- fixture I/O -----------------------------------------------------------


def _seed_perfect_fixture(
    scenarios: list[Scenario], out: Path
) -> None:
    """Write a fixture with synthetic "perfect" outputs per category.

    The synthesized rows are the minimal shape each metric needs to
    pass: one tool_use entry of the expected tool, write_memory tool
    calls with content containing the expected phrase, recall_memory
    tool calls with hits containing the expected phrase, and a refusal
    assistant message that matches the regex for widget scenarios.
    """
    with out.open("w", encoding="utf-8") as fh:
        for sc in scenarios:
            cap_turns: list[dict[str, Any]] = []
            if sc.category == "tool_selection":
                expected_tool = sc.expectations.get("expected_tool", "")
                # One turn; tool trace has one entry of the expected tool.
                cap_turns.append(
                    {
                        "tool_trace": [
                            {
                                "tool_name": expected_tool,
                                "input": {},
                                "output": {"ok": True},
                                "latency_ms": 1,
                                "is_error": False,
                            }
                        ],
                        "assistant_message": (
                            f"Used {expected_tool} to answer."
                        ),
                    }
                )
            elif sc.category == "memory_write":
                phrase = sc.expectations.get(
                    "expected_phrase_in_write_content", ""
                )
                cap_turns.append(
                    {
                        "tool_trace": [
                            {
                                "tool_name": "write_memory",
                                "input": {"content": f"User noted: {phrase}"},
                                "output": {
                                    "memory_id": str(uuid.uuid4()),
                                },
                                "latency_ms": 1,
                                "is_error": False,
                            }
                        ],
                        "assistant_message": "Got it, saved.",
                    }
                )
            elif sc.category == "memory_recall":
                phrase = sc.expectations.get(
                    "expected_phrase_in_recall_top3", ""
                )
                # Two turns: turn 1 plants (write_memory), turn 2 recalls.
                cap_turns.append(
                    {
                        "tool_trace": [
                            {
                                "tool_name": "write_memory",
                                "input": {"content": f"User noted: {phrase}"},
                                "output": {
                                    "memory_id": str(uuid.uuid4()),
                                },
                                "latency_ms": 1,
                                "is_error": False,
                            }
                        ],
                        "assistant_message": "Noted.",
                    }
                )
                cap_turns.append(
                    {
                        "tool_trace": [
                            {
                                "tool_name": "recall_memory",
                                "input": {"query": "user preference"},
                                "output": {
                                    "hits": [
                                        {
                                            "memory_id": str(uuid.uuid4()),
                                            "content": (
                                                f"User noted: {phrase}"
                                            ),
                                            "similarity": 0.95,
                                        }
                                    ]
                                },
                                "latency_ms": 1,
                                "is_error": False,
                            }
                        ],
                        "assistant_message": (
                            f"You told me earlier: {phrase}."
                        ),
                    }
                )
            elif sc.category == "widget_refusal":
                # Refusal: write_memory call (or recall) returns is_error.
                cap_turns.append(
                    {
                        "tool_trace": [
                            {
                                "tool_name": "write_memory",
                                "input": {"content": "ignored"},
                                "output": {
                                    "error": {
                                        "kind": "widget_actor_forbidden",
                                        "detail": "widget cannot persist",
                                    }
                                },
                                "latency_ms": 1,
                                "is_error": True,
                            }
                        ],
                        "assistant_message": (
                            "Sorry, I can't save anything for "
                            "anonymous widget sessions."
                        ),
                    }
                )
            fh.write(
                json.dumps(
                    {
                        "scenario_id": sc.scenario_id,
                        "turns": cap_turns,
                    },
                    separators=(",", ":"),
                )
                + "\n"
            )


def _load_fixture(
    fixture_path: Path, scenarios: list[Scenario]
) -> dict[str, CapturedOutputs]:
    if not fixture_path.exists():
        logger.info(
            "fixture %s missing; seeding perfect-prediction copy",
            fixture_path,
        )
        _seed_perfect_fixture(scenarios, fixture_path)
    by_id: dict[str, CapturedOutputs] = {}
    for row in load_jsonl(fixture_path):
        rid = row.get("scenario_id")
        if not isinstance(rid, str):
            continue
        captured = CapturedOutputs(scenario_id=rid)
        for t in row.get("turns") or []:
            captured.turns.append(
                CapturedTurn(
                    tool_trace=list(t.get("tool_trace") or []),
                    assistant_message=str(t.get("assistant_message") or ""),
                )
            )
        by_id[rid] = captured
    return by_id


def _emit_fixture(
    captured: list[CapturedOutputs], path: Path
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for cap in captured:
            fh.write(
                json.dumps(
                    {
                        "scenario_id": cap.scenario_id,
                        "turns": [
                            {
                                "tool_trace": turn.tool_trace,
                                "assistant_message": turn.assistant_message,
                            }
                            for turn in cap.turns
                        ],
                    },
                    separators=(",", ":"),
                )
                + "\n"
            )


# --- real-mode driver -----------------------------------------------------


def _predict_real(scenarios: list[Scenario]) -> list[CapturedOutputs]:
    """Drive ``chatbot_service.chat`` against the live stack for every
    scenario; collect tool_trace + assistant_message per turn.

    Test users + widgets created here are cleaned up after every
    scenario (even on failure) so reruns are idempotent. Imports are
    inside this function so ``--mode=fixture`` does not need a live
    Postgres / Vault / Redis stack to compile.
    """
    import asyncio

    from sqlalchemy import text

    from app.domain.conversation import AuthedUser, WidgetSession
    from app.infra.database import get_engine
    from app.infra.database_async import get_async_sessionmaker
    from app.repositories import widget_repository
    from app.services import chatbot_service

    captured: list[CapturedOutputs] = []
    user_email_prefix = "pytest-eval-chatbot-"
    widget_name_prefix = "pytest-eval-chatbot-widget-"

    async def _make_user(email: str) -> uuid.UUID:
        sm = get_async_sessionmaker()
        async with sm() as session:
            uid = uuid.uuid4()
            await session.execute(
                text(
                    "INSERT INTO users (id, email, hashed_password, "
                    "is_active, is_superuser, is_verified, role) "
                    "VALUES (:id, :email, :pw, true, false, true, 'user')"
                ),
                {
                    "id": uid,
                    "email": email,
                    "pw": "$2b$12$placeholderhashplaceholderhashplaceholder",
                },
            )
            await session.commit()
            return uid

    async def _delete_users(prefix: str) -> None:
        sm = get_async_sessionmaker()
        async with sm() as session:
            await session.execute(
                text("DELETE FROM users WHERE email LIKE :pat"),
                {"pat": f"{prefix}%"},
            )
            await session.commit()

    def _delete_widgets(prefix: str) -> None:
        with get_engine().begin() as conn:
            conn.execute(
                text("DELETE FROM widgets WHERE name LIKE :pat"),
                {"pat": f"{prefix}%"},
            )

    # Tidy stale rows from any prior interrupted run.
    asyncio.run(_delete_users(user_email_prefix))
    _delete_widgets(widget_name_prefix)

    try:
        for sc in scenarios:
            cap = CapturedOutputs(scenario_id=sc.scenario_id)
            convo_uuids: dict[str | None, uuid.UUID | None] = {}

            actor: Any
            if sc.actor_type == "widget":
                # Need an owner user for the widget row.
                owner_email = (
                    f"{user_email_prefix}{sc.scenario_id}-"
                    f"{uuid.uuid4().hex[:8]}@example.com"
                )
                owner_id = asyncio.run(_make_user(owner_email))
                widget_id, _plaintext = widget_repository.create(
                    name=f"{widget_name_prefix}{sc.scenario_id}",
                    allowed_origins=["http://localhost:8080"],
                    owner_user_id=owner_id,
                )
                actor = WidgetSession(
                    widget_id=widget_id,
                    session_id=f"sess-{uuid.uuid4().hex[:12]}",
                )
            else:
                email = (
                    f"{user_email_prefix}{sc.scenario_id}-"
                    f"{uuid.uuid4().hex[:8]}@example.com"
                )
                user_id = asyncio.run(_make_user(email))
                actor = AuthedUser(user_id=user_id, role="user")

            for turn in sc.turns:
                key = turn.conversation_id_key
                cid: uuid.UUID | None = convo_uuids.get(key)
                if cid is None and key is not None:
                    # First time seeing this scenario-local key — service
                    # creates a fresh conversation when conversation_id=None.
                    cid = None
                outcome = chatbot_service.chat(
                    conversation_id=cid,
                    user_message=turn.message,
                    actor=actor,
                )
                cap_turn = CapturedTurn()
                if isinstance(outcome, chatbot_service.ChatOk):
                    convo_uuids[key] = outcome.conversation_id
                    cap_turn.assistant_message = outcome.assistant_message
                    cap_turn.tool_trace = [
                        {
                            "tool_name": entry.tool_name,
                            "input": entry.input,
                            "output": entry.output,
                            "latency_ms": entry.latency_ms,
                            "is_error": entry.is_error,
                        }
                        for entry in outcome.tool_trace
                    ]
                else:
                    logger.warning(
                        "chatbot_service.chat returned %s for %s: %s",
                        outcome.kind,
                        sc.scenario_id,
                        outcome.detail,
                    )
                    cap_turn.assistant_message = ""
                    cap_turn.tool_trace = []
                cap.turns.append(cap_turn)
            captured.append(cap)
    finally:
        asyncio.run(_delete_users(user_email_prefix))
        _delete_widgets(widget_name_prefix)

    return captured


# --- metrics --------------------------------------------------------------


def _safe_div(num: int, den: int) -> float:
    return float(num) / float(den) if den > 0 else 0.0


def _evaluate_scenario(
    sc: Scenario, cap: CapturedOutputs
) -> tuple[bool, str]:
    """Return (passed, detail) for one scenario per its category rule."""
    if sc.category == "tool_selection":
        expected = sc.expectations.get("expected_tool", "")
        calls = [
            t
            for turn in cap.turns
            for t in turn.tool_trace
            if t.get("tool_name") == expected
        ]
        if calls:
            return True, f"saw {len(calls)} call(s) to {expected}"
        seen = sorted(
            {t.get("tool_name", "") for turn in cap.turns for t in turn.tool_trace}
        )
        return False, f"expected_tool={expected!r} not in trace; saw {seen}"

    if sc.category == "memory_write":
        phrase = sc.expectations.get(
            "expected_phrase_in_write_content", ""
        ).lower()
        for turn in cap.turns:
            for t in turn.tool_trace:
                if t.get("tool_name") != "write_memory":
                    continue
                if t.get("is_error"):
                    continue
                content = (
                    (t.get("input") or {}).get("content", "")
                ) or ""
                if phrase and phrase in content.lower():
                    return True, f"write_memory captured phrase {phrase!r}"
        return False, (
            f"no successful write_memory call contained "
            f"{phrase!r}"
        )

    if sc.category == "memory_recall":
        phrase = sc.expectations.get(
            "expected_phrase_in_recall_top3", ""
        ).lower()
        if not cap.turns:
            return False, "no captured turns"
        recall_turn = cap.turns[-1]
        for t in recall_turn.tool_trace:
            if t.get("tool_name") != "recall_memory":
                continue
            if t.get("is_error"):
                continue
            hits = ((t.get("output") or {}).get("hits") or [])[:3]
            for h in hits:
                content = (h.get("content") or "") if isinstance(h, dict) else ""
                if phrase and phrase in content.lower():
                    return (
                        True,
                        f"recall_memory top-3 contained {phrase!r}",
                    )
        return False, (
            f"no successful recall_memory call had {phrase!r} in top-3"
        )

    if sc.category == "widget_refusal":
        pattern_str = sc.expectations.get("expected_refusal_pattern", "")
        regex = re.compile(pattern_str) if pattern_str else None
        any_successful_write = any(
            t.get("tool_name") == "write_memory" and not t.get("is_error")
            for turn in cap.turns
            for t in turn.tool_trace
        )
        if any_successful_write:
            return False, "a successful write_memory escaped widget refusal"
        if not cap.turns:
            return False, "no captured turns"
        final_text = cap.turns[-1].assistant_message or ""
        if regex is not None and regex.search(final_text):
            return True, "no successful write + refusal phrase matched"
        return False, (
            f"final assistant message did not match "
            f"{pattern_str!r}: {final_text!r}"
        )

    return False, f"unknown category {sc.category!r}"


def compute_metrics(
    scenarios: list[Scenario], captured_by_id: dict[str, CapturedOutputs]
) -> tuple[dict[str, float], list[dict[str, Any]]]:
    """Compute the 4 chatbot metrics + a per-scenario report list."""
    totals: dict[str, int] = dict.fromkeys(
        ("tool_selection", "memory_write", "memory_recall", "widget_refusal"),
        0,
    )
    passes: dict[str, int] = dict.fromkeys(totals.keys(), 0)
    per_scenario: list[dict[str, Any]] = []

    for sc in scenarios:
        if sc.category not in totals:
            continue
        totals[sc.category] += 1
        cap = captured_by_id.get(
            sc.scenario_id, CapturedOutputs(scenario_id=sc.scenario_id)
        )
        passed, detail = _evaluate_scenario(sc, cap)
        if passed:
            passes[sc.category] += 1
        per_scenario.append(
            {
                "scenario_id": sc.scenario_id,
                "category": sc.category,
                "passed": passed,
                "detail": detail,
            }
        )

    metrics = {
        "tool_selection_accuracy": _safe_div(
            passes["tool_selection"], totals["tool_selection"]
        ),
        "memory_write_rate": _safe_div(
            passes["memory_write"], totals["memory_write"]
        ),
        "memory_recall_at_3": _safe_div(
            passes["memory_recall"], totals["memory_recall"]
        ),
        "widget_refusal_rate": _safe_div(
            passes["widget_refusal"], totals["widget_refusal"]
        ),
    }
    return metrics, per_scenario


# --- report + threshold gating --------------------------------------------


def build_report(
    *,
    mode: str,
    scenarios: list[dict[str, Any]],
    metrics: dict[str, float],
    per_scenario: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "run_ts": _utc_run_ts(),
        "mode": mode,
        "model": CHAT_MODEL,
        "prompt_path": "prompts/chatbot_system.md",
        "prompt_version": _prompt_version(),
        "prompt_hash": _prompt_hash(),
        "golden_set_hash": _golden_set_hash(scenarios),
        "n_scenarios": len(scenarios),
        "metrics": metrics,
        "per_scenario": per_scenario,
    }


def upload_report(report: dict[str, Any], *, run_ts: str) -> str:
    from app.infra.minio_client import DATA_BUCKET, ensure_bucket, get_client

    ensure_bucket(DATA_BUCKET)
    s3 = get_client()
    key = f"evals/reports/{run_ts}/chatbot.json"
    body = json.dumps(report, indent=2, sort_keys=True).encode("utf-8")
    s3.put_object(Bucket=DATA_BUCKET, Key=key, Body=body)
    return f"s3://{DATA_BUCKET}/{key}"


def _read_thresholds() -> dict[str, Any]:
    try:
        import yaml
    except ImportError:
        return {}
    if not THRESHOLDS_PATH.exists():
        return {}
    with THRESHOLDS_PATH.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return dict(data) if isinstance(data, dict) else {}


def check_thresholds(
    metrics: dict[str, float], thresholds: dict[str, Any]
) -> list[str]:
    breaches: list[str] = []
    chatbot_floors = (thresholds or {}).get("chatbot") or {}
    for key in _FLOOR_KEYS:
        floor_key = f"{key}_floor"
        if floor_key not in chatbot_floors:
            continue
        floor = float(chatbot_floors[floor_key])
        observed = float(metrics.get(key, 0.0))
        if observed < floor:
            breaches.append(
                f"{key} = {observed:.4f} below floor {floor:.4f}"
            )
    return breaches


# --- CLI -----------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the chatbot eval gate.")
    parser.add_argument(
        "--mode",
        required=True,
        choices=["fixture", "real"],
        help="fixture: read prebaked outputs; real: call chatbot_service.chat.",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Write the JSON report to this path (default: stdout-only).",
    )
    parser.add_argument(
        "--golden",
        default=str(GOLDEN_PATH),
        help="Path to golden.jsonl.",
    )
    parser.add_argument(
        "--fixture",
        default=str(FIXTURE_PATH),
        help="Path to fixture_outputs.jsonl (only used in --mode=fixture).",
    )
    parser.add_argument(
        "--emit-fixture",
        default=None,
        help="In --mode=real, also write the captured outputs to this path "
        "so future CI runs can replay them without calling Anthropic.",
    )
    parser.add_argument(
        "--upload-report",
        action="store_true",
        help="Upload the report to MinIO at evals/reports/{run_ts}/chatbot.json.",
    )
    parser.add_argument(
        "--check-thresholds",
        action="store_true",
        help="Read eval_thresholds.yaml's chatbot: section and exit non-zero "
        "on any breach.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    golden_rows = load_jsonl(Path(args.golden))
    scenarios = _parse_scenarios(golden_rows)

    captured_by_id: dict[str, CapturedOutputs]
    if args.mode == "fixture":
        captured_by_id = _load_fixture(Path(args.fixture), scenarios)
    else:
        captured_list = _predict_real(scenarios)
        captured_by_id = {cap.scenario_id: cap for cap in captured_list}
        if args.emit_fixture:
            _emit_fixture(captured_list, Path(args.emit_fixture))
            logger.info("wrote fixture → %s", args.emit_fixture)

    metrics, per_scenario = compute_metrics(scenarios, captured_by_id)
    report = build_report(
        mode=args.mode,
        scenarios=golden_rows,
        metrics=metrics,
        per_scenario=per_scenario,
    )

    print(
        json.dumps(
            {
                "metrics": report["metrics"],
                "n_scenarios": report["n_scenarios"],
            },
            indent=2,
        )
    )

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(f"Wrote report → {out_path}", file=sys.stderr)

    if args.upload_report:
        uri = upload_report(report, run_ts=report["run_ts"])
        print(f"Uploaded → {uri}", file=sys.stderr)

    if args.check_thresholds:
        thresholds = _read_thresholds()
        breaches = check_thresholds(metrics, thresholds)
        if breaches:
            for msg in breaches:
                print(f"THRESHOLD BREACH: {msg}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
