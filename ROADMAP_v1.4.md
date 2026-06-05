# AgentWire-Cue v1.4 — 迭代方案

> **拟定日期**: 2026-06-04
> **拟定人**: 丝线 (SilkThread)
> **基础**: v1.3 spec 的 v1.4 backlog 12 项 + v1.3 实现期发现的实操问题 + v1.3 roadmap 11 项未完成

---

## v1.3 落地总结

✅ **v1.3 冻结** (commit 470877a): 154 tests / 1522 行 core 代码 / 端到端 smoke 跑通
✅ **v1.3.1 patch 冻结** (commit pending): +33 test / + sandbox.py / + target validation / 195 tests 全绿

v1.3.1 patch 修了 2 个 P0 bug:
- **P0-1**: persist.path 沙箱（4 层防御 + dotdot/symlink escape 防住）
- **P0-2**: target 校验（loader 启动期 + statechart runtime）

实现期发现的关键 issue（v1.3 → v1.3.1 全部解决）：
- ✅ persist.path 沙箱 → v1.3.1 §3.4.2
- ✅ target 校验 → v1.3.1 §4.7.1

未解决（v1.4 必修）：
- 缺真实 A2A HTTP client — 当前用注入式 stub，端到端 A2A 消息流测不了
- 缺 trigger scheduler — 数据结构在，但 cron / A2A 监听调度没实装
- 缺 host 进程 CLI — 只有 `cue validate`，没有 `cue host` 真的跑服务
- 缺 admin API — 3 endpoint 没实装
- 缺 fallback dispatch 验证 — 需要真 A2A 错误注入
- 缺 timer (after/deadline) — statechart §4.4 跳过了
- 3+ example 缺 — 只有 2 个从 v1.2 继承的，新 action 类型无 example

---

## v1.4 三大目标

1. **A2A 消息真正打通** (P0)：host 进程能收能发 A2A message，含 retry+backoff+fallback
2. **运维就绪** (P1)：admin API + Dockerfile + 端到端测试
3. **生态铺垫** (P2)：killer examples + 可观测性 + 升级协议

---

## 🔴 P0 — 不做不敢上生产 (4 项)

> ~~P0-1: 修 persist.path 沙箱~~ → **v1.3.1 已修**（§3.4.2, 4 层防御, 9 个 blocked parent, 33 test case）
> ~~P0-2: target 校验~~ → **v1.3.1 已修**（§4.7.1, loader 启动期 + statechart runtime）
>
> **v1.4.1 已落地 + 冻结** (commit fd3cf12, tag v1.4.1): 6 BUG fix + 9 regression test + 5 example + host + admin + a2a + scheduler
> 235/235 test 全过, 端到端手测 OK

### 1. A2A HTTP client + retry/backoff/fallback
**为什么 P0**: v1.3 的 reply_a2a / send_a2a 都是注入式 stub。**没有 A2A 客户端，host 等于孤岛**。当前 4-stage rollout stage 1 跑不起来。

**实现范围**:
- `core/a2a_client.py`: aiohttp-based, 注入 a2a_url + bearer token
- retry 指数退避 (spec §7 resilience: a2a_retries / backoff_ms)
- 失败时 synthetic `A2A_EXHAUSTED` event 注入 statechart（spec §6.1.1）
- fallback chain 调度（spec §6.1.2：非递归 + from_fallback flag）
- peer discovery via `/.well-known/agent.json`

**测试**:
- mock A2A server fixture（success / timeout / 5xx）
- 重试 1 次后成功 / 重试耗尽后发 A2A_EXHAUSTED
- fallback 链中调 send_a2a 不递归
- bearer token 注入正确

**预估**: ~300 行 + 100 行 test

### 2. Trigger scheduler (cron + A2A listener)
**为什么 P0**: v1.3 启动了 plugin 但没启动 trigger 调度，**等于没启动**。

**实现范围**:
- `core/scheduler.py`: 注册 cron expression + a2a_message_type 两种 trigger
- cron 调度走 `croniter` + asyncio 后台 task（spec §6）
- A2A listener 走 aiohttp web app（接 AGENTWIRE 转发的 message）
- S6.1 fix: `register` race - `await setup` 或 emit ready event

