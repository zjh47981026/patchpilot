# Security model

PatchPilot is a single-user local development tool. Its server binds to 127.0.0.1 and rejects unexpected Host/Origin headers. Mutation endpoints require JSON and a random per-process token. Only explicit bundled UI assets are served; imported paths are metadata, never filesystem read/write targets.

Imported code is untrusted. It is parsed with Python AST and sent only to a fixed loopback Ollama endpoint when the user requests AI. It is never imported, executed, built, tested, cloned, or granted tools. Model messages and source comments cannot request actions. Schema and added-line references are validated, but valid references do not prove a finding is correct. Prompt injection remains an AI output-quality risk.

All source/model text is rendered with DOM textContent. The UI does not interpret model HTML, Markdown, images or links. A Content Security Policy blocks external assets and framing. GitHub URLs use a strict public PR URL format; the importer calls only api.github.com, disables proxies/redirects and uses pinned refs. No GitHub credentials or mutation requests are involved.

Resource budgets: 250 KB diff, 30 files, 500 KB per complete source, 1 MB total source, 1.5 MB JSON body, two concurrent tasks, 128 KB AI response, 45-second AI socket timeout, 45-second GitHub import budget with at most 8 seconds per request. Socket timeout is not a hard CPU/memory cancellation guarantee. Parsing hostile deeply nested Python can still strain this process; loopback and modest size limits reduce exposure, not eliminate it. Model servers may continue generation after a client timeout.

Review results include titles, explanations and cited source lines and therefore may contain proprietary content. SQLite is local and unencrypted. Full input diffs and complete sources are not saved to history. Imported snapshots remain in memory for up to 15 minutes (at most 10) or until process exit. Deleting a history item removes its results/feedback from the active database using secure_delete; it cannot erase OS backups, screenshots, downloaded exports or third-party model caches. Routine server access logging is disabled.

Static/offline review does not contact GitHub. Public PR import contacts GitHub; optional AI contacts the loopback Ollama service, whose own model configuration controls any cloud usage. Use a local model with OLLAMA_NO_CLOUD=1 for offline inference. Other processes/users with access to this machine can read local data; this app provides no user accounts or tenant isolation.

Do not expose the stdlib HTTP server to the internet or a shared untrusted network. For a security concern, open a minimal issue without credentials or private code. No warranty or security certification is implied.
