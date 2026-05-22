# Embed the RAG corpus on Colab A100

Operator-facing draft. Each `###` block is one cell — paste into Colab in order.

## Pre-flight on the local host (do this **before** opening Colab)

The local stack must be reachable from Colab through an ngrok HTTPS tunnel pointed at MinIO. Postgres stays behind the firewall — Colab writes back to MinIO and a local importer drains it into `rag_chunks`.

```bash
# inside WSL, on the local host
docker compose start minio   # if you stopped it
ngrok http --domain=<your-stable-domain>.ngrok-free.app 9000
# leave running. The HTTPS URL it prints (e.g. https://abc.ngrok-free.app) is
# the value of NGROK_MINIO_URL below.
```

Vault is not exposed — pass MinIO creds directly. Default dev creds:
- `minioadmin` / `dev_minio_password` (from `secret/maintainers-copilot.minio_root_password`).

The Colab side needs:
- An ngrok HTTPS URL pointing at port 9000.
- Read+write access to the bucket `maintainers-copilot`.
- No PAT (issues are already cached at `rag/held_out_issues/v1-full-20260521T2327Z/`).

---

### Cell 1 — markdown

```
# Embed the v1-full RAG corpus on A100

Pulls the cached held-out issues from MinIO (via ngrok), sparse-clones
pandas-dev/pandas (free, no auth), re-chunks deterministically using the
project's parent-document chunker, embeds children with
BAAI/bge-base-en-v1.5 on GPU, and writes the results back to MinIO as
two parquet files. A local script (`scripts/rag/import_embeddings.py`)
drains them into `rag_chunks`.
```

### Cell 2 — code (install deps)

```python
!pip install -q sentence-transformers==2.7.0 boto3 pyarrow pandas tqdm
import torch
assert torch.cuda.is_available(), "Switch the runtime to A100/L4/T4 GPU"
print(torch.cuda.get_device_name(0))
```

### Cell 3 — code (configuration — fill in the two NGROK / cred values)

```python
import os

# --- fill these in --------------------------------------------------------
NGROK_MINIO_URL  = "https://<your-stable-domain>.ngrok-free.app"
MINIO_ROOT_USER  = "minioadmin"
MINIO_ROOT_PASS  = "dev_minio_password"
# --------------------------------------------------------------------------

CORPUS_RUN_ID    = "v1-full-20260521T2327Z"
DATASET_RUN_ID   = "20260519T133455Z"
PANDAS_REPO_REF  = "main"   # matches what the local build used
DATA_BUCKET      = "maintainers-copilot"
EMBED_MODEL_ID   = "BAAI/bge-base-en-v1.5"
EMBED_DIM        = 768
EMBED_BATCH_SIZE = 128
```

### Cell 4 — code (S3 client over the ngrok tunnel)

```python
import boto3
from botocore.client import Config

s3 = boto3.client(
    "s3",
    endpoint_url=NGROK_MINIO_URL,
    aws_access_key_id=MINIO_ROOT_USER,
    aws_secret_access_key=MINIO_ROOT_PASS,
    region_name="us-east-1",
    config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
)

# sanity ping — should list at least the existing rag/ prefix
resp = s3.list_objects_v2(Bucket=DATA_BUCKET, Prefix="rag/", MaxKeys=5)
print("ngrok S3 reachable; sample keys:")
for k in (resp.get("Contents") or []):
    print(f"  - {k['Key']}")
```

### Cell 5 — code (clone the maintainers-copilot repo to import the chunkers)

```python
import subprocess, sys, os, importlib

REPO_DIR = "/content/maintainers-copilot"
if not os.path.isdir(REPO_DIR):
    subprocess.check_call([
        "git", "clone", "--depth=1", "--branch=rag",
        "https://github.com/JanaHsen/maintainers-copilot.git", REPO_DIR,
    ])
sys.path.insert(0, REPO_DIR)

# only the pure modules are needed — no vault, no postgres, no fastapi.
from scripts.rag.chunk_parent_document import chunk_source, ParentChunk
print("imported chunk_source OK")
```

### Cell 6 — code (pull the held-out issues cache from MinIO)

```python
import json
from datetime import datetime

prefix = f"rag/held_out_issues/{CORPUS_RUN_ID}"

meta = json.loads(
    s3.get_object(Bucket=DATA_BUCKET, Key=f"{prefix}/cache_meta.json")["Body"].read()
)
print(f"cache_meta: {len(meta['batches'])} batches, "
      f"excluded={len(meta['excluded_issue_numbers'])}, "
      f"dropped_no_maint={len(meta['dropped_no_maintainer'])}, "
      f"fetched_at={meta['fetched_at']}")

issue_nodes = []
for key in meta["batches"]:
    body = s3.get_object(Bucket=DATA_BUCKET, Key=key)["Body"].read().decode()
    for line in body.splitlines():
        if line.strip():
            issue_nodes.append(json.loads(line))
print(f"loaded {len(issue_nodes)} held-out issue nodes from cache")
```