**测试**:
- cron tick 触发 transition（用 mock clock）
- A2A message 触发 transition
- SIGHUP hot reload 暂不做（v1.3 决定）
- 路由冲突：18801 listener 第一个胜出 + WARN

**预估**: ~250 行 + 80 行 test

### 3. `cue host` CLI + 真实启动
**为什么 P0**: 现在 `cue validate` 是死的。要真把 host 跑起来。

**实现范围**:
- `python -m agentwire_cue host --plugin-dir <dir> [--a2a-url] [--port] [--ignore-corrupt-state]`
- 10 步启动顺序（spec §2.1）
- 优雅关闭（SIGTERM → drain → fsync → exit 0）
- 0 plugin 成功加载 → exit 1

**测试**:
- 启动 → SIGTERM → 0 退出码
- 启动失败（schema 错）有清晰 log
- 0 plugin → exit 1
- 端到端: cron trigger 跑完一个 transition

**预估**: ~200 行 + 60 行 test

### 4. End-to-end integration test (with mock A2A server)
**为什么 P0**: 当前 154 测试全是单测。**没有端到端，4-stage rollout stage 1 跑不起来**。

**实现范围**:
- mock A2A server (aiohttp) - 接 a2a_client 测试
- 跑 echo-with-persist.yaml: 真实 cron → 真实 A2A message in → statechart 转移 → 真实 persist → 真实 A2A message out
- 跑 resilience-demo.yaml: A2A 失败 → 重试 → fallback

**测试目标**: 端到端覆盖率 5% → 10%（v1.3 L7）
**预估**: ~150 行 + 200 行 test infra

### 5. 3 个新 example cue.yaml
**为什么 P0**: v1.3 P0 #1 (CLI 工具链) 完成后用户没 example 看不懂。新 action types 同样需要 example。

**实现范围**:
- `examples/cron-driven.yaml`: 定时器触发 + log + set_context
- `examples/a2a-driven-with-fallback.yaml`: A2A 失败 → fallback chain
- `examples/file-watcher.yaml`: write_file 落地（带 filesystem permission 演示）
- 每个 example 自带 README 一句话用途

**预估**: ~120 行

---

## 🟡 P1 — 生产环境必需 (6 项)

### 6. Admin API 3 endpoint on :19000
- `GET /status` — host 状态 + uptime + loaded plugin 数
- `GET /plugins` — 每个 plugin 的 name / state / current_state / context (sensitive filter)
- `POST /trigger` — 手动发 trigger 到指定 plugin（debug 用）
- bearer token 鉴权 (v1.3 强建议绑 127.0.0.1)
- §10.5 context filter（用 enforcer.filter_sensitive 共享 helper）
- §10.6 trigger type 白名单

**预估**: ~200 行 + 60 行 test

### 7. After / deadline timer (statechart §4.4)
- `asyncio.create_task(sleep_and_fire)` 每个 state 切换时 cancel 上一个
- 触发时走完整 6 步 transfer
- **预估**: ~100 行 + 40 行 test

### 8. Dockerfile + docker-compose
- spec §12.4: AGENTWIRE 容器内通信，不映射 18800 给宿主
- spec §12.8: 升级流程措辞写明
- 多阶段 build / non-root user / healthcheck
- **预估**: Dockerfile 30 行 + compose 50 行

### 9. v1.3 backlog 4 项小修
- **L1**: `peer.allow_messages` default 从 `['*']` 改 `[]`（更严）
- **L3**: `PermissionEnforcer.register/unregister` 加 asyncio.Lock
- **L4**: `timers.max_concurrent` 在 schema 加字段
- **L5**: `PermissionError_` 改 `AgentPermissionError`（名字更清晰）

**预估**: ~30 行 + 20 行 test

### 10. 可观测性 (v1.3 roadmap P0 #2)
- structlog JSON output（v1.3 R1 拍板用 structlog，但当前用 stdlib）
- 每次 state transfer emit 一条 trace event
- 注入到 state.json meta.trace（可选）
- **预估**: ~80 行

