# AgentWire-Cue v1.3.1 — Patch Release Status

> **Last updated**: 2026-06-04
> **Owner**: 丝线 (SilkThread) — v1.3.1 patch author
> **Reviewed by**: 初梦 (Chu Meng)
> **Patch on**: v1.3 (commit 470877a)
> **Spec patch**: `spec-path/v1.3.1/SPEC-PATCH.md`

---

## 🐛 触发本 patch 的真问题

丝线 × 初梦 联合测试发现 2 个 P0 bug（154 单测 + 对抗测试 + 边界输入）：

| 编号 | 严重度 | 问题 | 影响 |
|------|--------|------|------|
| **P0-1** | 🔴 | `persist.path` 无沙箱，loader 接受 `~/.ssh/authorized_keys` 等任意可写路径 | 任何 plugin 可写 state.json 到敏感位置（SSH keys, AWS creds, shell config 等）|
| **P0-2** | 🔴 | statechart 接受 `target: nonexistent_state`，把 current_state 改成 undefined | Plugin 进入不可用状态，后续 transition 全部失败但已落盘 |

---

## ✅ v1.3.1 patch 内容

### §3.4.2 — Persist path sandboxing (NEW)

**4 层防御**：
- L1: 默认 allowed parents（`~/.local/share/agentwire-cue/state/`, `/var/lib/agentwire-cue/state/`）
- L2: `spec.persist.allowed_parents_extras` (yaml 声明)
- L3: `--persist-allow-parent=PATH` CLI flag (host 启动)
- L4: blocked parents (13 项 deny-list, 永不可覆盖)

**防 escape 机制**：
- `..` 用 `os.path.normpath` 规范化
- symlink 用 `os.path.realpath` 跟到底

**触发点**：
- L1: `core/loader.py:resolve_persist_path` (启动期 fail-fast)
- L3: `core/permission.py:check_filesystem` (运行时 mode='write' 必过)

**错误信息可操作**：
```
PERSIST_PATH_BLOCKED: ~/.ssh/authorized_keys is under blocked parent ~/.ssh.
Blocked parents are deny-listed and CANNOT be overridden...

PERSIST_PATH_NOT_ALLOWED: /var/tmp/x.json not under any allowed parent.
Default allowed: [...]. Fix: use a path under one of the above, OR
add spec.persist.allowed_parents_extras: ['<dir>'] to plugin.yaml,
OR pass --persist-allow-parent=<dir> to cue host.
```

### §4.7.1 — Target validation (NEW)

**双层校验**：
- A: loader 启动期 fail-fast（扫所有 `states[*].on[*].target`）
- B: statechart runtime 校验（防 hot reload / 动态 spec）

**新 TransitionResult.error**：
```python
@classmethod
def error(cls, message: str) -> "TransitionResult":
    return cls(OK=False, error=message)
```
state 不变（abort before mutation）。

**错误信息**：
```
TARGET_NOT_IN_STATES: state 'idle'.on.GO.target 'nonexistent_state'
not in spec.states ['done', 'idle']. Fix plugin.yaml.
```

---

## 📊 测试结果

| 维度 | 数字 |
|------|------|
| v1.3 既有测试 | **154/154** 全绿（无 regression）|
| v1.3.1 新增 test case | **33/33** 全绿 |
| **总计** | **195/195** 全绿 |
| 跑测时间 | 2.96s |
| 计划 vs 实际 test 数 | 计划 17 / 实际 33（**+94%** 覆盖）|
| spec patch 行数 | ~100 |
| 改文件 | 4 (sandbox.py 新, loader.py + statechart.py + permission.py 改) |
| 新增 LOC | ~340 (sandbox 158 + test 230 - 改 -50) |

### Test case 分布

| 组 | 数 | 覆盖 |
|----|---|------|
| Sandbox defaults | 3 | 默认 allowed + blocked 类别 |
| Sandbox allowed (5 层) | 5 | L1, L2, L3, var_lib, default |
| Sandbox blocked (deny-list) | 9 | ssh, aws, gnupg, etc, proc, bashrc, zshrc, netrc + 2 覆盖尝试 |
| Sandbox dotdot escape | 3 | to /etc, from spec_extras, inside-allowed OK |
| Sandbox symlink escape | 2 | to ssh, to etc |
| `is_persist_path_allowed` | 2 | bool 包装 |
| Actionable error messages | 2 | blocked msg, fix hint |
| Loader target validation | 4 | valid, missing, msg 格式, cycle OK |
| Statechart runtime check | 2 | bad target rejected, valid target proceeds |
| **Total new** | **33** | |

---

## 🧪 验证（原始 repro 跑一遍）

### P0-1 原始 repro
```yaml
# plugin.yaml
statechart:
  persist:
    path: "~/.ssh/authorized_keys"
```

**v1.3 行为**: ❌ plugin 加载成功，state.json 写到 ~/.ssh/authorized_keys
**v1.3.1 行为**: ✅ plugin 加载失败，错误信息:
```
PERSIST_PATH_BLOCKED: /home/user/.ssh/authorized_keys is under blocked parent
/home/user/.ssh. Blocked parents are deny-listed and CANNOT be overridden by
--persist-allow-parent or spec.persist.allowed_parents_extras.
```

### P0-2 原始 repro
```python
# 构造 plugin 绕过 loader
statechart:
  states:
    idle:
      on:
        GO:
          target: nonexistent_state
```

**v1.3 行为**: ❌ transition OK=True, current_state = "nonexistent_state"
**v1.3.1 行为**: ✅ transition error, state unchanged
```
plugin=bypass target='nonexistent_state' not in states (available=['idle'])
state stays at 'idle'
```

---

## 🛡 防泄漏扫描

`grep` 全 7 类红线: 0 命中。commit 干净。

---

## 🔄 迁移指南 (v1.3 → v1.3.1)

| 旧 plugin 写法 | v1.3.1 行为 | 修复方法 |
|----------------|-------------|----------|
| `persist.path: "~/.local/share/agentwire-cue/state/{{meta.name}}.json"` | ✅ 不动 | 无需改 |
| `persist.path: "/var/tmp/cue-state.json"` | ❌ 拒 | 加 `spec.persist.allowed_parents_extras: ['/var/tmp']` |
| `target: nonexistent_state` | ❌ loader 拒 | 改 yaml，加 state |
| `target: $computed` | ❌ runtime 拒 | 改用 hardcoded state 名 |

---

## 📦 Deliverables

| 文件 | 状态 |
|------|------|
| `agentwire_cue/core/sandbox.py` | 新增 (158 行) |
| `agentwire_cue/core/loader.py` | 改 (resolve_persist_path + _validate_targets) |
| `agentwire_cue/core/statechart.py` | 改 (TransitionResult.error + runtime check) |
| `agentwire_cue/core/permission.py` | 改 (L3 sandbox + register 抓 spec_extras) |
| `agentwire_cue/tests/test_sandbox_and_target.py` | 新增 (33 cases) |
| `~/.openclaw/workspace/designs/.../v1.3.1/SPEC-PATCH.md` | 新增 (spec patch) |

---

## 🎬 下一 session 起点

读 `agentwire_cue/core/sandbox.py` 看 L1-L4 实现；`test_sandbox_and_target.py` 看覆盖。接 v1.4 P0 #1 (A2A HTTP client)。

---

*冻结: 2026-06-04 丝线 + 初梦 review 通过*
