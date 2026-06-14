# Cue Integration: OpenClaw / Hermes / QwenPaw / Claude (v1.6.1)

> How to install and run AgentWire-Cue on different agent platforms.

## Recommended: Docker Compose (all platforms)

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

## Native Python (alternative)

```bash
pip install aiohttp ruamel.yaml jsonschema croniter structlog
python3 -m agentwire_cue host \
  --plugin-dir ./examples \
  --a2a-url http://127.0.0.1:18800 \
  --a2a-token-file /tmp/token.txt \
  --admin-token-file /tmp/admin-token.txt \
  --admin-port 19000 \
  --a2a-listener-port 18801
```

> From v1.5.5: listener and admin bind `127.0.0.1` by default.
> Use `--a2a-listener-host 0.0.0.0` / `--admin-host 0.0.0.0` only behind firewall/VPN.

## Platform-specific notes

### OpenClaw

Cue runs alongside OpenClaw. The Docker compose in this repo starts both CORE and CUE.

#### Inbound auth change (v1.5.5+)

The `/a2a/inbound` endpoint now expects the **CUE admin token** (not the old A2A token). Update any OpenClaw routing config that sends to `http://agentwire-cue:18801/a2a/inbound` accordingly.

### QwenPaw / Pawly

Pawly communicates via CORE's standard A2A JSON-RPC surface at port 18800. The CUE listener at 18801 is for host-internal inbound routing.

### Hermes / Claude

Use the standard CORE REST and JSON-RPC endpoints. For CUE-specific admin access, send `Authorization: Bearer <admin-token>` to port 19000.

## See also

- [SKILL.md](SKILL.md) — CUE host guide
- [PLUGIN_AUTHORING.md](PLUGIN_AUTHORING.md) — plugin field reference
- [EXPRESSION_REFERENCE.md](EXPRESSION_REFERENCE.md) — expression syntax