### Cell 7 — code (sparse-clone pandas docs — same pattern as scripts/rag/fetch_docs.py)

```python
import subprocess, os
from pathlib import Path

PANDAS_DIR = Path("/content/pandas")
if not PANDAS_DIR.exists():
    subprocess.check_call([
        "git", "clone", "--depth=1", "--filter=blob:none", "--sparse",
        "--branch", PANDAS_REPO_REF,
        "https://github.com/pandas-dev/pandas.git", str(PANDAS_DIR),
    ])
    subprocess.check_call([
        "git", "-C", str(PANDAS_DIR), "sparse-checkout", "set", "--no-cone",
        "README.md", "CONTRIBUTING.md", "doc/source/",
    ])

# Mirror scripts/rag/fetch_docs.py constants exactly.
ROOT_FILES = ("README.md", "CONTRIBUTING.md")
DOC_GLOB   = "doc/source/**/*.rst"

root_paths = [PANDAS_DIR / n for n in ROOT_FILES if (PANDAS_DIR / n).is_file()]
doc_paths  = sorted(PANDAS_DIR.glob(DOC_GLOB))
print(f"found {len(root_paths)} root + {len(doc_paths)} doc/source/**/*.rst candidate files")
```

### Cell 8 — code (re-chunk docs + issues — ID-stable with the local build)

```python
from datetime import datetime, timezone, UTC
import subprocess
from scripts.rag.fetch_docs import _is_code_heavy, ROOT_FILES, DOC_GLOB  # noqa: F401

def _git_commit_timestamp(rel_path: str):
    """Last-commit timestamp for `rel_path`, identical logic to fetch_docs._file_timestamp."""
    try:
        out = subprocess.check_output(
            ["git", "log", "-1", "--format=%cI", "--", rel_path],
            cwd=str(PANDAS_DIR), stderr=subprocess.DEVNULL, text=True,
        ).strip()
        if out:
            return datetime.fromisoformat(out)
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
        pass
    mtime = (PANDAS_DIR / rel_path).stat().st_mtime
    return datetime.fromtimestamp(mtime, tz=UTC)

# --- docs ---------------------------------------------------------------
docs_sources = []
docs_skipped = 0
for path in root_paths + doc_paths:
    rel = path.relative_to(PANDAS_DIR).as_posix()  # e.g. "doc/source/user_guide/groupby.rst"
    raw = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix == ".rst" and _is_code_heavy(raw):
        docs_skipped += 1
        continue
    docs_sources.append((rel, _git_commit_timestamp(rel), raw))
print(f"docs kept={len(docs_sources)} skipped={docs_skipped}")

# --- issues -----------------------------------------------------------
# These two helpers must mirror scripts/rag/build_corpus.py::_issue_to_text
# byte-for-byte — chunk IDs hash the raw text, so any drift here splinters
# IDs from the local-build rows already in rag_chunks.
def _parse_dt(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))

def _issue_to_text(node):
    parts = [f"# {node.get('title','')}", "", node.get('body') or ""]
    comments = (node.get("comments") or {}).get("nodes") or []
    if comments:
        parts.append("")
        parts.append("## Comments")
        for c in comments:
            created = _parse_dt(c.get("createdAt") or "1970-01-01T00:00:00Z").isoformat()
            parts.append("")
            parts.append(f"### {c.get('authorAssociation','NONE')} @ {created}")
            parts.append("")
            parts.append(c.get("body") or "")
    return "\n".join(parts).strip()

issue_sources = []
for node in issue_nodes:
    issue_sources.append((
        str(node["number"]),
        _parse_dt(node["closedAt"]),
        _issue_to_text(node),
    ))
print(f"issues={len(issue_sources)}")

# --- chunk --------------------------------------------------------------
all_parents = []
for src_id, ts, raw in docs_sources:
    all_parents.extend(chunk_source(
        corpus_run_id=CORPUS_RUN_ID, source_type="docs",
        source_id=src_id, source_timestamp=ts, raw_text=raw,
    ))
for src_id, ts, raw in issue_sources:
    all_parents.extend(chunk_source(
        corpus_run_id=CORPUS_RUN_ID, source_type="issues",
        source_id=src_id, source_timestamp=ts, raw_text=raw,
    ))
n_children = sum(len(p.children) for p in all_parents)
print(f"chunked: {len(all_parents)} parents, {n_children} children")
# expected for v1-full-20260521T2327Z: ~46,739 parents, ~78,104 children
```

