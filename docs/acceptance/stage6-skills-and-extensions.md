# Stage 6 离线综合验收

本记录对应 Subplans 63–75 的本地验收。所有场景使用 pytest `tmp_path` 隔离数据根、内存凭据存储、固定时钟/ID 和脚本化 Provider；
不会读取或修改用户的 `~/.morrow`、真实 CredentialStore、项目 Skill 或外部 MCP 配置。MCP 场景使用仓库内的
`tests/spikes/fake_mcp_stdio_server.py`，Provider 场景使用 `tests/fixtures/stage6/fake-provider.py` 的无 IO 形状。

## 综合场景

`tests/acceptance/test_stage6_integrated.py` 覆盖：

- 手写 `SKILL.md` 的 validate/install/enable/select/resource/disable、workspace 隔离、Usage 和 Doctor；
- 版本化安装、pin/rollback、历史保留、Backup v2 验证和新目标隔离 restore；
- 已接受 SkillCandidate → Draft → edit/revalidate → accept → 显式 enable，且接受前 Binding 保持不变。

其余脚本、Provider/Model、MCP 和故障矩阵沿用各领域的离线合同测试，避免在集成夹具中重复创建第二套权威状态。

## 最终命令证据

以下命令均在仓库根目录执行，使用本地 uv 缓存且显式 offline：

| 命令 | 结果 |
|---|---|
| `UV_CACHE_DIR=/private/tmp/morrow-uv-cache uv run --offline pytest -q tests/acceptance/test_stage6_integrated.py` | `2 passed in 1.08s` |
| `UV_CACHE_DIR=/private/tmp/morrow-uv-cache uv run --offline pytest -q tests/test_skill_catalog.py tests/test_skill_lifecycle.py tests/test_skill_drafts.py tests/test_skill_usage.py tests/test_skill_scripts.py` | `41 passed in 3.56s` |
| `UV_CACHE_DIR=/private/tmp/morrow-uv-cache uv run --offline pytest -q tests/test_provider.py tests/test_provider_control.py tests/test_agent_run_preparation.py` | `66 passed, 1 skipped in 1.95s`；唯一跳过项是显式 Live Provider checklist，因未提供凭据 |
| `UV_CACHE_DIR=/private/tmp/morrow-uv-cache uv run --offline pytest -q tests/test_mcp_control.py tests/test_mcp_runtime.py` | `14 passed in 3.19s` |
| `UV_CACHE_DIR=/private/tmp/morrow-uv-cache uv run --offline pytest -q tests/test_stage6_backup.py tests/test_stage4_backup.py tests/test_stage4_doctor.py tests/test_stage5_doctor_backup.py tests/test_preference_doctor_backup.py tests/test_stage4_cli_operational.py` | `30 passed in 3.15s` |
| `UV_CACHE_DIR=/private/tmp/morrow-uv-cache uv run --offline pytest -m 'not live'` | `1060 passed, 2 skipped, 2 deselected in 38.78s` |
| `UV_CACHE_DIR=/private/tmp/morrow-uv-cache uv run --offline ruff format --check .` | `428 files already formatted` |
| `UV_CACHE_DIR=/private/tmp/morrow-uv-cache uv run --offline ruff check .` | `All checks passed!` |
| `UV_CACHE_DIR=/private/tmp/morrow-uv-cache uv run --offline python -m compileall -q src tests` | 通过 |
| `git diff --check` | 通过 |

CLI help 也已通过：`morrow --help`、`morrow skill --help`、`morrow mcp --help`、`morrow provider --help`、
`morrow model --help`。`morrow state backup --version 2` 通过 CLI 回归测试生成 manifest v2；`state verify-backup` 自动识别 v1/v2。

## 边界与结果

- Backup v1 仍可解码和验证；Backup v2 覆盖在线 SQLite、Artifact、脱敏 YAML、被引用的 imported/generated Skill 版本和 MCP 引用。
- Backup v2 restore 只接受不存在的新目标，并拒绝 bundle 内部目标、符号链接和不安全父目录；CredentialStore/Keychain 永不进入 bundle。
- Generated Skill 的受控审批引用会留在托管版本信封的有界证据字段中；无审批的 generated 包仍保持 `unknown` Trust，不能借包内声明提升权限。
- MCP Server 崩溃、超时、Schema/结果异常和调用失败不自动重试，也不污染主状态；普通 ToolExecutor、审批、Artifact 和恢复分类继续是唯一执行边界。
- Live Provider/MCP 网络验证、真实凭据和 Linux 原生 Seatbelt 未在本验收中运行；这不是离线通过的替代结论。

## 与最终方案的实现边界

- MCP 当前只交付 stdio transport；没有引入远程 HTTP、Marketplace、daemon、自动下载或后台自动更新。
- Provider/Model 只支持显式控制、能力快照和下一 AgentRun 生效；没有自动路由、静默 fallback 或成本优化器。
- generated/imported Skill 不会自动启用，Skill 脚本不获得独立于 CapabilityPolicy 的权限；多 Agent Workflow、GUI 和后台任务留在 Stage 7–10。
- Backup v2 使用已批准的官方 MCP/JSON Schema 依赖，但 CredentialStore、Keychain、真实网络和 Live 质量评估仍不属于 Stage 6 离线验收。
