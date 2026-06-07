# Changelog

All notable changes to AgentWire-Cue are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## v1.4.x series (2026-06 — FROZEN)

The v1.4 series brings AgentWire-Cue to production readiness on top of A2A v1.0.1:
plugin host + admin API + per-peer history awareness + a small but real expression
language. v1.4 is the **stable baseline** going into v1.5.

**Companion dependency**: this release requires [agentwire-core v1.4.3+](https://github.com/DerekEXS/agentwire-core)
running on the same host (default `http://127.0.0.1:18800`). CORE provides the
history JSON-RPC surface, redaction catalog, and Bearer-token auth.

---

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