### Cell 9 — code (load BGE on GPU)

```python
from sentence_transformers import SentenceTransformer
model = SentenceTransformer(EMBED_MODEL_ID, device="cuda")
dim = model.get_sentence_embedding_dimension()
assert dim == EMBED_DIM, f"unexpected dim={dim}"
print(f"loaded {EMBED_MODEL_ID} on {model.device}, dim={dim}")
```

### Cell 10 — code (embed children in batches of 128, with progress bar)

```python
import numpy as np
from tqdm.auto import tqdm

# Flatten children with stable (parent_id, child) refs.
flat_children = []
for parent in all_parents:
    for child in parent.children:
        flat_children.append((parent, child))

texts = [c.content for _, c in flat_children]
print(f"embedding {len(texts)} sentences on GPU...")

embeddings = model.encode(
    texts,
    batch_size=EMBED_BATCH_SIZE,
    convert_to_numpy=True,
    normalize_embeddings=True,
    show_progress_bar=True,
)
print(f"embeddings shape: {embeddings.shape}")  # expect (~78104, 768)
```

### Cell 11 — code (write parents.parquet + children.parquet to MinIO)

```python
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import io

# --- parents (no embedding column) -----------------------------------
parent_rows = [{
    "id": p.id,
    "kind": "parent",
    "parent_id": p.id,            # mirrors the embed_and_upsert convention
    "content": p.content,
    "source_type": p.source_type,
    "source_id": p.source_id,
    "source_timestamp": p.source_timestamp,
    "section_path": p.section_path,
    "child_index": 0,
    "parent_index": p.parent_index,
    "corpus_run_id": p.corpus_run_id,
} for p in all_parents]
parents_df = pd.DataFrame(parent_rows)

# --- children (with embeddings) --------------------------------------
child_rows = []
for (parent, child), vec in zip(flat_children, embeddings, strict=True):
    child_rows.append({
        "id": child.id,
        "kind": "child",
        "parent_id": child.parent_id,
        "content": child.content,
        "embedding": vec.astype("float32").tolist(),
        "source_type": parent.source_type,
        "source_id": parent.source_id,
        "source_timestamp": parent.source_timestamp,
        "section_path": child.section_path,
        "child_index": child.child_index,
        "parent_index": parent.parent_index,
        "corpus_run_id": parent.corpus_run_id,
    })
children_df = pd.DataFrame(child_rows)

# --- upload ------------------------------------------------------------
def _put_parquet(df, key):
    buf = io.BytesIO()
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), buf, compression="snappy")
    buf.seek(0)
    s3.put_object(Bucket=DATA_BUCKET, Key=key, Body=buf.getvalue())
    return key, len(buf.getvalue())

prefix = f"rag/embeddings/{CORPUS_RUN_ID}"
k1, sz1 = _put_parquet(parents_df,  f"{prefix}/parents.parquet")
k2, sz2 = _put_parquet(children_df, f"{prefix}/children.parquet")
print(f"uploaded:\n  {k1} ({sz1/1e6:.1f} MB)\n  {k2} ({sz2/1e6:.1f} MB)")
print(f"parents={len(parents_df)} children={len(children_df)}")
```

### Cell 12 — markdown (handoff)

```
## Local follow-up

On the host:

```bash
docker compose start postgres vault minio   # if anything was stopped
uv run python scripts/rag/import_embeddings.py --corpus-run-id v1-full-20260521T2327Z
```

The importer reads both parquets from MinIO and bulk-INSERTs into
`rag_chunks` with `ON CONFLICT DO NOTHING`, so the ~1,000 parents +
~2,737 children that already landed from the partial local run are
no-op skips. Expected final count: 46,739 parents + 78,104 children
under `corpus_run_id=v1-full-20260521T2327Z`.

After the importer reports done, restart api + model-server, set
`RAG_CORPUS_RUN_ID` in `.env`, and `curl POST /retrieve` to verify.
```

---

## Why this shape (instead of ngrok to postgres)

- **Postgres stays behind the firewall.** Only MinIO needs an ngrok HTTPS endpoint, and even that is read-mostly with append-style writes.
- **Resumable.** If Colab disconnects mid-embed, the issue cache + chunker logic are reproducible on a fresh runtime. The model+chunks parquets are uploaded atomically per parquet.
- **One round-trip per parquet** instead of N database writes across the internet — much faster network-wise than streaming INSERTs through ngrok.
- **No vault leakage.** MinIO root creds are passed directly into the notebook; the project's vault stays untouched.
