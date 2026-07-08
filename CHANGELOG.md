# Changelog

All notable changes to AgentWire-Cue are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [v2.0.3] - 2026-07-08

### 🛡️ Security — Information Leak Sanitization
- CUE CHANGELOG historical entries (v1.6.4, v1.6.5) stripped of personal
  agent names. All release-tree files now use generic slot names and
  placeholder values only.

## [v2.0.2] - 2026-07-08

### 🐛 Bug Fixes
- `history_client.list_peers()`: replace deprecated `messages/peers` (v1.x) with
  `ListTasks` pagination (v2.0). Derives peer identity from `contextId` prefix.
  Falls back to configured aliases when `ListTasks` is unavailable.
- `HistoryProxy.refresh()`: no longer crashes when `list_peers` returns empty
  due to v2.0 API changes.

## [v2.0.0] - 2026-07-07

### 🚀 Architecture Reposition
- **Repositioned**: from "polling trigger host" to **A2A-Native Workflow Orchestration Engine**
- Replaced private CORE history polling with standard A2A JSON-RPC event reception via CORE v2.0's
  real-time `_forward_to_cue()` push channel
- Plugin triggers now react to events on the A2A wire, not to periodic CORE polls
- Behavior change visible only in deployment: plugins that relied on `history_change` polling now
  fire in real-time when CORE pushes the corresponding A2A event

### ✨ Added
- New endpoint `/.well-known/agent-card.json` (A2A standard discovery path)
- New endpoint `/a2a/jsonrpc` — handles standard `SendMessage`, `GetTask`, `ListTasks` JSON-RPC methods
  dispatched to trigger handlers
- Updated Agent Card to A2A v1.0 format with `supportedInterfaces`, `provider`, `skills`
- Compose: CORE image reference updated from `agentwire-core:v1.5.6` → `agentwire-core:v2.0.0`

### 🔄 Backward Compatibility
- `/a2a/inbound` route retained — still receives messages from CORE v1.x with admin token auth
- `/.well-known/agent.json` route retained — same content as the new `agent-card.json`
- All existing plugins continue to work without modification
- All 359 existing tests pass without changes

## [v1.6.5] - 2026-07-06

### 🐛 Bug Fixes

**P0: script-receiver trigger was registered but never fired**
- `examples/script-receiver/cue.yaml` rewrite: ``a2a_content_match`` → ``history_change``
  with guard expression using ``last_inbound_contains('project:')`` AND
  ``last_inbound_contains('scenes:')``. The original ``a2a_content_match``
  trigger registers a handler on ``A2AListener`` (a passive HTTP server
  listening on 18801) but CORE never pushes inbound messages to CUE, so
  the handler is dead code.
- New ``history_proxy._PeerHistoryProxy.last_inbound_text()`` method returns
  the concatenated text of recent inbound messages (skips outbound CORE
  auto-acks) so ``write_file`` actions can persist the full payload.

**P0: history_change alias resolution was broken since v1.4.3**
- ``core/trigger_impl.py::HistoryChangeTrigger._poll_loop`` matched
  ``self.peer`` (e.g. ``'remote_peer_a'``) against CORE's ``messages/peers``
  ``name`` field, which CORE returns as the first 8 chars of the peer uuid
  (e.g. ``'75755f13'``). The string comparison never matched, so every
  history_change trigger with an alias peer name was silently inactive —
  including ``owner-alert`` (deployed since v1.4.3).
- New private method ``_peer_matches(key)`` resolves ``self.peer`` via
  ``HistoryClient._aliases`` (alias → uuid → CORE name 8-char prefix)
  before comparing. Back-compat: explicit CORE names and ``peer: '*'``
  continue to work.

### 📦 Changed

- `__init__.py`: `__version__ = "1.6.5"`
- `examples/script-receiver/cue.yaml`: trigger type, guard expression,
  peer declaration, `permissions.peers` (added `remote_peer_a`)
