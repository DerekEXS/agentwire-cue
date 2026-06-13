# AgentWire-Cue SKILL 中文版 (v1.5.7)

> **语言**: 简体中文 | [English](SKILL.md)

## 什么是 AgentWire-Cue?

AgentWire-Cue 是基于 [AgentWire A2A v1.0.1](https://a2a-protocol.org) 协议的 **YAML statechart 插件引擎**。你把工作流写成 `*.yaml` 文件（遵循 `agentwire/v1.2` schema），host 加载后评估 trigger、执行状态转换和 action。

Cue 是**行为**层，AgentWire CORE 网关是**协议+历史**层。Cue 通过 A2A JSON-RPC 连接 CORE，用 `peers.*` 和 `history.*` namespace 表达式消费消息历史，用 `send_a2a` action 发回响应。

## 架构

```
┌────────────────────────────────────────────────┐
│       AgentWire CORE (18800)                    │
│       - JSONL history (按 peer 分文件)         │
│       - JSON-RPC API                            │
│       - Bearer-token 鉴权 (hmac)                │
└─────────────┬──────────────────────────────────┘
              │ HTTP + Bearer
              ▼
┌────────────────────────────────────────────────┐
│       AgentWire-Cue host                       │
│       - 加载插件目录 *.yaml                     │
│       - 执行 statechart 评估                    │
│       - 轮询 messages/peers 触发 history_change │
│       - peers.Pawly.history.last_inbound_contains()│
│       - 18801: A2A 入站 (admin token 鉴权)      │
│       - 19000: admin API (token 鉴权)          │
└────────────────────────────────────────────────┘
        ▲
        │ *.yaml 插件
        │
   插件作者 (你)
```

## 快速启动

### 推荐: Docker Compose

```bash
cd agentwire_cue
mkdir -p secrets
printf '%s\n' 'YOUR_A2A_TOKEN' > secrets/a2a-token.txt
printf '%s\n' 'YOUR_ADMIN_TOKEN' > secrets/cue-admin-token.txt
chmod 600 secrets/*.txt
docker compose up -d
curl http://127.0.0.1:19000/admin/status \
  -H "Authorization: Bearer YOUR_ADMIN_TOKEN"
```

### 备选: Python 原生

```bash
pip install aiohttp ruamel.yaml jsonschema croniter structlog
python3 -m agentwire_cue host \
  --plugin-dir ./examples \
  --a2a-url http://127.0.0.1:18800 \
  --a2a-token-file /tmp/a2a-token.txt \
  --admin-token-file /tmp/cue-admin-token.txt \
  --admin-port 19000 \
  --a2a-listener-port 18801
```

> v1.5.5 起，listener 和 admin 默认绑定 `127.0.0.1`。仅在防火墙/VPN 保护下使用 `--a2a-listener-host 0.0.0.0` / `--admin-host 0.0.0.0`。

## 插件 YAML 结构

```yaml
apiVersion: agentwire/v1.2
kind: plugin

metadata:
  name: my-plugin
  version: 1.0.0

spec:
  triggers:
    - id: on-cron
      type: cron
      config: { expression: "0 8 * * *", timezone: "Asia/Shanghai" }

    - id: on-pawly-urgent
      type: history_change
      config: { peer: "Pawly", granularity: round, poll_interval_seconds: 15 }

    - id: on-a2a
      type: a2a_message_type
      config: { match: "*" }

  requires:
    plugins: []
    peers: [Pawly]
    capabilities: [metadata]

  peers:
    Pawly:
      uuid: "pawly-demo-uuid"
      url: "http://agentwire-core:18800"

  statechart:
    initial: watching
    context: { last_notified_round: 0 }
    states:
      watching:
        "on":
          history_change:
            target: watching
            guard: "peers.Pawly.history.last_inbound_contains('urgent:') && event.new_round > context.last_notified_round"
            actions:
              - type: set_context
                with: { key: last_notified_round, value: "{{event.new_round}}" }
              - type: send_a2a
                with:
                  peer: main
                  message: { type: A2A_MESSAGE, text: "urgent" }

  secrets: []
  permissions:
    network: { http_egress: ["agentwire-core", "127.0.0.1"], raw_socket: false }
    filesystem: []
    subprocess: { allow: [] }
    env: []
    peers: [{ id: main, allow_messages: ["*"] }]
    timers: { max_concurrent: 1, min_interval_ms: 1000 }
```

## 表达式 namespace

| Namespace | 示例 | 说明 |
|-----------|---------|-------|
| `event` | `event.type`, `event.peer` | 触发器事件载荷 |
| `context` | `context.last_notified_round` | 插件状态（持久化） |
| `state` | `state.duration_ms` | 当前状态元数据 |
| `meta` | `meta.name` | 插件元数据 |
| `peers` | `peers.Pawly.history.last(5)` | **v1.4.3** — 按 peer 查询 |
| `history` | `history.total_rounds()` | **v1.4.3** — 跨 peer 聚合 |

## 触发器

| 类型 | 触发时机 | 配置 |
|------|-------------|------|
| `cron` | 定时表达式匹配 | `expression`, `timezone` |
| `a2a_message_type` | 18801 收到入站 A2A 消息 | `match: "*"` 或具体类型 |
| `history_change` (v1.4.3) | peer 轮数变化 | `peer`, `granularity: round`, `poll_interval_seconds` |

## 动作

| 动作 | 用途 |
|--------|---------|
| `log` | 写日志 |
| `set_context` / `increment_context` | 修改 statechart 上下文 |
| `send_a2a` | 向 peer 发送 A2A 消息（支持 metadata block） |
| `reply_a2a` | 回复当前入站消息 |
| `http_request` | HTTP 调用（受 `http_egress` 沙箱约束） |

## Admin API（19000 端口，需 Bearer token）

| 端点 | 返回 |
|----------|---------|
| `GET /admin/status` | 每个插件的运行时状态、`last_trigger_at`、`last_match` |
| `GET /admin/peers` | Peer 别名表，uuid 脱敏（前 6 位 + `...`），url 脱敏到 scheme+host+port |
| `GET /admin/plugins` | 已加载插件列表 |
| `POST /plugins/{name}/trigger` | 手动触发插件 |

## `agentwire-cue doctor`

```bash
python3 -m agentwire_cue doctor \
  --a2a-listener-port 18801 --admin-port 19000 --no-network
```

检查 token hygiene、CORE 可达性（容器 DNS 场景降级 INFO）、端口可用性、代理环境变量泄漏、插件依赖完整性。Docker 镜像内置 `CUE_DOCTOR_A2A_URL=http://agentwire-core:18800`。

## 安全默认值 (v1.5.5+)

- **Inbound listener**: 默认 `127.0.0.1`，用 CUE admin token 做 Bearer 鉴权（**破坏性变更**: 调用方须使用 admin token，非旧版 A2A token）。
- **Admin API**: 默认 `127.0.0.1`，`hmac.compare_digest` token 比对。
- **send_a2a**: 当 `permissions.peers` 非空时，仅允许列表中声明的 peer。
- **Compose**: 宿主机端口默认以 `127.0.0.1` 前缀发布。

## 参考阅读

- [PLUGIN_AUTHORING.md](PLUGIN_AUTHORING.md) — 字段参考
- [EXPRESSION_REFERENCE.md](EXPRESSION_REFERENCE.md) — 表达式语法
- [INTEGRATION_OpenClaw_Hermes_QwenPaw_Claude.md](INTEGRATION_OpenClaw_Hermes_QwenPaw_Claude.md) — 平台接入
