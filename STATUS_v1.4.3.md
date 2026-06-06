# AgentWire-Cue v1.4.3 — Final Status

> **Released**: 2026-06-06
> **Tag**: `v1.4.3` (commit `e40beb8`)
> **Owner**: 丝线 (SilkThread)
> **Spec scope**: v1.4.3 spec closes the v1.4 backlog (history / redact / SKILL library)

---

## 🎯 完成清单

主人指示: **"v1.4 修复改进：history 持久化 / CUE history 引用 / SKILL 库 / 双语 README"** → 全完成。

| 项 | 状态 |
|---|---|
| History 持久化（per-peer JSONL） | ✅ Done (CORE 端) |
| History JSON-RPC API (`messages/{list,get,peers,export}`) | ✅ Done (CORE 端) |
| Context 自动注入（metadata） | ✅ Done (CORE 端) |
| Redact 引擎 + `/redact/patterns` 端点 | ✅ Done (CORE 端) |
| CUE 端 `HistoryClient`（30s LRU cache） | ✅ Done |
| CUE 表达式扩展：`peers.*` / `history.*` 命名空间 | ✅ Done |
| CUE 表达式扩展：方法调用语法 `a.b.c(args)` | ✅ Done |
| CUE 新 trigger：`history_change` | ✅ Done |
| CUE 端 `RedactClient`（24h cache + builtin fallback） | ✅ Done |
| Agent 文档：core `skill/` 9 文件 | ✅ Done |
| Agent 文档：cue `skill/` 5 文件 | ✅ Done |
| 双语 README（core + cue） | ✅ Done |
| git tag `v1.4.3` | ✅ Done (annotated) |
| 推送 GitHub | ✅ Done |
| GitHub Release | ✅ <https://github.com/DerekEXS/agentwire-cue/releases/tag/v1.4.3> |

---

## 📊 数字

| 维度 | 数字 |
|------|------|
| **Tag** | `v1.4.3` (commit `e40beb8`) |
| **Commits** | 2 (feat `1ad64b2` + docs `e40beb8`) |
| **Files changed** | 10 (3 new + 5 modified + 2 docs new) |
| **+ 540 / -7 lines** | history_client + history_proxy + redact + expression/host/statechart/trigger_impl 改造 |
| **Tests** | 250/250 全绿 (v1.4.2 baseline) |
| **SKILL docs** | 5 (SKILL.md / SKILL_CN.md / PLUGIN_AUTHORING.md / EXPRESSION_REFERENCE.md / INTEGRATION_*.md) |
| **Dependencies on CORE** | Requires agentwire-core **v1.4.3+** for `messages/*` JSON-RPC and `/redact/patterns` |

---

## 🆕 v1.4.3 新功能详解

### 1. `peers.*` / `history.*` 命名空间（表达式）

cue 表达式白名单从 `{event, context, state, meta, now}` 扩展为 7 个，**新增 `peers` 和 `history`**。

#### dot-path 用法

```yaml
- when: "peers.Pawly.last_round > 0"
  target: processing
- when: "peers.Pawly.history.count() > 100"   # 注意方法调用（见 §2）
- when: "history.total_rounds() > 500"
  target: high_traffic
```

#### method-call 语法（v1.4.3 表达式语法扩展）

支持 `a.b.c(args)` —— 这是对 v1.2 表达式语法的扩展（v1.2 仅支持顶层 `func(args)` 和 `var.path.path`）。

新增 AST 节点 `{"op": "method", "path": [...], "args": [...]}`，求值时先解析路径到对象，再调其方法。

#### 命名空间 API

`peers.<name>` (代理):
- `.uuid` `.name` `.last_round` `.total_rounds` `.last_ts` — dot-path
- `.history.last(n=5)` — 列表
- `.history.last_n_rounds(n=5)` — 别名
- `.history.count()` — int
- `.history.last_round()` — int
- `.history.last_inbound_contains(needle)` — bool
- `.history.last_outbound_contains(needle)` — bool

`history.*` (跨 peer):
- `.total_rounds()` — int
- `.total_rounds_today()` — int (v1.4.3 占位实现：等同 total_rounds)
- `.peer_count()` — int
- `.peer_names()` — list[str]

### 2. `history_change` trigger（v1.4.3 新增 trigger 类型）

cue 原 2 类 trigger（`cron` / `a2a_message_type`）扩展为 **3 类**，新增 `history_change`。

```yaml
triggers:
  - name: pawly_replied
    type: history_change
    peer: "Pawly"        # 或 "*" = 任意 peer
    granularity: round    # round | message | manual
    poll_interval_seconds: 30
```

实现：`HistoryChangeTrigger` 在 cue host 启动时创建 asyncio task，每 N 秒 poll CORE `/messages/peers`，比对前后 `last_round`，差值 > 0 即向 statechart 投 `Event(type='history_change', payload={...})`。

粒度：
- **`round`**（默认）：每对 outbound+inbound 完成 = 1 轮
- **`message`**：每条消息触发（频率高）
- **`manual`**：仅手动 `cue trigger ... manual` 触发

### 3. HistoryClient + RedactClient

- `HistoryClient`：30s LRU cache 包裹 CORE 的 `messages/{list,get,peers,export}` JSON-RPC
- `RedactClient`：启动时拉 CORE `/redact/patterns` 一次 → `~/.cache/agentwire/redact_patterns.json`（24h TTL）；CORE 不可达时用内置 fallback（2 个最小 pattern）

