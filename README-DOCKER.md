# AgentWire Docker 部署指南

## 前置条件

- Docker Engine 24+（含 Compose V2 插件）
- `agentwire_core/` 与 `agentwire_cue/` 位于同一父目录

```text
A2A/
├── agentwire_core/
└── agentwire_cue/
```

## 准备 secrets

```bash
cd /mnt/d/项目/A2A/agentwire_cue
mkdir -p secrets
printf '%s\n' 'your-a2a-token-here' > secrets/a2a-token.txt
printf '%s\n' 'your-admin-token-here' > secrets/cue-admin-token.txt
chmod 600 secrets/*.txt
```

## 启动

```bash
docker compose up -d
```

## 验证

```bash
curl http://127.0.0.1:18800/.well-known/agent.json
curl http://127.0.0.1:18801/.well-known/agent.json
ADMIN_TOKEN=$(cat secrets/cue-admin-token.txt)
curl -H "Authorization: Bearer $ADMIN_TOKEN" http://127.0.0.1:19000/admin/status
docker compose ps
docker exec agentwire-cue python3 -m agentwire_cue doctor --no-network
```

## 停止

```bash
docker compose down
```

数据保存在 Docker volumes 中；如需删除数据，执行 `docker compose down -v`。