- `core/trigger_impl.py`: `_peer_matches()` helper extracted from
  `_poll_loop()` for testability and clarity
- `core/history_proxy.py`: `last_inbound_text()` method added to
  `_PeerHistoryProxy`

### 🧪 Tests

- New `tests/test_v165_script_receiver_fix.py` (11 cases):
  - 4 cases for `_peer_matches` (alias resolves, CORE name back-compat,
    wildcard, negative case)
  - 4 cases for `last_inbound_text` (basic, skips outbound, multi-part
    join, empty history raises `HistoryDiagnosticError`)
  - 3 cases for `script-receiver/cue.yaml` shape (trigger type,
    peer alias declared, guard has both keywords)
- Test suite: **351 passed, 6 skipped, 0 failed** (was 340 passing before)

### 📝 Acknowledgements

Bug discovered by remote peer during first end-to-end script delivery
after v1.5.5 CORE + v1.5.2 CUE went into production.

---

## v1.5.x series (2026-06)

The v1.5 series validates the workflow-pointer handoff path on top of CORE
metadata persistence and CUE peer aliases.

---

## [v1.6.3] - 2026-07-05

### Fixed
- `__main__.py::host --a2a-url` now defaults to `os.environ.get("CUE_CORE_URL", ...)`
  so compose / k8s can wire the URL without rebuilding the image. The hardcoded
  URL was removed from Dockerfile `CMD` (replaced with comment). Symptom:
  WSL2 + `network_mode: host` + k8s-style `agentwire-core` service DNS
  resolves to a non-routable fake Service IP (`198.18.0.20`), causing every
  `history_change` poll to log `Remote end closed connection without response`.
  Verifying: `docker compose up -d agentwire-cue && docker logs ... | grep
  "history_change.*poll failed"` should now be silent (zero failures per
  15-second cycle).
- `docker-compose.yml`: `agentwire-cue` now sets `CUE_CORE_URL=http://127.0.0.1:18800`
  (host-network mode) and mounts a per-peer secrets file for any remote
  CORE that uses an independent A2A token. HistoryClient reads the token
  via the `token_file` alias field; the literal value never lives in compose / cue.yaml.
