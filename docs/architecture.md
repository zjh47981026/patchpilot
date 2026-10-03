# Architecture decisions

## Small enough to inspect

The application runs with Python's standard library: a loopback HTTP server, SQLite, AST, JSON and urllib. This avoids runtime dependency setup for a portfolio demo. The frontend has no build step and bundles all assets. Python 3.12+ is the only static-review requirement.

## Evidence before presentation

The parser validates hunk lengths/order, old/new line counters, file limits and paths. The reviewer uses the parser's added-line set as the only allowed location set. Full source must match every non-deleted diff line at its exact new line number before static checks run. Entirely new files can be reconstructed only from a /dev/null header and consecutive added lines from 1. Partial modules are never presented as complete AST coverage.

Five explainable static rule families inspect AST syntax. Findings are risks with conditional explanations, not exploitability proofs. Model output follows a narrow findings contract; locations must match added lines. The server independently resolves cited excerpts from the parsed diff, and the UI renders text without interpreting HTML.

## Snapshot consistency

GitHub import reads PR metadata and files, fetches new source with a head SHA ref, then rechecks head and base SHAs. Server memory retains a bounded imported snapshot and issues an opaque ID. A review using that ID must match its exact diff and source. Origin metadata is server-owned. Editing imported input clears the ID and converts it into a manually supplied review. Saved reviews include a SHA-256 diff hash and snapshot provenance when imported.

## Honest modes

Static mode requires no model. Static + local AI mode runs static checks plus optional Ollama generation. Invalid/unavailable AI becomes static_fallback with explicit warnings. No static finding is relabeled AI. AI line-validation establishes only that a line was supplied, not that the model's reasoning is true.

## Persistence and evaluation

History stores findings, cited line excerpts, mode, warnings and provenance; full diffs/source files stay out of SQLite. Feedback has one useful/incorrect vote per finding per review and is stored transactionally. It does not train a model.

The versioned dataset is synthetic development data, with exact rule/path/line identities, clean controls, injection and unsupported-context cases. It is intentionally described as development coverage. AI semantic quality needs independent adjudication and a frozen held-out set; the static dashboard does not claim AI accuracy.

## API

GET /api/config (local request token and model configuration)
GET /api/demo
GET /api/history
GET /api/reviews/{id}
GET /api/reviews/{id}/export
POST /api/import {url}
POST /api/review {title,diff,files?,use_ai,snapshot_id?}
POST /api/evaluate {} (static development benchmark)
POST /api/feedback {review_id,finding_id,vote}
POST /api/delete {review_id}

POST requires Content-Type: application/json and X-PatchPilot-Token from the same-origin config response. Request/response examples can be inspected directly in the browser's network panel.
