"""One-shot helper: for each operator_labeled=true row in golden.jsonl,
drive /retrieve to get candidate chunks, then print them so the agent
can hand-pick parent ids."""
import json, urllib.request, sys

API = "http://localhost:8000/retrieve"

PLACEHOLDERS = ["q03", "q07", "q12", "q19", "q24"]

def retrieve(question, k=10):
    body = json.dumps({"question": question, "k": k}).encode()
    req = urllib.request.Request(API, data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)["chunks"]

rows = [json.loads(l) for l in open("/tmp/golden.jsonl") if l.strip()]
for r in rows:
    if r["question_id"] not in PLACEHOLDERS:
        continue
    print(f"\n[{r['question_id']}] {r['question']}")
    print(f"    ideal: {r['ideal_answer']}")
    print(f"    notes: {r['notes']}")
    chunks = retrieve(r["question"], k=10)
    for i, c in enumerate(chunks):
        snippet = c["content"][:160].replace("\n", " ")
        cid = c["chunk_id"]
        print(f"  #{i+1} child_id={cid} {c['source_type']}:{c['source_id']} score={c['score']:.3f}")
        print(f"     {snippet}")
