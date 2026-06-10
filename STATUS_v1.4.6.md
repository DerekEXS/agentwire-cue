# AgentWire-Cue v1.4.6 — Token Leak Closure

> **Released**: 2026-06-07
> **Tag**: `v1.4.6` (commit `3690ebf` post-filter-repo)
> **Owner**: 丝线 (SilkThread)
> **Series**: v1.4.x FROZEN — see [CHANGELOG.md](CHANGELOG.md) header
> **License**: MIT

---

## 🎯 v1.4.6 定位：Token 泄漏 closure + PII cleanup

v1.4.6 不是新功能 release——**纯历史清理 release**。`git filter-repo` 重写 cue 仓 history，移除：
- 主人真 AGENTWIRE_TOKEN (`<REDACTED_AGENTWIRE_TOKEN>`)
- 一处 PII 痕迹 (`<REDACTED_USER_ALIAS>` reviewer 代号 → `用户`)

外加 cue README 双语加 **Deployment** 警告段 (L1 文档)。

---

## 🐛 Token 泄漏详情

**位置**: `STATUS_v1.4.2.md:46-47` (cue 仓 v1.4.2 release 时代)

```yaml
- `auth_token` 变量实际是 `<REDACTED_AGENTWIRE_TOKEN>` (24 + 1 BOM char)
- 跟期望的 `<REDACTED_AGENTWIRE_TOKEN>` 比较失败 → 401
```

**这是主人真 AGENTWIRE_TOKEN**——BOM-1 bug 解释时贴了进去。`STATUS_v1.4.2.md` 在 v1.4.2 commit `749e098` 进入 history，之后所有 clone 的人都本地有备份。

**v1.4.5 / v1.4.6 收口的 leak scan 漏了**——只扫了当时改的少数文件, 没系统扫全仓。

---

## 📊 数字

| 维度 | 数字 |
|------|------|
| **filter-repo runs** | 2 (一次清 token, 一次清用户代号 + token) |
| **Commits rewritten** | 14 (history 全重写) |
| **Files cleaned in history** | 1 (STATUS_v1.4.2.md 含 token) + 1 (STATUS_v1.4.1.md 含 PII) |
| **Token 0 命中** | ✅ working tree + git log all |
| **PII alias 0 命中** | ✅ working tree + git log all |
| **Code changes** | 0 (纯历史清理 + 文档) |
| **Tests** | 243 passed (历史未变) |
| **Backups** | 3 (cue 全量 535KB + .git 237KB + .git-pre2 167KB) |

---

## 📁 交付清单

| 文件 | 改动 |
|------|------|
| `git history` (14 commits) | 重写: token / 用户代号 替换 |
| `README.md` + `README_CN.md` | **Deployment** 段, 解释 18801 默认绑 0.0.0.0 + TLS 终止要求 |
| `CHANGELOG.md` | `v1.4.6` entry (filter-repo 详细说明) |

---

## ⚠️ 强烈建议

**主人**: token 虽已在 cue 仓 history 清干净, 但:

1. **轮换 `~/.openclaw/a2a-token.txt`**——任何已 clone cue 仓 v1.4.5 或之前的协作者本地仍可能有 token 备份
2. **同步更新所有引用**: systemd unit / .env / 启动脚本
3. 旧 token 一旦轮换, 任何历史 clone 里的旧 token 都失效

---

## 🎬 下一 session 起点

读本文件 + [CHANGELOG.md](CHANGELOG.md)。

**v1.5 backlog**: Dockerfile / structlog / CUE_LISTEN_HOST env / 跨 CUE 依赖 / mTLS (core)

---

*冻结: 2026-06-07 丝线 (SilkThread)*
*Tag: v1.4.6 (cue 仓, 14 commits 重写历史)*
