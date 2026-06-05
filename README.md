# AgentWire-Cue — YAML-driven plugin host

AgentWire-Cue is a **YAML-driven statechart engine** that consumes the A2A (Agent-to-Agent) protocol. Define plugin workflows as `cue.yaml` — pure YAML, zero custom code.

## Quick Start

```bash
pip install agentwire-cue
cue validate my-plugin.yaml
cue host --plugin-dir ./plugins --admin-token=xxx
```

## Repository Structure

```
agentwire-cue/
├── core/              # Python host runtime
│   ├── loader.py      # YAML plugin loader
│   ├── statechart.py  # 6-step transition engine
│   ├── expression.py  # Expression parser + evaluator
│   ├── permission.py  # 5-category enforcer
│   ├── sandbox.py     # 5-surface path sandbox
│   └── ...
├── tests/             # pytest suite (250 tests)
├── examples/          # 5 demo cue.yaml plugins
├── schema/            # plugin.schema.json
└── __main__.py        # CLI entrypoint
```

## Compatibility

- **Python 3.11+**
- **AgentWire A2A v1.0.1** (companion service on port 18800)
- Requires: [agentwire-core](https://github.com/DerekEXS/agentwire-core) (A2A JSON-RPC upstream)

## License

Private — not for redistribution.
