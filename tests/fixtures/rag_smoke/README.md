# RAG smoke fixture

Tiny stand-in for the real `doc/source/` tree and a held-out issue
slice. Five RST-flavored doc files under `docs/` and five issue JSON
files under `issues/`. `scripts/rag/fetch_docs.py` / `fetch_issues_held_out.py`
read this directory directly when invoked with `--fixture`, bypassing
the GitHub fetch + sparse-checkout cache.

## Layout

```text
tests/fixtures/rag_smoke/
├── README.md
├── docs/
│   ├── intro.rst
│   ├── groupby.rst
│   ├── timeseries.rst
│   ├── indexing.rst
│   └── io_csv.rst
└── issues/
    ├── 9001.json
    ├── 9002.json
    ├── 9003.json
    ├── 9004.json
    └── 9005.json
```

Files are kept under 2KB each so the full smoke (fetch → chunk →
embed → bulk-upsert) completes in <30s on CPU with the pre-cached
embedding model.

## Issue JSON shape

Mirror of the GraphQL response shape consumed by
`scripts/rag/fetch_issues_held_out.py`:

```jsonc
{
  "number": 9001,
  "title": "…",
  "body":  "…",
  "closedAt": "2024-08-12T15:30:00Z",
  "comments": [
    {
      "body": "…",
      "createdAt": "2024-08-12T16:01:00Z",
      "authorAssociation": "MEMBER"   // MEMBER/OWNER/COLLABORATOR -> kept by T007 filter
    },
    ...
  ]
}
```

The five fixture issues are designed so the maintainer-association
filter behavior is testable:

| issue | maintainer-association comment? | expected outcome |
|-------|---------------------------------|------------------|
| 9001  | MEMBER                          | kept             |
| 9002  | OWNER                           | kept             |
| 9003  | COLLABORATOR                    | kept             |
| 9004  | only NONE / CONTRIBUTOR         | dropped by T007  |
| 9005  | MEMBER + others                 | kept             |

T007's unit test asserts 9004 is dropped and the other four survive.