- `examples/owner-alert/cue.yaml` and `skill/PLUGIN_AUTHORING.md`:
  refreshed remote-peer `uuid` and `url` to match the current peer
  allocation. (Concrete values omitted from this release-tree history;
  tracked on the operator's local overlay only.)

---

## [v1.6.4] - 2026-07-05

### Sanitization (post v1.6.3 token leak)
Personal agent names must not appear in release-tree files alongside
real uuid / IP / host / token values. v1.6.4 strips every concrete
personal value out of the tracked tree; real peer configuration moves
to a `*.local.yaml` overlay.

Files changed:
- `examples/owner-alert/cue.yaml`: every peer `uuid`, `url`, `description`,
  `http_egress` IP, and `workflow_pointer.workflow_file` replaced with
  `<set-me-*>` placeholders. Alias slot names changed to `remote-peer-a`
  / `remote-peer-b`. `production.local.yaml` pattern documented inline.
- `examples/script-receiver/cue.yaml`: workflow metadata anonymized
  (`<set-me-workflow-filename>`); description no longer references
  user-specific agent names.
- `skill/PLUGIN_AUTHORING.md`, `skill/SKILL.md`: remote peer blocks replaced
  with slot-name placeholders (`<your-remote-peer-name>`).
- `README.md`, `README_CN.md`: remote peer names and `pawly_responder`
  example replaced with `<your-...>` placeholders.
- `docker-compose.yml`: secret renamed to generic `peer-a-a2a-token`
  (file path + secret name + env var).
- `secrets/peer-a-a2a-token.txt` (chmod 600).
- `examples/owner-alert/README.md`, `README-DOCKER.md`: prose references
  generalized.

Tests (`tests/test_*.py`) reference demo fixtures only and were left untouched.

### Production overlay convention
Replace `<set-me-*>` placeholders via:

```bash
cp examples/owner-alert/cue.yaml examples/owner-alert/production.local.yaml
$EDITOR examples/owner-alert/production.local.yaml  # fill in real values
```

`.gitignore` already covers `examples/**/*.local.yaml`. README-DOCKER.md
documents the overlay pattern + compose mount for production cue.yaml.

### Maintenance (post-release)

- `core/a2a_client.py`: `CUE_VERSION` constant now derives from
  `agentwire_cue.__version__` instead of a hardcoded literal. Symptom
  caught by `test_cue_version_matches_package_version` regression
  guard: after v1.6.3→v1.6.4, `agentwire_cue.__version__` was bumped
  to `"1.6.4"` but `CUE_VERSION` was still `"1.6.2"`, so
  `/.well-known/agent.json` and `/admin/status` reported a stale
  version. Both surfaces now match the package version automatically.
- `tests/test_v150_regressions.py::test_agent_card_reports_current_version`
  also made version-dynamic (was hardcoded `"1.6.2"`).

---

## [v1.6.2] - 2026-06-15

### Fixed
- `core/trigger_impl.py::ContentMatchTrigger` now sets `event.matched_keywords`
  to a list of actually-matched keyword strings (was: an int count) and adds
  the new `event.contains_count` field (int, total keywords searched). This
  fixes `{{event.contains_count}}` template rendering in `script-receiver/cue.yaml`.

### Documentation
- `docker-compose.yml`: `network_mode: host` now has comments explaining the
  WSL2/Tailscale requirement and the security trade-offs (lost network
  isolation, recommend tailscale0-only firewall on WSL).
- `SKILL.md` + `SKILL_CN.md`: added `a2a_content_match` row to the Triggers
  table and a dedicated event-payload subsection listing all `event.*` fields
  with their types.
- `PLUGIN_AUTHORING.md`: new `spec.peers` section covering the peer alias
  table plus the v1.6.1 per-peer A2A token fields (`token`, `token_env`,
  `token_file`) with priority, examples, and security recommendations.
- `examples/a2a-with-fallback.yaml` + `examples/file-watcher.yaml`: added
  inline comments on `match: "*"` explaining why each plugin intentionally
  matches all inbound A2A messages.

### Tests
- Full CUE suite: target 335 passed, 6 skipped.
- Added v1.6.2 coverage for `matched_keywords` list semantics and the new
  `contains_count` field.

## [v1.6.1] - 2026-06-14

### Added
- **Per-peer A2A token**: `spec.peers.<alias>` now supports optional `token`,
  `token_env`, and `token_file` fields for authenticating to remote peer COREs.
  Priority: `token_file` > `token_env` > `token` (literal) > default local CORE
  token. Used for history access (`HistoryClient`) and `send_a2a` requests
  (`A2AClient.send_message`). Backward compatible: peers without token config
  use the local CORE token unchanged.
- **`a2a_content_match` trigger**: new trigger type that matches inbound A2A
  messages by text content. Config: `contains` (list of keyword strings),
  `min_match` (minimum keyword count, default 1), optional `peer` filter.
  Event payload exposes `event.peer`, `event.peer_uuid`, `event.text`,
  `event.parts`, `event.metadata`.
- **`script-receiver` example plugin**: receives video scripts from a remote
  peer via A2A, writes them to disk (`write_file`), and notifies the main
  agent via `send_a2a` with workflow-pointer metadata.
- **`write_file` path template rendering**: `with.path` now renders `{{...}}`
  template variables (previously only `with.content` was rendered).
- **`owner-alert` example**: remote peer now includes `token_env: "PEER_A_A2A_TOKEN"`
  demonstrating per-peer token usage.

### Changed
- `plugin.schema.json`: `spec.peers` properties now accept `token`, `token_env`,
  `token_file` (optional). New `a2a_content_match` trigger type with validation.
- `CORE` image tag in compose bumped to `agentwire-core:v1.5.5`.

### Tests
- Added v1.6.1 coverage for per-peer token resolution, header propagation in
  `send_message`, `HistoryClient._rpc` token passthrough, and `write_file` path
  template rendering.
- Full CUE suite: 333 passed, 6 skipped, 1 flaky (P50 timing).

## [v1.6.0] - 2026-06-13

### Production Ready Milestone

This release marks **AgentWire-Cue production readiness**. All v1.5.x functionality,
security hardening, and documentation work has converged; the version jumps to
`v1.6.0` to signal this milestone. Future work moves to maintenance mode.

### Added
- `README.md` + `README_CN.md` fully rewritten: status badge `v1.6.0`, Docker
  Compose quick-start, complete feature list (all v1.5.x items), updated
  deployment section with security defaults, migrated examples to v1.4.8+
  `peers` config block, updated repository structure.

### Changed
- Version strings unified to `1.6.0` across `__init__.py`, `a2a_client.py`,
  `test_v150_regressions.py`, `docker-compose.yml` (image tag).
- CORE image tag in compose bumped to `agentwire-core:v1.5.5`.
- SKILL docs version headers synced to CORE v1.5.5 / CUE v1.6.0.

### Fixed
- All stale `v1.4.3` / `v1.4.4` / "future v1.5+" references purged from
  README, README_CN, and README-DOCKER.

### Tests
- Full CUE suite: 334 passed, 6 skipped.

## [v1.5.9] - 2026-06-13

### Fixed
- `examples/owner-alert/cue.yaml`: `set_context` now uses the correct direct key-value structure (`last_notified_round: "{{event.new_round}}"`) instead of the broken `key:`/`value:` wrapper that silently set `context["key"] = "last_notified_round"` (literal). The round-dedup guard `event.new_round > context.last_notified_round` is now functional.
- `spec.resilience.on_exhaust` is now validated at loader time against known state names, catching spelling mistakes before runtime.

### Changed
- Examples `echo-with-persist.yaml`, `a2a-with-fallback.yaml`, and `file-watcher.yaml` now document their `match: "*"` intent with inline comments.

### Tests
- Added v1.5.9 coverage for owner-alert set_context structure, on_exhaust validation (missing and valid), plus full-regression suite.

## [v1.5.8] - 2026-06-13

### Changed
- All 5 SKILL files updated to reflect v1.5.7 feature state:
  `agentwire/v1.2` schema examples, Docker Compose startup, inbound admin-token
  auth, security defaults, `/admin/peers` redaction, `permissions.peers`
  enforcement, and `doctor` CORE downgrade.

## [v1.5.7] - 2026-06-13

### Security
- `agentwire-cue doctor` now downgrades `CORE` reachability FAIL → INFO when the URL targets the in-container `agentwire-core` DNS and resolution fails on the host shell, or when a loopback CORE is not listening. The probe is meaningless in either case and was a noisy false positive.
- `examples/echo.yaml` (v0.3.0) removed; the workspace keeps only the newer `echo-with-persist.yaml` (v0.4.0) to avoid duplicate-plugin-name load warnings.

### Changed
- `doctor` CLI now reads `--a2a-url` default from `CUE_DOCTOR_A2A_URL`, which the Docker image sets to `http://agentwire-core:18800` for in-container healthchecks.
- CUE image tag and agent card version bump to `v1.5.7`.

### Tests
- Added v1.5.7 coverage for `doctor` CORE downgrade and env-driven A2A URL.
- Full CUE suite: 326+4 passed, 7 skipped.

## [v1.5.6] - 2026-06-13

### Security
- `examples/owner-alert/cue.yaml.local-backup` is moved out of the working tree into the local archive directory; tracked tree only contains demo defaults.
- `/admin/peers` now redacts `uuid` to first 8 hex chars + `...` and `url` to scheme + host + port; query/path are dropped.
- `/admin/peers` reachability probes are cached for 30s per URL to avoid repeat scans.
- A2A listener bound to a non-loopback interface now rejects inbound requests outright (HTTP 403) when no auth token is configured; loopback listeners still warn and accept (developer ergonomics).
- `README-DOCKER.md` drops host-specific absolute paths in command examples.

### Changed
- CUE image tag and agent card version bump to `v1.5.6`.

### Tests
- Added v1.5.6 coverage for `/admin/peers` redaction, peer reachability cache, and the no-token bind policy.

## [v1.5.5] - 2026-06-13

### Security
- A2A inbound listener now defaults to `127.0.0.1`; LAN binding requires explicit `--a2a-listener-host 0.0.0.0` and logs a warning.
- `/a2a/inbound` now enforces Bearer auth when a CUE admin token is configured. **Breaking change for inbound callers**: they must now pass the CUE admin token (previously the A2A token was acceptable). Update routing config / call sites accordingly.
- Admin API now defaults to `127.0.0.1`; remote binding requires explicit `--admin-host 0.0.0.0` and logs a warning.
- `send_a2a` now enforces `permissions.peers` allow-lists when present; empty peer allow-lists keep legacy permissive behavior.
- Docker Compose publishes CORE/CUE ports on host loopback only by default.

### Changed
- CUE image tag and agent card version bump to `v1.5.5`.

### Tests
- Added v1.5.5 coverage for listener/admin bind defaults, inbound Bearer auth, and send_a2a peer permission enforcement.

## [v1.5.4] - 2026-06-13

### Fixed
- `agentwire-cue doctor` now treats ports already owned by an `agentwire_cue` process as healthy, and downgrades unidentifiable busy ports to INFO when the container cannot expose owner details.
- `examples/echo-with-persist.yaml` now uses supported `apiVersion: agentwire/v1.2`, so the example loads without schema warnings.
- The release-tracked Compose file is the CUE-root `docker-compose.yml`; the workspace-level compose file is retired to a pointer to avoid drift.
- `README-DOCKER.md` now documents the official compose path, production owner-alert configuration via ignored local overrides, migration from old systemd/nohup deployments, history volume migration, token migration, and post-migration verification.

### Changed
- CUE image tag and agent card version bump to `v1.5.4`.

### Tests
- Full CUE suite: 316 passed, 7 skipped.
- Docker build: `agentwire-cue:v1.5.4` at 138MB.
- Compose smoke: CORE and CUE both healthy; in-container doctor reports listener ports as INFO, not WARN.

## [v1.5.3] - 2026-06-12

### Added
- Dockerfile for containerized CUE deployment on `python:3.13-slim`, running as non-root `agentwire` and exposing ports 18801 + 19000.
- Runtime `requirements.txt` now includes `structlog`; Docker images use the structlog-backed observability path while local environments retain the stdlib fallback.
- Top-level AgentWire Docker deployment files in the A2A workspace: compose, `.env.example`, secrets placeholder, and Chinese Docker guide.

### Fixed
- Scheduler-fired cron, A2A, and history-change triggers now update plugin `last_trigger_at`, `last_match`, `last_reason`, and `last_details`, so `/admin/status` reflects automatic triggers as well as admin-fired triggers.
- Scheduler/admin-triggered transitions invalidate the shared history cache before guard evaluation, so freshly imported CORE history is visible to immediate owner-alert fires.
- `examples/owner-alert/cue.yaml` is restored to demo placeholder peer UUIDs and uses the Docker Compose `agentwire-core` service URL for containerized E2E testing.

### Tests
- Added scheduler tracking regression coverage for automatic trigger bookkeeping, history-cache invalidation, and trace event emission.
- Full CUE suite: 315 passed, 6 skipped.

## [v1.5.2] - 2026-06-12

### Added
- `spec.requires` block on plugin manifests with optional `plugins`, `peers`, and `capabilities` lists.
- `Host._check_requires`: at startup, after all plugins load, marks any plugin with an unmet dependency as `degraded` (with a comma-joined human-readable `degraded_reason`). Degraded plugins remain loaded but their triggers are NOT registered, so the rest of the host still boots and `/admin/status` keeps surfacing them.
- Schema (`schema/plugin.schema.json`) now accepts `spec.requires`.
- `agentwire-cue doctor` CLI subcommand. Surface today's silent-failure modes: token-file BOM/CRLF, CORE reachability, peer reachability, port conflicts (18801 / 19000), proxy env vars, and plugin dependency completeness. `--no-network` skips the HTTP probes for offline use.
- Package `__version__` corrected from stale `1.3.0` to `1.5.2` so `agentwire-cue doctor` reports the right version header.

### Tests
- Added v1.5.2 coverage for `requires` extraction, `_check_requires` (plugin / peer / capability misses + satisfied case), each doctor check function, and the CLI integration of the doctor command.
- Full CUE suite: 311 passed, 6 skipped.

## [v1.5.1] - 2026-06-12

### Fixed
- **P0**: `{{event.peer}}` (and any other `event.*` field) rendered to an empty string when an admin trigger payload omitted `peer`. `handle_trigger` now injects the configured peer alias from the matching `history_change` trigger before constructing the `Event`, so `text` / `metadata` templates see the real peer name.

### Added
- `core/observability.py`: stdlib-only structured event logging — `new_trace_id()` / `set_trace_id()` / `emit(event, **fields)` writing one JSON line per event. The trace id flows via `contextvars` so concurrent tasks stay isolated. structlog is deferred to a future release; this module keeps the public API compatible.
- Admin trigger calls now bracket the transition with a trace id and emit `cue.trigger.received` + `cue.trigger.evaluated`; the trace id is also returned in the response body so a caller can grep one tag end-to-end.
- Statechart now emits `cue.guard.evaluated`, `cue.action.executed` (for `log` / `set_context` / `reply_a2a` / `send_a2a`), and `cue.error` for guard parse/eval failures.
- `host._wrap_send` emits `cue.send_a2a.completed` with target peer, metadata keys, and the underlying `SendResult` value.
- New `/admin/status`, `/admin/peers`, `/admin/plugins` endpoints under the existing Bearer-token gate. `/admin/status` exposes per-plugin runtime state plus `last_trigger_at` / `last_match` / `last_reason` / `last_details`; `/admin/peers` reports each alias's `uuid`, `url`, and a best-effort reachable probe.
- Agent Card version bumped to `1.5.1`.

### Tests
- Added v1.5.1 coverage for the trace_id contract, admin-trigger emit wiring, statechart emit wiring, send_a2a emit wiring, and the three admin diagnostics endpoints (including auth gate).
- Full CUE suite: 294 passed, 6 skipped.

## [v1.5.0] - 2026-06-12

### Added
- A2A send payloads now normalize CUE `{text: ...}` shorthand into A2A-compatible `role` + `parts`, preventing empty-message history entries.
- Agent Card version now reports `1.5.0` instead of stale `1.4.0`.
- `examples/owner-alert/cue.yaml`: upgraded to v1.5.0 and includes a `main` peer alias plus workflow-pointer metadata.

### Tests
- Added v1.5.0 regression coverage for send payload normalization and Agent Card version.
- Full CUE suite: 274 passed, 6 skipped.
- Manual workflow-pointer E2E: seeded urgent history → owner-alert matched true → `send_a2a` stored non-empty parts and `metadata.workflow_pointer` in CORE history.

## v1.4.x series (2026-06 — FROZEN)

The v1.4 series brings AgentWire-Cue to production readiness on top of A2A v1.0.1:
plugin host + admin API + per-peer history awareness + a small but real expression
language. v1.4 is the **stable baseline** going into v1.5.

**Companion dependency**: this release requires [agentwire-core v1.4.3+](https://github.com/DerekEXS/agentwire-core)
running on the same host (default `http://127.0.0.1:18800`). CORE provides the
history JSON-RPC surface, redaction catalog, and Bearer-token auth.

---

## [v1.4.8] - 2026-06-12

### Added
- `spec.peers`: optional peer alias table with explicit `uuid` + `url`, used for stable history lookup and direct A2A routing.
- `HistoryClient`: resolves configured peer aliases to CORE peer UUIDs before `messages/list` / `messages/get` calls while preserving legacy behavior when no aliases are configured.
- `A2AClient.send_message`: accepts optional `metadata` and forwards it as `message.metadata`.
- `send_a2a` statechart action: supports optional `metadata` blocks and renders template strings inside metadata.
- `examples/owner-alert/cue.yaml`: upgraded to v1.4.8 with `peers` aliases and `workflow_pointer` metadata.

### Tests
- Added regression coverage for peer alias loading, alias-to-uuid history lookup, direct alias URL routing, send_a2a metadata, and metadata template rendering.
- Full CUE suite: 270 passed, 6 skipped.

## [v1.4.7] - 2026-06-12

### Added
- Admin trigger responses now include `reason` and `details` when `matched: false`.
- Statechart guard diagnostics now distinguish `guard_false`, `guard_eval_error`, `peer_not_found`, and `history_empty`.
- CUE logs now include matched-false diagnostic reason/details for admin-triggered evaluations.

### Tests
- Added regression coverage for statechart diagnostics and admin trigger JSON responses.

## [v1.4.6] - 2026-06-07

### Security
- `git filter-repo` re-writes history (HEAD: `3690ebf`) replacing `<REDACTED_AGENTWIRE_TOKEN>` (主人的 AGENTWIRE_TOKEN 真值) with `demo-token-REDACTED-v1.4.6` in all commits. Token leak in `STATUS_v1.4.2.md` (originally written as BUG-1 documentation showing the actual variable value) is **permanently removed from history**.
- `git filter-repo` replaces `<REDACTED_USER_ALIAS>` (PII) with `用户` in `STATUS_v1.4.1.md` (originally written as a reviewer/team name in the "7 类红线 0 命中" annotation). Note: this PII was a 私有仓 internal reviewer code-name, not a 真凭据, but is replaced for hygiene in case cue 仓 ever goes public.

### Documentation
- `README.md` + `README_CN.md`: new **Deployment** section explicitly states HTTP-not-HTTPS limitation, lists loopback/LAN/VPN as suitable, public-internet as NOT suitable without TLS-terminating reverse proxy. Documents that **18801 A2A listener binds `0.0.0.0` by default** (architectural choice for LAN peer communication, unlike CORE 18800 which is loopback-default).

### Notes
- v1.4.6 is a **token leak closure + PII cleanup** release — all done via `git filter-repo` (历史重写), not a forward-commit
- 14 commits re-written; all tag hashes updated (v1.4.2-v1.4.5)
- `agentwire-core` 仓 v1.4.6 (separate release): `hmac.compare_digest` + 503 错误响应脱敏 + README TLS 警告 (3 项真问题修复)
- cue 仓 v1.4.6: 0 行 code 改动 (纯历史清理 + 文档)
- 243 cue 单测全过 (历史未变)
- **强烈建议主人**: token 虽已在 cue 仓 history 清干净, 仍**轮换 `~/.openclaw/a2a-token.txt`**——任何已 clone cue 仓 v1.4.5 或之前的协作者本地仍可能有 token 备份

## [v1.4.5] - 2026-06-07

### Changed
- `STATUS_v1.4.4.md`: 真 SHA 校准 (tag `v1.4.4` commit `ddb72d4`) + prior v1.4.3 SHA `2c1e083` 引用; 修订 A2A 测试措辞 (同 core 仓)
- `examples/owner-alert/README.md`: 顶部加粗体 "v1.4.4 scope" 提示块; "What's OUT of scope" 段加 deferral 路径 (v1.4.4 只验 cue 单测, 端到端 TG 归全栈升级)

### Notes
- v1.4.5 is a **cleanup / spec-debt closure** release
- v1.4.5 acceptance: 243 cue 单测全过 + leak scan 0 命中
- v1.4.5 commit history: 2 commits (`75a830d` + `8ce3e03`)
- v1.4.5 没新增 SPEC-PATCH — 因 v1.4.4 SPEC-PATCH 已含 v1.4.5 精神

## [v1.4.4] - 2026-06-07

### Added
- `examples/owner-alert/cue.yaml` + `README.md`: killer example demonstrating `history_change` trigger + `peers.X.history.last_inbound_contains()` in a real scenario (remote peer sends urgent: keyword → notify main agent)
- `tests/test_owner_alert.py`: 7 unit tests covering yaml load, schema validate, 2 history_change triggers, state transitions, multi-peer parallel
- `schema/plugin.schema.json`: v1.4.3 v1.4.4 schema bump — adds `history_change` trigger type with config schema (peer/granularity/poll_interval_seconds) (this was a real v1.4.3 sync debt — runtime code shipped but schema missed)
- `core/expression.py`: tokenize + parser now unicode-aware (supports e.g. `peers.remote_peer_a.history.count()`)
- `core/statechart.py`: `send_a2a` action now accepts both v1.2 dict form (`{type, text}`) and bare string
- `CHANGELOG.md` (this file)
- `STATUS_v1.4.4.md`: complete delivery checklist
- `designs/v1.4.3/SPEC-PATCH.md`: retrospective SPEC for v1.4.3
- `designs/v1.4.4/SPEC-PATCH.md`: this release's spec

### Notes
- v1.4.4 is a **micro-improvement** release — no new endpoints, no new trigger types (history_change shipped in v1.4.3, schema bumped here), no new expression namespaces
- v1.4.4 acceptance: cue unit tests only (no end-to-end TG notification — that's OpenClaw main agent's A2A→TG routing, out of v1.4.4 scope)

## [v1.4.3] - 2026-06-06

### Added
- `core/history_client.py`: HistoryClient with 30s LRU cache (consumes CORE's `messages/*` JSON-RPC)
- `core/history_proxy.py`: `_PeersNamespace` / `_PeerProxy` / `_HistoryNamespace` for natural expression access
- `core/redact.py`: RedactClient — pulls `/redact/patterns` from CORE, 24h local cache, builtin fallback
- Expression grammar extension: method-call syntax `a.b.c(args)` (was `func(args)` or `var.path` only)
- New expression namespaces: `peers.*` and `history.*`
- New trigger type: `history_change` (polls CORE `/messages/peers`, fires on round completion)
- `skill/` directory with 5 documents (SKILL+CN, PLUGIN_AUTHORING, EXPRESSION_REFERENCE, INTEGRATION_*)
- Bilingual `README.md` / `README_CN.md`
- `STATUS_v1.4.3.md`

## [v1.4.2] - 2026-06-05

### Added
- 4 fixes: BOM (utf-8-sig) + systemd + reverse proxy + `--a2a-token-env`
- 15 regression tests; total 250/250 green

## [v1.4.1] - 2026-06-04

### Added
- Plugin host (Python aiohttp) with 10-step startup
- 5 example plugins
- Admin API: 3 endpoints on port 19000
- Real A2A HTTP client with retry/backoff/fallback
- Trigger scheduler (cron + a2a_message_type)
- 6 BUG fixes; 235/235 tests green

## [v1.3.1.1] - 2026-05-15

### Fixed
- Sandbox tightening, trigger await pattern, peer card cache

## [v1.3.1] - 2026-05-10

### Security
- P0-1: persist.path sandbox (4-layer defense)
- P0-2: target validation (loader + runtime)

## [v1.3] - 2026-05-01

### Added
- Initial public release: 154 tests, 1522 lines of core code

---

## v1.5 backlog (NOT in v1.4 series)

Items deferred from v1.4 → v1.5:
- Dockerfile + docker-compose
- structlog observability
- Cross-cue plugin dependencies
- `/messages/import` endpoint (would be implemented in CORE, not CUE)
