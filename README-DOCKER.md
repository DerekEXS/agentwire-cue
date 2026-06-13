# AgentWire Docker 部署指南

## 官方 compose 位置

官方 Docker Compose 文件位于 CUE 仓库根目录：

```bash
cd agentwire_cue/
docker compose up -d
```

`/mnt/d/项目/A2A/docker-compose.yml` 仅保留弃用提示，避免工作区副本与发布版配置分叉。

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
cd agentwire_cue/
mkdir -p secrets
printf '%s\n' 'your-a2a-token-here' > secrets/a2a-token.txt
printf '%s\n' 'your-admin-token-here' > secrets/cue-admin-token.txt
chmod 600 secrets/*.txt
```

## 默认网络边界

Compose 默认只发布到宿主机 loopback：

```yaml
127.0.0.1:${CORE_PORT:-18800}:18800
127.0.0.1:${CUE_API_PORT:-18801}:18801
127.0.0.1:${CUE_ADMIN_PORT:-19000}:19000
```

如需 LAN/VPN 访问，先确认防火墙、VPN 或 TLS 反代已配置，再移除端口映射前的 `127.0.0.1:`。容器内服务仍会显式使用 `--a2a-listener-host 0.0.0.0` 和 `--admin-host 0.0.0.0`，这样 Docker 才能发布端口；宿主机暴露面由 compose 的 `127.0.0.1:` 前缀控制。不要把 18800/18801/19000 直接暴露到公网。

## 配置生产 owner-alert

仓库里的 `examples/owner-alert/cue.yaml` 保持 demo 安全默认值；生产环境不要把真实 IP、peer uuid 或内网路由提交到仓库。

推荐做法是在本机创建未跟踪的覆盖文件：

```bash
cp examples/owner-alert/cue.yaml examples/owner-alert/cue.local.yaml
```

然后只修改 `examples/owner-alert/cue.local.yaml` 的 `spec.peers`：

```yaml
spec:
  peers:
    Pawly:
      uuid: "<Pawly peer uuid>"
      url: "http://<pawly-host>:18800"
      description: "小爪 - QwenPaw @ 阿里云"
    main:
      uuid: "<初梦本机 peer uuid>"
      url: "http://127.0.0.1:18800"
      description: "初梦 - OpenClaw @ 本地"
```

获取 `main` peer uuid 的方式：

```bash
TOKEN=$(cat secrets/a2a-token.txt)
curl -s -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  http://127.0.0.1:18800/a2a/jsonrpc \
  -d '{"jsonrpc":"2.0","id":1,"method":"messages/peers","params":{}}'
```

也可以查看现有 history 文件名：

```bash
find ~/.local/share/agentwire/history -name 'peer_*.jsonl' -maxdepth 1
```

确认本机覆盖文件不会被提交：

```bash
git status --short examples/owner-alert/cue.local.yaml
# 应无输出；.gitignore 会忽略 examples/**/*.local.yaml
```

如果要让 compose 使用生产覆盖文件，可把 `docker-compose.yml` 中的插件挂载从 `./examples:/plugins:ro` 改为一个本机专用插件目录，或在部署前把生产文件复制到受控的 `/plugins` 挂载目录。

## 从旧版本迁移

### 1. 停止旧 systemd 服务

旧 `agentwire.service` 可能设置了 `Restart=always`，只 `stop` 会被自动拉起。迁移前先 disable：

```bash
systemctl --user stop agentwire.service agentwire-proxy.service 2>/dev/null || true
systemctl --user disable agentwire.service agentwire-proxy.service 2>/dev/null || true
systemctl --user status agentwire.service --no-pager 2>/dev/null || true
```

如果服务文件已退役，可改名避免误启：

```bash
mv ~/.config/systemd/user/agentwire.service ~/.config/systemd/user/agentwire.service.retired 2>/dev/null || true
systemctl --user daemon-reload
```

### 2. 停止旧手工进程

```bash
pgrep -af 'agentwire|agentwire_cue|start.py'
# 确认 PID 后再终止，不要批量 kill 未识别进程
kill <pid>
```

### 3. 迁移 CORE history

Docker compose 使用 named volume `agentwire_cue_core_history` 持久化 CORE history。先启动一次服务创建 volume：

```bash
docker compose up -d agentwire-core
docker compose stop agentwire-core
```

把旧 history 复制进 volume：

```bash
docker run --rm \
  -v agentwire_cue_core_history:/to \
  -v ~/.local/share/agentwire/history:/from:ro \
  alpine sh -c 'cp -a /from/. /to/'
```

### 4. 迁移 token

```bash
mkdir -p secrets
cp ~/.openclaw/a2a-token.txt secrets/a2a-token.txt
printf '%s\n' '<new-admin-token>' > secrets/cue-admin-token.txt
chmod 600 secrets/*.txt
```

### 5. 启动与验证

```bash
docker compose up -d
docker compose ps
curl http://127.0.0.1:18800/.well-known/agent.json
curl http://127.0.0.1:18801/.well-known/agent.json
ADMIN_TOKEN=$(cat secrets/cue-admin-token.txt)
curl -H "Authorization: Bearer $ADMIN_TOKEN" http://127.0.0.1:19000/admin/status
docker exec agentwire-cue python3 -m agentwire_cue doctor --no-network
```

## 启动

```bash
cd agentwire_cue/
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
