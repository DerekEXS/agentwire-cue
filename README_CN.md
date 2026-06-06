# AgentWire-Cue

> **YAML 驱动的 statechart 引擎** —— 给 A2A v1.0.1 agent 用,跑在 [AgentWire-Core](https://github.com/DerekEXS/agentwire-core) 之上
>
> **语言**: 简体中文 (本文) | [English](README.md)

[![A2A Protocol](https://img.shields.io/badge/A2A-v1.0.1-blue)](https://a2a-protocol.org/latest/specification/)
[![许可证](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org)
[![状态](https://img.shields.io/badge/status-v1.4.3-yellow)](https://github.com/DerekEXS/agentwire-cue/releases)

---

## 什么是 AgentWire-Cue?

AgentWire-Cue 是一个**插件 host**,加载 YAML 定义的 statechart,对 A2A v1.0.1 wire 上的事件做出反应。Cue 消费 AgentWire-Core 的 A2A 服务:通过 JSON-RPC 读消息历史、根据最新轮次求值状态 guard、通过网关回送动作。

在以下场景用 cue:
- 用**声明式 YAML** 定义 agent 行为(无 Python 样板)
- 触发**消息历史**驱动的工作流(如 "Pawly 刚说了 X")
- 跑**cron 风格**的后台任务,参考会话上下文
- 跨 QwenPaw / OpenClaw / Hermes / Claude 编排**多 peer** 会话

Cue **不是** agent 框架。它不生成回复,只执行**你**在 YAML 里描述的行为。

## 特性

- **YAML 插件格式** —— 4 个原语:triggers、statechart、actions、permissions
- **两种 trigger 类型** —— `cron` 和 `a2a_message_type`(v1.4.3 新增 `history_change`)
- **两个新命名空间**(v1.4.3) —— `peers.<name>.history.*` 和 `history.*` 用于跨 peer 查询
- **方法调用语法**(v1.4.3) —— `peers.Pawly.history.last_inbound_contains("project:")`
- **4 层路径沙箱** 用于文件和 subprocess 动作
- **3 层 HTTP egress** 白名单(spec / 插件 / flag)
- **5 类权限执行器**(persist / network / subprocess / secret / admin)
- **Bearer-token admin API** 监听 19000(`/status`、`/plugins`、`/trigger`)
- **持久化 context** 带敏感字段排除
- **250+ 单元测试** 覆盖语法、动作、权限

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

Cue 需要 AgentWire-Core 同时运行。

```bash
# 1. 装 cue
pip install aiohttp ruamel.yaml jsonschema croniter structlog

# 2. 启动 AgentWire-Core
git clone https://github.com/DerekEXS/agentwire-core.git
cd agentwire-core/server
pip install -r requirements.txt
echo "my-token-123" > /tmp/agentwire.token
python3 start.py --host 127.0.0.1 --port 18800 --token-file /tmp/agentwire.token
cd ../..

# 3. 启动 cue host
cd agentwire-cue
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

v1.4.3 用到 history 的插件:

```yaml
id: pawly_responder
version: 1.0.0

triggers:
  - name: pawly_replied
    type: history_change      # v1.4.3
    peer: "Pawly"
    granularity: round
    poll_interval_seconds: 30

statechart:
  initial: watching

  states:
    watching:
      transitions:
        - when: "peers.Pawly.history.last_inbound_contains('project:')"
          target: kickoff

    kickoff:
      on_enter:
        - action: send_a2a
          params:
            peer: "Pawly"
            text: "Got the project brief. Starting work on round {{ peers.Pawly.last_round }}."
      transitions:
        - { target: watching }
```

## CLI

```bash
# 跑 host
python3 -m agentwire_cue host --plugin-dir ./plugins --a2a-url http://127.0.0.1:18800

# 校验插件
python3 -m agentwire_cue validate plugins/my_plugin.yaml

# 手动触发插件
python3 -m agentwire_cue trigger my_plugin manual --payload '{"foo": "bar"}'

# 看状态
python3 -m agentwire_cue status
```

## 文档

- [skill/SKILL.md](skill/SKILL.md) —— 快速入门
- [skill/PLUGIN_AUTHORING.md](skill/PLUGIN_AUTHORING.md) —— 完整插件编写指南
- [skill/EXPRESSION_REFERENCE.md](skill/EXPRESSION_REFERENCE.md) —— 表达式语法
- [skill/INTEGRATION_OpenClaw_Hermes_QwenPaw_Claude.md](skill/INTEGRATION_OpenClaw_Hermes_QwenPaw_Claude.md) —— 在不同平台跑

## 配套项目

AgentWire-Cue 需要 [AgentWire-Core](https://github.com/DerekEXS/agentwire-core) 跑在同一台机器(默认 `127.0.0.1:18800`)。CORE 提供:

- A2A v1.0.1 协议面(JSON-RPC、REST)
- Bearer-token 鉴权
- 按 peer 消息历史持久化
- 脱敏 pattern 目录
- 指标端点

## 仓库结构

```
agentwire-cue/
├── core/                      # 库代码
│   ├── expression.py          # v1.4.3: + peers/history 命名空间
│   ├── history_client.py      # v1.4.3 新增
│   ├── history_proxy.py       # v1.4.3 新增
│   ├── redact.py              # v1.4.3 新增
│   ├── host.py                # v1.4.3: + history trigger 接入
│   ├── trigger_impl.py        # v1.4.3: + HistoryChangeTrigger
│   ├── statechart.py          # v1.4.3: + history_client in EvalEnv
│   ├── a2a_client.py
│   ├── loader.py
│   ├── permission.py
│   └── ...
├── skill/                     # v1.4.3 新增:agent 文档
│   ├── SKILL.md / SKILL_CN.md
│   ├── PLUGIN_AUTHORING.md
│   ├── EXPRESSION_REFERENCE.md
│   └── INTEGRATION_OpenClaw_Hermes_QwenPaw_Claude.md
├── examples/                  # cue 插件示例
├── tests/                     # 250+ 单元测试
├── schema/
│   └── plugin.schema.json
├── __main__.py                # CLI 入口
└── README.md
```

## 协议

- **A2A v1.0.1** —— <https://a2a-protocol.org/latest/specification/>
- **JSON-RPC 2.0** —— <https://www.jsonrpc.org/specification>

## 参考

- [A2A 协议规范](https://a2a-protocol.org/latest/specification/)
- [AgentWire-Core](https://github.com/DerekEXS/agentwire-core) (配套)
- [发布](https://github.com/DerekEXS/agentwire-cue/releases)

## 许可证

**MIT License** —— Copyright (c) 2026 DerekEXS。完整文本见 [LICENSE](LICENSE)。

可自由使用、修改、分发本软件(有或无修改),前提是保留版权声明和许可声明。
