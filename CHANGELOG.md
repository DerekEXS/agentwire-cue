# Changelog

All notable changes to AgentWire-Cue are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## v1.5.x series (2026-06)

The v1.5 series validates the workflow-pointer handoff path on top of CORE
metadata persistence and CUE peer aliases.

---

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
- `examples/owner-alert/README.md`: 顶部加粗体 "v1.4.4 scope" 提示块; "What's OUT of scope" 段加 deferral 路径 (v1.4.4 只验 cue 单测, 端到端 TG 归初梦全栈升级)

### Notes
- v1.4.5 is a **cleanup / spec-debt closure** release — 初梦 P1/P2 行动清单
- v1.4.5 acceptance: 243 cue 单测全过 + leak scan 0 命中
- v1.4.5 commit history (按初梦 P1 建议拆分): 2 commits (`75a830d` + `8ce3e03`)
- v1.4.5 没新增 SPEC-PATCH — 因 v1.4.4 SPEC-PATCH 已含 v1.4.5 精神 (初梦 P2 措辞改, P3 对称补)

## [v1.4.4] - 2026-06-07

### Added
- `examples/owner-alert/cue.yaml` + `README.md`: killer example demonstrating `history_change` trigger + `peers.X.history.last_inbound_contains()` in a real scenario (Pawly/初梦 含 urgent: 关键词 → notify 主 agent)
- `tests/test_owner_alert.py`: 7 unit tests covering yaml load, schema validate, 2 history_change triggers, state transitions, multi-peer parallel
- `schema/plugin.schema.json`: v1.4.3 v1.4.4 schema bump — adds `history_change` trigger type with config schema (peer/granularity/poll_interval_seconds) (this was a real v1.4.3 sync debt — runtime code shipped but schema missed)
- `core/expression.py`: tokenize + parser now unicode-aware (supports e.g. `peers.初梦.history.count()`)
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
