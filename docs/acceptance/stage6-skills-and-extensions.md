# Stage 6 当前实现与离线验收证据

> 状态：Skill、Provider/Model、MCP、Doctor 与当前完整 Backup 已实现

本文只描述当前可达能力，不保留旧 Backup 版本、阶段性计数或已删除评测入口。

## 当前能力

| 领域 | 当前公开入口 | 实现与离线证据 |
|---|---|---|
| Skill Catalog 与生命周期 | `morrow skill list/show/validate/install/enable/disable/pin/rollback/remove` | `tests/test_skill_catalog.py`、`tests/test_skill_lifecycle.py`、`tests/test_skill_bindings.py` |
| Skill Draft 与 Usage | `morrow skill draft-*`、`morrow skill usage` | `tests/test_skill_drafts.py`、`tests/test_skill_usage.py` |
| 受限 Skill Script | AgentRun 冻结选择后的 `run_skill_script` | `tests/test_skill_scripts.py`、`tests/test_skill_selection.py` |
| Provider/Model 控制面 | `morrow provider *`、`morrow model *` | `tests/test_provider.py`、`tests/test_provider_control.py`、`tests/test_cli_commands.py` |
| MCP desired state 与 Catalog | `morrow mcp add/list/show/inspect/status/enable/disable/remove/refresh` | `tests/test_mcp_control.py`、`tests/test_mcp_runtime.py` |
| Doctor 与完整 Backup | `morrow state doctor/backup/verify-backup` | `tests/test_stage6_backup.py`、`tests/test_stage4_doctor.py`、`tests/acceptance/test_stage6_integrated.py` |

## 当前边界

- Skill install/enable/pin/rollback 是显式操作；generated/imported Skill 不会自动启用。
- Skill Script 只执行 AgentRun 已冻结的包和声明输入输出，并受当前平台沙箱、权限、超时与 Artifact 合同约束。
- MCP 当前只支持 stdio transport；没有远程 HTTP、Marketplace、daemon、自动下载或后台更新。
- Provider/Model 只支持显式配置、选择与下一 AgentRun 冻结；没有自动路由、静默 fallback 或成本优化器。
- 当前 Backup 只有一种完整格式，包含 SQLite、Artifact、当前配置/Preference/Profile/扩展 YAML、被引用的 managed Skill 和 MCP 引用；不包含 CredentialStore、Keychain 或凭据字节。
- Backup restore 只接受不存在的新目标，并拒绝 bundle 内部目标、符号链接和不安全父目录。当前 CLI 暴露 create/verify；隔离 restore 由应用服务与综合验收覆盖。
- Live Provider、真实网络 MCP、真实凭据和 Linux 原生沙箱不属于默认离线验收。

## 当前验证入口

```text
uv run pytest -q tests/acceptance/test_stage6_integrated.py
uv run pytest -q tests/test_skill_catalog.py tests/test_skill_lifecycle.py tests/test_skill_drafts.py tests/test_skill_usage.py tests/test_skill_scripts.py
uv run pytest -q tests/test_provider.py tests/test_provider_control.py tests/test_agent_run_preparation.py
uv run pytest -q tests/test_mcp_control.py tests/test_mcp_runtime.py
uv run pytest -q tests/test_stage6_backup.py tests/test_stage4_backup.py tests/test_stage4_doctor.py
uv run pytest -m 'not live'
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
git diff --check
```

本轮正式 CLI 可行性测试的环境、场景、结果和未授权 Live lane 记录在
[`current-chain-feasibility.md`](current-chain-feasibility.md)。
