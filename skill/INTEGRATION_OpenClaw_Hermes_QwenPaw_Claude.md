# Cue Integration: OpenClaw / Hermes / QwenPaw / Claude

> How to install and run AgentWire-Cue on different agent platforms.

## OpenClaw

OpenClaw bundles a JS/TS runtime with a plugin loader. Cue is a Python process, so you run it alongside OpenClaw and wire them via the A2A listener.

```bash
# 1. Install cue dependencies
pip install aiohttp ruamel.yaml jsonschema croniter structlog

# 2. Run cue host
python3 -m agentwire_cue host \
  --plugin-dir ./plugins \
  --a2a-url http://127.0.0.1:18800 \
  --a2a-token-file /etc/agentwire/token \
  --admin-port 19000
```

OpenClaw talks to cue through CORE: OpenClaw → CORE (18800) → cue (18801).

## Hermes

Hermes uses a generic process model. Run cue as a sidecar.

```bash
# Sidecar: cue host + CORE + Hermes in the same docker-compose
docker-compose up -d agentwire-core agentwire-cue hermes
```

Hermes agents → CORE (18800) → cue (18801) reacts via statecharts.

## QwenPaw

QwenPaw is Python-native. Run cue in the same Python environment.

```bash
# One venv, both packages
python3 -m venv .venv
source .venv/bin/activate
pip install agentwire-cue aiohttp ruamel.yaml jsonschema croniter structlog

# Run cue as a long-lived background process
python3 -m agentwire_cue host \
  --plugin-dir ./plugins \
  --a2a-url http://127.0.0.1:18800 \
  --a2a-token-file /tmp/token.txt
```

QwenPaw agents (Pawly, etc.) → CORE → cue.

## Claude (Code / API)

Two patterns:

### A. Claude Code sidecar

```bash
# 1. CORE running
python3 .../agentwire_core/server/start.py --token-file /tmp/token.txt

# 2. Cue running
python3 -m agentwire_cue host --plugin-dir ./plugins ...

# 3. In each Claude Code session, the agent can:
#   - Read cue's history via:
#     curl -X POST http://127.0.0.1:18800/a2a/jsonrpc \
#       -H "Authorization: Bearer $TOKEN" \
#       -d '{"jsonrpc":"2.0","id":1,"method":"messages/list","params":{"limit":5}}'
#   - Send messages to peers via CORE
```

### B. Claude API as a backend

If you have a Claude API-driven agent that you want to register as an A2A peer:

1. Set up the Claude API agent to listen on a port (e.g. 19999)
2. Register it as a peer in CORE by hitting `/agents` discovery (or via a custom adapter)
3. Core routes messages to that agent's URL
4. Cue reacts to history changes triggered by Claude API responses

## Cross-platform note

Cue itself is platform-agnostic — it speaks A2A JSON-RPC to CORE. The platform-specific glue is in CORE's `plugin/` (for OpenClaw) and the agent-side adapters (for QwenPaw / Hermes / Claude). You only need to run cue the same way regardless of which platform is generating the A2A traffic.

## See also

- [SKILL.md](SKILL.md)
- [PLUGIN_AUTHORING.md](PLUGIN_AUTHORING.md)
- [EXPRESSION_REFERENCE.md](EXPRESSION_REFERENCE.md)
- agentwire-core's [INTEGRATION_*.md](../agentwire-core/skill/) docs for client-side recipes
