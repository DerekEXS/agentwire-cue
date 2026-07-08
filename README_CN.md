# AgentWire-Cue

> **YAML 驱动的 statechart 引擎** —— 给 A2A v1.0.1 agent 用,跑在 [AgentWire-Core](https://github.com/DerekEXS/agentwire-core) 之上
>
> **语言**: 简体中文 (本文) | [English](README.md)

[![A2A Protocol](https://img.shields.io/badge/A2A-v1.0.1-blue)](https://a2a-protocol.org/latest/specification/)
[![许可证](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org)
[![状态](https://img.shields.io/badge/status-v2.0.0-green)](https://github.com/DerekEXS/agentwire-cue/releases/tag/v2.0.0)

---

## 什么是 AgentWire-Cue?

AgentWire-Cue 是一个**插件 host**,加载 YAML 定义的 statechart,对 A2A v1.0 wire 上的事件做出反应。Cue 消费 AgentWire-Core 的 A2A 服务:接收 CORE v2.0 通过 `_forward_to_cue()` 通道推送过来的标准 A2A JSON-RPC 事件、根据最新数据求值状态 guard、通过网关回送动作。

在以下场景用 cue:
- 用**声明式 YAML** 定义 agent 行为(无 Python 样板)
- 触发**消息历史**驱动的工作流(如 "<your-remote-peer-name> 刚说了 X")
- 跑**cron 风格**的后台任务,参考会话上下文
- 跨 QwenPaw / OpenClaw / Hermes / Claude 编排**多 peer** 会话

Cue **不是** agent 框架。它不生成回复,只执行**你**在 YAML 里描述的行为。

## 特性

- **YAML 插件格式** —— triggers、statechart、actions、permissions (apiVersion `agentwire/v1.2`)
- **三种 trigger** —— `cron`、`a2a_message_type`、`history_change` (v1.4.3)
- **表达式引擎** —— `peers.<name>.history.*`、`history.*`、`event.*`、`context.*`、`meta.*`、`now.*` 命名空间;方法调用语法 `peers.<your-remote-peer-name>.history.last_inbound_contains("keyword")` (v1.4.3)
- **`spec.peers` 别名** —— peer UUID/URL 表用于稳定历史查找和直接 A2A 路由 (v1.4.8)
- **`spec.requires` 跨插件依赖** —— `plugins` / `peers` / `capabilities` 依赖检查 (v1.5.2)
- **`send_a2a` 含 metadata** —— 可选 `metadata` 块带模板渲染 (v1.4.8)
- **`send_a2a` workflow-pointer** —— `metadata.workflow_pointer` 设好 A2A task 交接阶段 (v1.5.0)
- **`spec.resilience.on_exhaust`** —— loader 时校验目标 state 有效性 (v1.5.9)
- **`permissions.peers` 强制** —— 非空 peer 白名单把关 `send_a2a` (v1.5.5)
- **Admin API 19000** —— `/admin/status`、`/admin/peers` (uuid/url 脱敏、30s 可达性缓存)、`/admin/plugins` (v1.5.1/v1.5.6)
- **`agentwire-cue doctor`** —— 全面启动前检查:token 文件 BOM/CRLF/chmod、CORE 可达 (容器内降级)、端口冲突 (v1.5.2/v1.5.7)
- **结构化可观测** —— 每条 trigger 一个 trace_id,`cue.trigger.*` / `cue.guard.*` / `cue.action.*` / `cue.send_a2a.*` / `cue.error` JSON 行事件 (v1.5.1)
- **安全默认值** —— A2A listener + admin API 默认 `127.0.0.1`; `/a2a/inbound` 要求 admin token (v1.5.5);非 loopback+无 token 直接拒收 (v1.5.6)
- **4 层路径沙箱** 用于文件和 subprocess 动作
- **330+ 单元测试** 覆盖语法、动作、权限、admin、doctor

## 架构

```
┌────────────────────────────────────────────────┐
│   AgentWire-Core gateway (18800)               │
│   - JSON-RPC + 按 peer 历史                    │
└─────────────┬──────────────────────────────────┘
              │ A2A JSON-RPC over HTTP
              ▼
┌────────────────────────────────────────────────┐
│   AgentWire-Cue host                           │
│   ┌────────────────────────────────────────┐  │
│   │  插件加载器 (plugins/*.yaml)            │  │
│   │  表达式引擎 (event/context/             │  │
│   │    state/meta/peers/history/now)       │  │
│   │  动作调度器 (http/write/etc)            │  │
│   │  4 层沙箱 + 权限执行器                  │  │
│   │  trigger 调度器 (cron/a2a/history)      │  │
│   │  peer card 缓存 (10 分钟 TTL)          │  │
│   └────────────────────────────────────────┘  │
│   18801: A2A listener (peer 入站)             │
│   19000: Admin API (Bearer 保护)              │
└────────────────────────────────────────────────┘
```

## 快速开始

Cue 需要 AgentWire-Core。推荐 Docker Compose:

```bash
# 1. 克隆 CUE 仓(含 CORE + CUE 统一 compose)
git clone https://github.com/DerekEXS/agentwire-cue.git
cd agentwire-cue

# 2. 准备 secrets
mkdir -p secrets
printf '%s\n' 'YOUR_A2A_TOKEN' > secrets/a2a-token.txt
printf '%s\n' 'YOUR_CUE_ADMIN_TOKEN' > secrets/cue-admin-token.txt
chmod 600 secrets/*.txt

# 3. 启动 CORE + CUE
docker compose up -d

# 4. 验证
docker compose ps
curl -s http://127.0.0.1:18800/.well-known/agent.json
curl -s http://127.0.0.1:18801/.well-known/agent.json

# 5. 容器内 doctor
docker exec agentwire-cue python3 -m agentwire_cue doctor --no-network
```

CORE 监听 `127.0.0.1:18800`;CUE A2A listener 监听 `127.0.0.1:18801`;admin API 监听 `127.0.0.1:19000`。

### 独立运行(Python 直接起)

```bash
# 1. 装依赖
pip install aiohttp ruamel.yaml jsonschema croniter structlog

# 2. 先启动 AgentWire-Core(见其 README)
# 3. 启动 CUE host
python3 -m agentwire_cue host \
  --plugin-dir ./plugins \
  --a2a-url http://127.0.0.1:18800 \
  --a2a-token-file /tmp/agentwire.token \
  --admin-port 19000 \
  --admin-token "admin-secret"
```

## 插件示例

`plugins/greet.yaml`:

```yaml
id: greet
version: 1.0.0
description: 收到第一条 A2A 消息时记录问候

permissions:
  persist: { allowed_parents: [] }
  network: { http_egress: [] }

triggers:
  - name: on_a2a_message
    type: a2a_message_type
    match: "*"

statechart:
  initial: greeting
  context: { greeted: false }

  states:
    greeting:
      on_enter:
        - action: log
          params: { message: "Hello from cue!" }
        - action: set_context
          params: { key: greeted, value: true }
      transitions:
        - { target: idle }

    idle: {}
```

v1.4.8+ 带 peer 别名的 history 插件:

```yaml
id: remote-peer-responder
version: 1.0.0

peers:
  <your-remote-peer-name>:
    uuid: "<your-remote-peer-name>-demo-uuid"
    url: "http://<your-remote-peer-name>:18800"

triggers:
  - name: remote-peer-replied
    type: history_change
    peer: "<your-remote-peer-name>"
    granularity: round
    poll_interval_seconds: 30

statechart:
  initial: watching

  states:
    watching:
      transitions:
        - when: "peers.<your-remote-peer-name>.history.last_inbound_contains('project:')"
          target: kickoff

    kickoff:
      on_enter:
        - action: send_a2a
          params:
            peer: "<your-remote-peer-name>"
            text: "Got the project brief. Starting work on round {{ peers.<your-remote-peer-name>.last_round }}."
      transitions:
        - { target: watching }
```

更多细节见 [`skill/PLUGIN_AUTHORING.md`](skill/PLUGIN_AUTHORING.md)。

## CLI

```bash
# 跑 host
python3 -m agentwire_cue host --plugin-dir ./plugins --a2a-url http://127.0.0.1:18800

# 校验插件
python3 -m agentwire_cue validate plugins/my_plugin.yaml

# 手动触发插件
python3 -m agentwire_cue trigger my_plugin manual --payload '{"foo": "bar"}'

# 飞行前 doctor(仅本地检查)
python3 -m agentwire_cue doctor --no-network

# 飞行前 doctor(完整,含 CORE 可达性探测)
python3 -m agentwire_cue doctor
```

## 文档

- [skill/SKILL.md](skill/SKILL.md) —— 快速入门
- [skill/PLUGIN_AUTHORING.md](skill/PLUGIN_AUTHORING.md) —— 完整 YAML 字段参考(v1.2 schema + 全部 v1.5.x 动作)
- [skill/EXPRESSION_REFERENCE.md](skill/EXPRESSION_REFERENCE.md) —— 表达式语法(peers.*、history.*、event.*、context.*、meta.*)
- [skill/INTEGRATION_OpenClaw_Hermes_QwenPaw_Claude.md](skill/INTEGRATION_OpenClaw_Hermes_QwenPaw_Claude.md) —— 平台接入
- [README-DOCKER.md](README-DOCKER.md) —— Docker 部署详情 + 从 systemd 迁移指南

## 配套项目

AgentWire-Cue 需要 [AgentWire-Core](https://github.com/DerekEXS/agentwire-core) 跑在同一台机器(默认 `127.0.0.1:18800`)。CORE 提供 A2A v1.0.1 协议面(JSON-RPC、REST)、Bearer-token 鉴权(HMAC 常量时间比较)、按 peer JSONL 消息历史(自动脱敏)、脱敏 pattern 目录、TLS 支持、metrics 端点。

## 仓库结构

```
agentwire-cue/
├── __init__.py                 # __version__ = "1.6.0"
├── __main__.py                 # CLI 入口
├── core/                       # 库代码
│   ├── host.py                 # 插件 host:加载 → 启动 → trigger 注册
│   ├── statechart.py           # 表达式 statechart 引擎(guard 求值、动作)
│   ├── expression.py           # tokenizer + parser,方法调用语法
│   ├── loader.py               # YAML loader + schema 校验 + on_exhaust 检查
│   ├── a2a_client.py           # A2A HTTP 客户端 + retry 策略 + peer card 缓存
│   ├── history_client.py       # CORE JSON-RPC 历史代理
│   ├── history_proxy.py        # 表达式求值 PeersNamespace
│   ├── permission.py           # PermissionEnforcer (persist/network/subprocess/secret/admin)
│   ├── observability.py        # 结构化 JSON 行可观测 (trace_id, emit)
│   ├── redact.py               # RedactClient(从 CORE /redact/patterns 拉 pattern)
│   ├── trigger_impl.py         # HistoryChangeTrigger 配置轮询
│   ├── doctor.py               # 飞行前健康检查
│   ├── sandbox.py              # 4 层路径沙箱
│   └── ...
├── skill/                      # agent 文档
│   ├── SKILL.md / SKILL_CN.md
│   ├── PLUGIN_AUTHORING.md
│   ├── EXPRESSION_REFERENCE.md
│   └── INTEGRATION_OpenClaw_Hermes_QwenPaw_Claude.md
├── tests/                      # 330+ 单元测试(334 passed, 6 skipped)
├── examples/                   # 示例 cue 插件
│   ├── echo-with-persist.yaml  # v0.4.0
│   ├── cron-driven.yaml
│   ├── a2a-with-fallback.yaml
│   ├── file-watcher.yaml
│   └── owner-alert/cue.yaml    # history_change 杀手级示例
├── schema/
│   └── plugin.schema.json      # agentwire/v1.2 JSON Schema
├── Dockerfile                  # v1.5.3: python:3.13-slim,非 root agentwire 用户
├── docker-compose.yml          # CORE + CUE 统一 Compose(host loopback 发布)
├── README.md
└── README-DOCKER.md
```

## 协议

- **A2A v1.0.1** —— <https://a2a-protocol.org/latest/specification/>
- **JSON-RPC 2.0** —— <https://www.jsonrpc.org/specification>

## 部署说明

> **Docker Compose 是规范部署方式**(CUE v2.0.0+)。见仓库根目录 `docker-compose.yml`。

Docker 镜像:CORE `agentwire-core:v2.0.1` / CUE `agentwire-cue:v2.0.0`。两者默认绑 `127.0.0.1`,端口仅 host-loopback 发布。

### Docker Compose(推荐)

```bash
cd agentwire-cue
mkdir -p secrets
printf '%s\n' 'YOUR_A2A_TOKEN' > secrets/a2a-token.txt
printf '%s\n' 'YOUR_CUE_ADMIN_TOKEN' > secrets/cue-admin-token.txt
chmod 600 secrets/*.txt
docker compose up -d
```

从旧 systemd/nohup 部署迁移、历史卷迁移、生产 owner-alert 配置见 [`README-DOCKER.md`](README-DOCKER.md)。

### 安全说明

- A2A listener + admin API 默认 `127.0.0.1` (v1.5.5 起)。绑 `0.0.0.0` 需显式传 `--a2a-listener-host 0.0.0.0` / `--admin-host 0.0.0.0` 并打 startup warning。
- `/a2a/inbound` 要求 CUE admin token Bearer 鉴权(v1.5.5 破坏性变更——旧版 A2A token 不再接受)。
- 非 loopback 绑 + 无 token → HTTP 403 拒收 (v1.5.6)。
- `send_a2a` 在 `permissions.peers` 非空时强制白名单检查 (v1.5.5)。

**适用于**:loopback、私有 LAN、Tailscale、WireGuard

**不适用于**:无 TLS 终止反向代理直接暴露公网

## 参考

- [A2A 协议规范](https://a2a-protocol.org/latest/specification/)
- [AgentWire-Core](https://github.com/DerekEXS/agentwire-core) (配套)
- [发布](https://github.com/DerekEXS/agentwire-cue/releases)

## 许可证

**MIT License** —— Copyright (c) 2026 DerekEXS。完整文本见 [LICENSE](LICENSE)。

可自由使用、修改、分发本软件(有或无修改),前提是保留版权声明和许可声明。