### 11. CLI `cue lint` 增强
- v1.3 实现：已捕获 `on:` key 无引号
- v1.4 加：YAML 1.1 数值 / 浮点歧义（`12:34:56` 可能是时间或 sexagesimal number）
- 命名 pattern 检查（plugin name 必 lowercase）

---

## 🟢 P2 — 生态长期 (5 项)

### 12. 文档自动生成 (v1.3 roadmap P2 #8)
- `cue docs cue.yaml --format markdown` 从 schema 自动生成
- 消除 README 和 schema.json 的手写重复
- **预估**: 200 行

### 13. CUE 升级协议 (v1.3 roadmap P2 #10)
- 旧 instance drain → 新 instance take over
- v0.1.0 → v0.2.0 升级时 in-flight state 不丢
- API: `cue upgrade <id> --to-version 0.2.0`

### 14. 跨 CUE 依赖 (v1.3 roadmap P1 #7)
- `spec.depends_on: [{cue: research-assistant, state: ready}]`
- AGENTWIRE Core 调度时检查依赖

### 15. Human-in-the-loop (v1.3 roadmap P1 #5)
- 新状态类型 `await_human`
- 触发 Telegram 推送 + Inline 按钮（依赖 Telegram 集成）

### 16. 进程隔离 (v1.4 拆进程 per-plugin subprocess)
- 解决 v1.3 §3.5.2 C1 根因
- mount namespace / seccomp / Python sandbox

---

## 📅 节奏建议 (2-3 周)

| 周 | 目标 | 关键交付 |
|----|------|----------|
| **W1** | P0 #1+#2+#3 (A2A + scheduler + host CLI) | host 进程能起来，cron + A2A 都能触发 transition |
| **W2** | P0 #4+#5 (端到端测试 + examples) | echo-with-persist 真实 A2A 跑通，3 个新 example |
| **W3** | P1 (admin API + timer + Dockerfile + L1/L3/L4/L5) | prod deploy 准备就绪 |
| **之后** | P2 (docs / 升级 / 跨 CUE / HITL / 进程隔离) | 生态完备 |

---

## 🤝 协作分工

- **丝线 (我)**: PR1-PR3 主体推进 + 测试 + commit + v1.4 落地
- **codeanchor**: 旁路接 P2 #12 (docs 自动生成) — 这是纯 codegen 任务，可并行
- **破晓 (netdawn)**: review §8 permission 实际实现 (跟 spec review 不同，这次看代码) — 必走
- **初梦**: review host process 的 4-stage rollout 准备度

**协作纪律**:
- 丝线不直接调 codeanchor/破晓，经初梦转 (CLAUDE.md 规定)
- 写完即 commit (频繁 commit)
- 防泄漏 Phase 0-3 必走 (memory 规定)

---

## ⚠️ 实操期已识别的"先做"项 (v1.3 → v1.4 必带)

1. **CLI `host` 子命令**比 admin API 更先 — 4-stage rollout stage 1 跑起来才算"落地"
2. **真实 A2A client**比 docker 化更先 — 没客户端，docker 化也跑不动
3. **端到端 mock server**比覆盖率指标更先 — 没有它，stage 1 没法验证
4. **`peer.allow_messages` default** 改严 — 安全的 default 重要（L1）

---

## 🎯 v1.4 freeze 目标

W3 末 (3 周内) 达到：
- [ ] `cue host` 跑通真实 cron + A2A 流程
- [ ] 端到端测试覆盖率 ≥ 10%
- [ ] 3 个新 example + 1 个 production-grade resilience demo
- [ ] Dockerfile + docker-compose 可部署
- [ ] admin API 3 endpoint 可用
- [ ] 全部 v1.3 backlog P0+P1 关闭
- [ ] 破晓 review 通过 §8 代码实现
- [ ] 4-stage rollout stage 1 (dev-only) 实际启动一次

---

*初拟: 2026-06-04 丝线*
*待 review: 初梦 + 破晓*
