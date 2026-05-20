"""Score the regex NER extractor against the hand-curated sample.

Run from the repo root:

    uv run python evals/ner/score.py

Reports precision = (extracted entities matching the hand-curated truth
set) / (total extracted entities). The truth set in ``sample.jsonl`` lists
only the entities that are actually code-shaped — anything the extractor
emits that isn't in that list is a false positive.
"""

from __future__ import annotations

import json
from pathlib import Path

from model_server.ner import extract

SAMPLE_PATH = Path(__file__).parent / "sample.jsonl"


def main() -> None:
    total_extracted = 0
    true_positives = 0
    false_positive_log: list[tuple[str, str, str]] = []

    with SAMPLE_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            text = row["text"]
            truth = {(e["text"], e["type"]) for e in row["expected"]}
            extracted = extract(text)
            for ent in extracted:
                total_extracted += 1
                if (ent.text, ent.type) in truth:
                    true_positives += 1
                else:
                    false_positive_log.append((row["id"], ent.text, ent.type))

    if total_extracted == 0:
        print("No extractions on the sample; precision undefined.")
        return

    precision = true_positives / total_extracted
    false_positives = total_extracted - true_positives
    print(
        f"extracted={total_extracted}  TP={true_positives}  FP={false_positives}"
    )
    print(f"precision={precision:.4f}")
    if false_positive_log:
        print("\nFalse positives:")
        for sample_id, text, etype in false_positive_log:
            print(f"  [{sample_id}] {text!r} ({etype})")


if __name__ == "__main__":
    main()