---

## 🛠 修改文件清单

| 文件 | 类型 | 改动 |
|------|------|------|
| `core/expression.py` | M | + `peers` / `history` 白名单 + method AST 节点 + getattr 兜底 |
| `core/host.py` | M | 构造 HistoryClient 注入 statechart + 注入 history_change trigger |
| `core/statechart.py` | M | EvalEnv 增加 history_client 字段；as_dict 注入 peers/history 命名空间 |
| `core/trigger_impl.py` | M | + `HistoryChangeTrigger` 类 |
| `core/history_client.py` | A | 30s cache 的 JSON-RPC 客户端 |
| `core/history_proxy.py` | A | 命名空间代理类（_PeersNamespace / _PeerProxy / _HistoryNamespace） |
| `core/redact.py` | A | RedactClient（24h cache + fallback） |
| `README.md` + `README_CN.md` | M | 双语 + A2A v1.0.1 / MIT 标注 + 引用 CORE |
| `skill/*.md` | A | 5 文件 SKILL 库 |

---

## 🧪 端到端验证 (v1.4.3)

### 表达式新语法（单元 smoke）

```
$ python3 -c "from core.expression import parse, evaluate; ..."
test1: peers.Pawly.last_round == 5            → True
test2: peers.Pawly.history.count() == 5       → True
test3: history.peer_count() == 2              → True
test4: history.total_rounds() == 17           → True
test5: peers.Pawly.history.last_inbound_contains("OK") → True
test6: render_template('{{ peers.Pawly.last_round }}', env) → "5"
test7: event.type == "a2a_message"            → True (向后兼容)
```

### CORE 端 history 持久化（本地 smoke）

```
$ python3 -c "from history import HistoryManager; ..."
peers: [{'uuid': 'e2725436', 'name': 'e2725436', 'last_round': 1, ...},
        {'uuid': 'efabbd95', 'name': 'efabbd95', 'last_round': 1, ...}]
A messages: 1 round, 2 messages
A export md:
# Conversation with e2725436
## Round 1 — 2026-06-06T11:41:15Z
- **Outbound**: my key is [REDACTED:OPENAI_KEY]
- **Inbound**: got it, your key is Bearer [REDACTED:TOKEN]
```

### 路由修复（v1.4.2 audit closure）

v1.4.2 时 `/.well-known/agent.json` / `/health` / `/a2a/jsonrpc` 三个路由因 `_setup_routes()` 未注册而失效；v1.4.3 已注册。

### 与 CORE 联动

cue 启动 → 拉 CORE `/redact/patterns` 一次（24h 缓存）。如 CORE 未运行，使用内置 fallback (2 pattern)。

---

## 🛡 防泄漏

- 所有 commit author 均为 `silk-thread <silk-thread@agentwire.local>`（化名），未暴露真实邮箱
- `RedactClient` 在写入历史前自动脱敏（API key / token / JWT / 私钥 / URL 密码）
- cue 仓为 **private** GitHub 仓；core 仓为 **public** 但不含任何机密（core README 已删除 "Companion Repository" 段对 cue 的显式 URL 引用）

---

## 📁 交付清单

| 文件 | 内容 |
|------|------|
| `core/expression.py` | 表达式语法扩展（method call + 新命名空间） |
| `core/host.py` | history_client 注入 |
| `core/statechart.py` | EvalEnv.history_client |
| `core/trigger_impl.py` | HistoryChangeTrigger |
| `core/history_client.py` | 新建 (75 行) |
| `core/history_proxy.py` | 新建 (110 行) |
| `core/redact.py` | 新建 (90 行) |
| `skill/SKILL.md` + `SKILL_CN.md` | cue 端概览 |
| `skill/PLUGIN_AUTHORING.md` | 详细 YAML 编写指南 |
| `skill/EXPRESSION_REFERENCE.md` | 完整表达式语法 reference |
| `skill/INTEGRATION_OpenClaw_Hermes_QwenPaw_Claude.md` | 平台集成 |
| `README.md` + `README_CN.md` | 双语 |

---

## 🔄 v1.4 整体状态

| 版本 | Tag | 关键交付 |
|------|-----|----------|
| v1.4.1 | ✅ | host + admin + a2a + 5 examples + 6 BUG fix |
| v1.4.2 | ✅ | BOM fix + systemd + reverse proxy + token-env |
| **v1.4.3** | **✅ (本文件)** | **history / redact / CUE 引用 / SKILL 库 / 双语 README** |

v1.4 backlog 已完结。

---

## 🎬 下一 session 起点

读本文件 + `agentwire_cue/ROADMAP_v1.4.md`（已更新 v1.4.3 为完成状态）。

**可选下一阶段**:
- **v1.5**: qwenpaw-skill 归属决策（独立仓 / 通用 client SDK / 删除） + 跨 CUE 依赖 + Dockerfile + K8s + structlog
- **CORE 端**: `total_rounds_today` 真实按日期聚合（v1.4.3 占位）
- **CUE 端**: expression `peers.*` 在 yaml 里编辑器的 schema lint 提示

---

*冻结: 2026-06-06 丝线 (SilkThread)*
*Tag: v1.4.3 (commit e40beb8)*
