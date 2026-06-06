# AgentWire-Cue SKILL 中文版

> **语言**: 简体中文 | [English](SKILL.md)

## 什么是 AgentWire-Cue?

AgentWire-Cue 是跑在 [AgentWire A2A v1.0.1](https://a2a-protocol.org) 网关之上的**YAML 驱动的 statechart 引擎**。你用 `*.yaml` 文件定义插件工作流;host 加载它们、求值状态转移、执行动作。

Cue 是**状态与行为**层。AgentWire CORE 网关是**协议与持久化**层。Cue 通过 A2A JSON-RPC 连 CORE,消费 history,再通过 A2A 消息回送动作结果。

## 架构

```
┌────────────────────────────────────────────────┐
│       AgentWire CORE (18800) — 协议层          │
│       - history.jsonl (按 peer)                │
│       - JSON-RPC API                            │
└─────────────┬──────────────────────────────────┘
              │ HTTP + Bearer
              ▼
┌────────────────────────────────────────────────┐
│       AgentWire-Cue host                       │
│       - 加载 plugins/*.yaml                    │
│       - 求值 statechart                        │
│       - 轮询 /messages/peers 触发 history_change│
│       - history.peer("X").last(5) 用于表达式  │
│       - 18801: A2A listener (cue → peer)     │
│       - 19000: admin API (Bearer)              │
└────────────────────────────────────────────────┘
        ▲
        │ 读 *.yaml
        │
   插件作者(你)
```

## 插件 YAML 剖析

```yaml
id: my_plugin
version: 1.0.0
description: 插件描述

triggers:
  - name: on_cron
    type: cron
    expression: "0 8 * * *"
  - name: on_pawly_round
    type: history_change      # v1.4.3 新增
    peer: "Pawly"
    granularity: round
  - name: on_a2a_msg
    type: a2a_message_type
    match: "*"

statechart:
  initial: idle
  context:
    foo: "bar"

  states:
    idle:
      transitions:
        - when: "peers.Pawly.last_round > 0"
          target: processing
        - when: "history.total_rounds() > 100"
          target: high_traffic
      on_enter:
        - action: log
          params: { message: "进入 idle" }

    processing:
      on_enter:
        - action: http_request
          params:
            url: "http://127.0.0.1:18800/a2a/jsonrpc"
            method: POST
            body:
              jsonrpc: "2.0"
              id: 1
              method: "messages/list"
              params: { peer_name: "Pawly", limit: 5 }
          save_as: context.recent
      transitions:
        - when: "context.recent != null"
          target: done

    high_traffic: {}
    done: {}
```

## 表达式命名空间 (v1.4.3+)

Cue 表达式支持这些顶层命名空间:

| 命名空间 | 示例 | 说明 |
|----------|------|------|
| `event` | `event.type` | 触发事件载荷 |
| `context` | `context.user_name` | 插件状态(持久化) |
| `state` | `state.duration_ms` | 当前状态元数据 |
| `meta` | `meta.plugin_id` | 插件元数据 |
| `now` | `now`(函数) | 当前时间戳 |
| `peers` | `peers.Pawly.history.last(5)` | **v1.4.3** — 按 peer |
| `history` | `history.total_rounds()` | **v1.4.3** — 跨 peer |

`peers.<name>` 返回 peer 代理对象。`peers.<name>.history` 上的方法:
- `last(n=5)` → 最近 n 条消息
- `last_n_rounds(n=5)` → `last(n)` 别名
- `count()` → 总轮数
- `last_round()` → 最高轮号
- `last_inbound_contains(needle)` → bool
- `last_outbound_contains(needle)` → bool

`history.*` 用于跨 peer:
- `total_rounds()` → 所有 peer 之和
- `peer_count()` → 已知 peer 数
- `peer_names()` → peer 名列表

## 触发器

| 类型 | 触发时机 | 示例 |
|------|----------|------|
| `cron` | cron 表达式匹配 | `{ type: cron, expression: "0 8 * * *", timezone: "Asia/Shanghai" }` |
| `a2a_message_type` | 入站 A2A 消息匹配 | `{ type: a2a_message_type, match: "request" }` |
| `history_change`(v1.4.3) | peer 轮数变化 | `{ type: history_change, peer: "Pawly", granularity: round, poll_interval_seconds: 30 }` |

## 动作

Cue 插件可调用这些动作:

| 动作 | 用途 |
|------|------|
| `http_request` | 发 HTTP 请求(沙箱) |
| `write_file` | 在 persist 目录写文件 |
| `read_file` | 从 persist 目录读文件 |
| `spawn_subprocess` | 跑 shell 命令(沙箱) |
| `log` | 写一行日志 |
| `send_a2a` | 通过 CORE 给 peer 发 A2A 消息 |

## 沙箱与权限

Cue 强制 4 层路径沙箱:
- **L1 默认**: 插件的 persist 目录
- **L2 spec**: 插件 `permissions.persist.allowed_parents` 声明的路径
- **L3 CLI**: `--persist-allow` 传的路径
- **L4 屏蔽**: 硬编码黑名单(如 `/etc`、`~/.ssh`)

## 配套:AgentWire CORE

Cue **不是** 独立的 A2A 网关。它依赖 AgentWire CORE 服务提供:
- HTTP 端点和 JSON-RPC 协议
- Bearer-token 鉴权
- 按 peer 消息历史持久化
- 脱敏 pattern 目录

要跑 cue,你必须在同机启动 CORE(默认 `127.0.0.1:18800`)。

## 快速开始

```bash
# 1. 启动 CORE
cd ../agentwire_core/server
python3 start.py --host 127.0.0.1 --port 18800 --token-file /tmp/token.txt

# 2. 装 cue
cd ../../agentwire_cue
pip install aiohttp ruamel.yaml jsonschema croniter structlog

# 3. 写插件
mkdir -p plugins
cat > plugins/hello.yaml <<'YAML'
id: hello
version: 1.0.0
triggers:
  - name: on_message
    type: a2a_message_type
    match: "*"
statechart:
  initial: greeted
  states:
    greeted:
      on_enter:
        - action: log
          params: { message: "Hello from cue!" }
YAML

# 4. 跑 cue host
python3 -m agentwire_cue host \
  --plugin-dir ./plugins \
  --a2a-url http://127.0.0.1:18800 \
  --a2a-token-file /tmp/token.txt \
  --admin-token "admin-secret"

# 5. 给 CORE 发消息,cue 会反应
curl -X POST http://127.0.0.1:18800/a2a/rest/message/send \
  -H "Authorization: Bearer $(cat /tmp/token.txt)" \
  -H "Content-Type: application/json" \
  -d '{"message":{"parts":[{"type":"text","text":"hi"}]}}'
```

## 另见

- [PLUGIN_AUTHORING.md](PLUGIN_AUTHORING.md) — 详细插件编写指南
- [EXPRESSION_REFERENCE.md](EXPRESSION_REFERENCE.md) — 完整表达式语法
- [INTEGRATION_OpenClaw_Hermes_QwenPaw_Claude.md](INTEGRATION_OpenClaw_Hermes_QwenPaw_Claude.md) — 在不同平台跑 cue

## 许可证

MIT License。Copyright (c) 2026 DerekEXS。详见 [LICENSE](../LICENSE)。
