# Stage 3 本地 Code Agent 验收证据矩阵

> 验收日期：2026-08-19
> 当前声明平台：macOS 26.6.1 / Darwin 25.6.0 / arm64
> 状态：通过（macOS）；外部实现 review 的 12 项结论及真实 Mimo 用户报告问题已逐条复核并修复；Linux 尚未声明运行支持，Windows 不是首个 Auto Sandboxed 目标

本文记录 `.agent/PLAN.md` Definition of Done 的逐条证据。所有结论只覆盖本次执行实际
运行过的命令、测试和当前代码，不把缺少 runner、凭据或网络的项目标记为通过。

## 1. 当前身份、范围与固定参考

| 项目 | 实际事实 |
|---|---|
| Git 分支 | `main` |
| Git HEAD | `099d804f8b59359e1672e4d034523593447b79cf` |
| 工作树 | 本次验收覆盖 Stage 3 实现、回归测试、Mimo 持久化包装和文档；提交前完成最终复核，未执行 push |
| 工作区根 | `/Users/ruirui/Documents/Project/Agent/developing` |
| 主参考 | Pi `@earendil-works/pi-coding-agent 0.84.2`，固定提交 [`209bc7b9a89b01c8fd05861cf5bbdda3e300037a`](https://github.com/earendil-works/pi/commit/209bc7b9a89b01c8fd05861cf5bbdda3e300037a) |
| 许可证处理 | 只借鉴行为/边界；没有复制 Pi 源码，因此没有新增 Pi 代码版权文件 |
| macOS 后端 | `/usr/bin/sandbox-exec` Seatbelt 类原生后端；Gate P0 与宿主级逃逸测试通过 |
| 快照文件系统 | APFS，`clonefile` CoW 探针通过；本次 Gate P0 记录的写入卷为 `/dev/disk3s5` |
| Linux | bubblewrap 规则构造有测试；没有真实 Linux runner，因此保持 `unsupported`，不是 pass |
| Windows | 不在首个 Stage 3 Auto Sandboxed 平台声明内 |
| Live/真实 Provider | 已在宿主环境使用持久化 OpenCode Go Keychain 配置验证 `provider show` 与 `provider test opencode-go`；默认自动化门禁仍为离线 Fake Provider；package build 仅为解析已声明的 hatchling 构建依赖访问过 PyPI |

## 2. 最终生产组合

普通 function-tool-capable Adapter 的精确工具集合为：

```text
update_configuration
list_directory
read_file
find_files
search_text
apply_patch
write_file
show_changes
run_command
git_status
git_diff
```

当且仅当当前 macOS native sandbox 后端探测通过且选择 `auto-sandboxed` 时，额外注册：

```text
promote_sandbox_changes
```

`lookup_record` 与 `calculate` 仅保留在显式测试 fixture；无 function calling 的 Adapter 不注册
本地工具。Provider schema 使用严格 Pydantic 参数模型，`additionalProperties` 为 `false`，
不包含 `PermissionProfile`、审批、沙箱或其他本地策略字段。

## 3. Definition of Done 逐条矩阵

| # | 主计划要求 | 证据 | 结果 |
|---:|---|---|---|
| 1 | 精确生产工具集合，demo 工具只在 fixture | `tests/test_local_tool_factories.py::test_production_inventory_is_exact_and_demo_tools_are_not_exposed`；`::test_supported_auto_sandbox_inventory_adds_only_current_run_promotion` | 通过 |
| 2 | 系统边界来自冻结 ToolSet；不支持 Adapter 保持无工具 | `tests/test_capability_prompt_and_preset.py`；`tests/test_stage_boundary.py`；全量离线测试 | 通过 |
| 3 | 权限维度冻结且不进入 Provider；配置不能提权 | `tests/test_capabilities.py`；`tests/test_capability_prompt_and_preset.py`；`tests/test_configuration_tool.py` | 通过 |
| 4 | intent 驱动的 allow/approval/deny，无静态生产分支 | `tests/test_capability_policy.py`；`tests/test_capability_executor.py` | 通过 |
| 5 | Manual/Auto Safe 不自动运行 Host 项目代码 | `tests/test_capability_policy.py::test_auto_safe_allows_structured_workspace_write_but_still_approves_host_process`；`tests/test_process.py` | 通过 |
| 6 | 路径、特殊文件、受保护内容不可通过文件/搜索工具泄露 | `tests/test_local_files.py`、`tests/test_local_search.py`、`tests/test_local_mutation.py`、`tests/test_stage_boundary.py` | 通过 |
| 7 | UTF-8、revision、400 行/8 KiB 基线和 continuation | `tests/test_local_files.py`；`tests/test_local_tool_factories.py::test_production_read_tools_use_semantic_result_and_continuation` | 通过 |
| 8 | rg 固定 argv/ignore 与 Python fallback 预算 | `tests/test_local_search.py`；`docs/decisions/stage-3-search-adapter.md` | 通过 |
| 9 | Patch/write 阈值、陈旧 SHA、原子发布、实际 Diff/ChangeSet | `tests/test_local_mutation.py`；`tests/test_stage3_product_acceptance.py` | 通过 |
| 10 | Mutation/promotion 审批展示有界实际 Diff | `tests/test_local_mutation.py`；`tests/test_sandbox.py`；`tests/test_terminal.py::test_terminal_approval_renders_only_sanitized_preview_and_accepts_yes` | 通过 |
| 11 | delete/rename/chmod/link 与 Git 写入不在 Provider surface | `tests/test_local_tool_factories.py`；`tests/test_stage_boundary.py` 精确 inventory 与 forbidden families | 通过 |
| 12 | Host command 结构化结果、输出边界、超时/取消、无残留子进程 | `tests/test_process.py`；`tests/test_stage2_e2e.py`；`docs/decisions/stage-3-process.md` | 通过 |
| 13 | Auto Sandboxed 使用 CoW 临时快照，禁止 Host fallback/Home/network/真实工作区写入 | Gate P0 记录；宿主级 `tests/test_sandbox.py` 两项测试；嵌套环境安全测试 | 通过（macOS） |
| 14 | 仅当前运行、始终需审批的 subset promotion 可进入真实工作区 | `tests/test_sandbox.py` promotion success/conflict/expiry/cross-run tests；`src/morrow/services/sandbox.py` | 通过 |
| 15 | Git 只读、有界、禁 hooks/textconv/fsmonitor/prompt，拒绝外部 metadata | `tests/test_git.py` 五项；`src/morrow/adapters/local/git.py` 与 `src/morrow/services/git.py` hardening scan | 通过 |
| 16 | credential/private-key/process secret 不进入 Provider、事件、终端、日志、state、Diff、sandbox | `tests/test_local_files.py`、`tests/test_local_search.py`、`tests/test_process.py`、`tests/test_git.py`、`tests/test_stage2_product_acceptance.py` secret-safe flow | 通过 |
| 17 | Host 非隔离边界在文档与审批预览中明确；不把 classifier 当 confinement | `README.md`、`docs/ARCHITECTURE.md`、`docs/roadmap/stage-3-local-tools-and-safety.md`；Host process tests | 通过 |
| 18 | 严格 ToolFacts、terminal summary、可关闭 JSON-safe local metrics，不改变公开事件/持久化 | `tests/test_capabilities.py::test_run_metrics_are_local_json_safe_and_composition_disableable`；`tests/test_stage2_product_acceptance.py::test_real_terminal_product_flow_is_ordered_recoverable_and_secret_safe`；`src/morrow/interfaces/terminal.py` | 通过 |
| 19 | AgentLoop/ToolExecutor/Orchestrator/Provider 与 ConversationLog ownership 不漂移 | `tests/test_agent_guardrails.py`、`tests/test_agent_tool_loop.py`、`tests/test_stage2_e2e.py`、`tests/test_terminal.py`；全量 suite | 通过 |
| 20 | 两种 Fixture 完成定位—修改—验证—报告，并经真实终端组合验证 | `tests/test_stage3_product_acceptance.py` 两项：Python 修复闭环、嵌套文本/预存用户改动闭环；`tests/test_stage2_product_acceptance.py` 真实 REPL | 通过 |
| 21 | 离线、质量、package、文档、安全、已声明平台 sandbox gates | 本文第 5 节的命令与观察结果；宿主级 2/2；wheel smoke gate | 通过（macOS；Linux unsupported） |
| 22 | Full Access 与 crash-durable intent/fact persistence 留给 Stage 4 | `README.md`、`docs/ARCHITECTURE.md`、Stage 3 roadmap；Session/ToolRunContext 仅进程内事实测试 | 通过，Stage 4 未开放 |

## 4. 产品故事与事实边界

### Python fixture

`tests/test_stage3_product_acceptance.py::test_fake_provider_python_locate_patch_fail_correct_validate_and_report`
建立临时 Git 仓库，依次执行搜索、读取、错误 patch、失败校验、正确 patch、成功校验、Git status、
Git diff、show changes，再输出最终回答。断言结果为：源码最终从 `return 1` 变为 `return 2`，命令
退出码为 `[1, 0]`，实际 Diff 含 `+    return 2`，审批请求 4 次，最新 metrics 为 9 次工具调用、
1 个变更文件；最终回答明确说明第一次失败后已修正并通过。

该运行的 `validation_outcome` 为 `failed`，因为本地 metrics 保留运行中出现过失败校验的事实；这不掩盖
随后成功校验，最终回答同时由两个 `CommandToolFact` 支撑。

### 嵌套文本 fixture

`tests/test_stage3_product_acceptance.py::test_fake_provider_nested_text_fixture_preserves_user_change_and_reports_unrun_validation`
在 `docs/guide/readme.md` 下完成 list/read/exact patch/show changes/Git status/Git diff，保留
预先存在的 `notes.txt` 用户改动，并断言最终回答写明“未运行项目校验”。这验证了 Morrow ChangeSet
与 Git 工作树预存改动的分离，以及未运行不能被表述为通过。

### 真实终端与恢复

`tests/test_stage2_product_acceptance.py` 通过真实 `run_repl`、共享 Terminal/PromptSession/ApprovalPort
和 Scripted Provider 验证：工具错误后恢复、后续对话继续、未知命令不改变状态、配置审批使用同一终端、
脏会话退出需要确认。工具轮次完成后实际输出一行有界 `事实摘要：工具 1 次...`；摘要不包含原始
参数、Diff 或 secret sentinel。

### 外部实现 review 复核与修复

2026-08-18 对外部 review 报告中的 9 个 bug、2 个 suggestion 和 1 个 nit 逐条按当前代码复现；
2026-08-19 对真实 Mimo 用户报告中的 Keychain、等待反馈、预设发现和持久化包装路径再次验收。
12 项均确认存在或具有明确的安全/可维护性价值，并完成以下修复；没有把未实现路线项当作缺陷：

| # | 复核结论与修复 | 当前证据 |
|---:|---|---|
| 1 | 受保护路径同时检查调用别名和工作区内解析后的符号链接目标；find/search 同步使用该规则 | `tests/test_local_files.py::test_protected_symlink_target_and_git_metadata_are_metadata_only`；`tests/test_local_search.py::test_search_blocks_protected_symlink_targets_and_explicit_git_root` |
| 2 | 私钥内容标记覆盖 PKCS#8、RSA、EC、DSA、encrypted PEM，并保留 OpenSSH/PGP 检测 | `tests/test_local_search.py::test_sensitive_policy_is_frozen_and_local_only`；`tests/test_git.py::test_git_status_and_diff_are_bounded_read_only_and_protect_content` |
| 3 | `.git` 与 `.morrow` 成为受保护路径组件；文件、目录、搜索和 mutation 均 fail closed，Git 只读检查仍走专用服务 | `tests/test_local_files.py`、`tests/test_local_search.py`、`tests/test_local_mutation.py` |
| 4 | `ReadFileResult` 允许合法空窗口 `end_line == start_line - 1`，覆盖 EOF 续读和预算裁空中间窗口 | `tests/test_local_files.py::test_read_file_reports_revision_newline_and_actionable_continuation`；`::test_read_file_allows_an_empty_mid_file_window_when_result_budget_trims_every_line` |
| 5 | Host preflight 检测 `sh -c`、直接 shell 与命令替换中的 Git 命令并标记 `GIT_WRITE` | `tests/test_process.py::test_process_preflight_classifies_forbidden_operations_before_approval` |
| 6 | 沙箱推广后的每个 `MutationResult` 写入同一 `ChangeSetService`，`show_changes` 可见推广结果 | `tests/test_sandbox.py::test_sandbox_text_change_requires_approval_and_promotes_safely` |
| 7 | `not_found`、`git_failed` 和新增的 `unsupported_newline` 映射为稳定工具错误码 | `tests/test_local_tool_factories.py::test_local_error_mapping_preserves_recoverable_not_found_and_git_failures` |
| 8 | 混合换行文件的 patch/replace 明确返回 `unsupported_newline`，原始字节保持不变，不再静默全文件 LF 化 | `tests/test_local_mutation.py::test_mixed_newline_files_are_rejected_without_rewriting_bytes` |
| 9 | 快照 prepare/collect 在启动线程前预留临时根，使用协作式取消并等待线程停稳后清理，超时不遗留项目副本 | `tests/test_sandbox.py::test_snapshot_phase_timeout_cancels_worker_and_removes_reserved_root` 两个参数化分支 |
| 10 | Host 审批预览增加有界、单行、脱敏的 argv/shell 命令文本，仍不进入 `CommandResult`、事件或持久状态 | `tests/test_process.py::test_tool_executor_requires_approval_for_host_process_and_denies_network` |
| 11 | Linux bubblewrap 只保留规则构造测试，真实 runner 通过前 `probe()` 固定 `supported=false`；规则同时隔离 PID namespace | `tests/test_sandbox.py::test_platform_backend_builders_are_fixed_and_fail_closed` |
| 12 | Auto Safe schema 说明收敛为一行非显然约束，不保留冗长设计历史注释 | `src/morrow/application/local_tools.py` |

修复相关测试切片最终为 `65 passed, 2 skipped`；两个 skip 仍是必须在宿主级单独运行的真实
macOS Seatbelt 测试。完整门禁和宿主级结果见下一节。

## 5. 最终命令与实际结果

### 离线与质量门禁

```text
UV_CACHE_DIR=/private/tmp/morrow-uv-cache uv run pytest -m 'not live'
418 passed, 2 skipped, 1 deselected in 5.31s

完整运行收集 421 项，其中 420 项选择执行；两个 skip 是嵌套 Codex 环境不能执行真实宿主
Seatbelt 的保护性 skip。

UV_CACHE_DIR=/private/tmp/morrow-uv-cache uv run ruff format --check .
101 files already formatted

UV_CACHE_DIR=/private/tmp/morrow-uv-cache uv run ruff check .
All checks passed!

UV_CACHE_DIR=/private/tmp/morrow-uv-cache uv run python -m compileall -q src tests
exit 0

UV_CACHE_DIR=/private/tmp/morrow-uv-cache uv run morrow --help
exit 0; CLI usage and provider/model/workspace commands rendered

git diff --check
exit 0

Markdown local link audit (README, ARCHITECTURE, ROADMAP, Stage 3 roadmap, evidence matrix)
checked=17 missing=0
```

两个 skip 是嵌套 Codex Seatbelt 环境不能执行真实宿主 Seatbelt 的保护性 skip，不是平台通过证据。

### 宿主级 macOS 安全验收

```text
UV_CACHE_DIR=/private/tmp/morrow-uv-cache uv run pytest -q -s \
  tests/test_sandbox.py::test_macos_native_sandbox_blocks_real_workspace_home_and_network \
  tests/test_sandbox.py::test_production_auto_sandbox_registers_only_native_tools_and_keeps_real_workspace_clean
..
2 passed in 0.23s
```

Gate P0 另已证明 `/usr/bin/sandbox-exec`、APFS `clonefile`、最小环境、工作区/Home/.ssh/loopback/外网
阻断和真实工作区保护；该探针没有修改仓库或持久 Host 状态。

### Git hardening 与安全扫描

固定 Adapter 使用 `GIT_CONFIG_NOSYSTEM=1`、global/system config 指向 `/dev/null`、
`GIT_OPTIONAL_LOCKS=0`、`GIT_TERMINAL_PROMPT=0`、`GIT_PAGER=cat`；Service 固定
`--no-ext-diff`、`--no-textconv`、无 color，并覆盖 `core.fsmonitor=false`、空
`credential.helper`、空 `diff.external`。`tests/test_git.py` 还实测 external diff 脚本没有执行、
输出截断有界、timeout typed、非仓库 typed result、外部 Git metadata 拒绝和 `.env` hunk 内容隐藏。

### Package gate

```text
uv build --wheel
dist/morrow_agent-0.1.0-py3-none-any.whl

SHA256
926f1655b495b2c26d2505169f66a51d2c24c1a01937158c751f53e1eb19108a

unzip -l ...whl
61 files; includes morrow/adapters/local/git.py, sandbox.py,
morrow/services/git.py, sandbox.py, and bundled agent-policy.toml
```

在全新 `/private/tmp/morrow-stage3-package-smoke.YLz9pF` 中执行 `uv pip install --no-deps` 成功；
以已验证的离线 `.venv` 运行时依赖作为 `PYTHONPATH` 做 smoke check，安装包路径确认来自临时 venv：

```text
import morrow -> /private/tmp/morrow-stage3-package-smoke.YLz9pF/lib/python3.13/site-packages/morrow/__init__.py
importlib.metadata.version("morrow-agent") -> 0.1.0
bundled policy first line -> max_tool_rounds = 30
installed policy: .git/config protected -> True
installed policy: RSA PRIVATE KEY content protected -> True
installed /private/tmp/.../bin/morrow --help -> exit 0
```

首次隔离构建因沙箱 DNS 受限无法解析 PyPI；经明确批准后，`uv build --wheel` 仅为解析项目已声明的
`hatchling>=1.25,<2` 构建依赖访问了 PyPI 并成功。随后没有把运行时依赖 wheel 下载到临时环境，
`uv pip install --no-deps` 与 smoke 没有再进行网络安装；因此这里是“新环境安装项目 wheel +
已验证离线依赖的 import/CLI smoke”，不是把第三方依赖声明为随 wheel 自包含。

## 6. 未运行与后续边界

- 除 OpenCode Go Mimo v2.5 的已授权 `provider show`、`provider test` 和此前记录的真实用户场景外，
  其他 Live Provider、产品运行时网络和外部服务：`not run`，不是 pass；package build 的 PyPI
  构建依赖解析不构成产品网络能力验收。
- Mimo 最终复核结果：`provider presets`、`model current`、Keychain 读取、连接测试均通过；CLI
  连接等待提示、首个 token 超时分类和 Keychain 异常脱敏由回归测试覆盖。
- Linux native sandbox：`unsupported`，因为没有真实 Linux runner；只接受规则构造测试结果。
- Windows Auto Sandboxed：非首发声明平台。
- Host 命令仍是用户审批后的非隔离进程；路径检查和 classifier 不能替代操作系统隔离。
- Full Access、持久化 Session/Task/Artifact、可撤销 CapabilityGrant、crash-durable intent/fact、
  LLM summary、Skills/MCP、浏览器、后台任务、多 Agent、网络、loopback、Git 写入、delete/rename/chmod/link
  仍未开放。

Stage 3 的明确例外是：`ToolRunContext`、ChangeSet、ToolFacts 和 `RunMetricsSnapshot` 只在当前进程内、
有界地保留最近一次运行；它们不写入 ConversationLog 之外的持久任务记录，不上传 Provider，也不提供
崩溃后恢复。crash-durable intent/fact、Artifact 和 CapabilityGrant 由 Stage 4 的 AgentRun/持久化设计
重新授权和验收。
