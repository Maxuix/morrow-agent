# S7P-02 Provider-visible Tool Contracts Acceptance

日期：2026-08-27
状态：实现、review、review finding 修复及离线验证完成；等待根会话合并。
基线：`36f3be18d2d9ce813a827a704ea911aa4baa502a`
分支：`codex/fix/s7p-02-tool-contracts`

本记录只覆盖 S7P-02。没有执行真实 Provider、model、Pi、MCP、network 或 credential 测试，
没有改变公开事件生命周期、runtime-policy 默认值、权限/沙箱权威或依赖，也没有触碰三份用户
自有 research 文档。

## 合同闭环

| 合同 | 实现与可执行证据 |
|---|---|
| Provider 与 runtime 共用一个规范化 Schema | `PydanticArgumentsValidator(provider_schema=...)` 先用 `JsonSchemaArgumentsValidator` 校验原始 JSON，再以 `strict=True` 构造 Pydantic；`ToolContractAudit` 比对 validator、`ToolDefinition`、OpenAI-compatible wire 及 digest。 |
| `run_command` 精确命令形状 | `RUN_COMMAND_PROVIDER_SCHEMA` 用 `oneOf` 表达非空 `argv`/非空 `shell` 的 XOR，拒绝空、双字段、null、额外字段，并保留 cwd/timeout 约束；实际 wire 和 runtime 均由测试提交正负 fixture。 |
| `write_file` 分支与预算 | create 禁止 revision，replace 要求非 null SHA-256；content 使用 Provider 保守上限。`apply_patch` 对单个可证明安全的 ASCII 编辑保留较大编辑路径，多编辑使用组合预算上限；测试同时检查未转义与 ASCII escaped JSON 的原始字节预算。 |
| 其他 Direct schema | 静态 inventory 覆盖文件/搜索、mutation、Git、配置、Preference、命令、Skill 和 sandbox promotion；路径规则、query vocabulary、Preference/Skill ID、环境名、NFC 路径及输出路径约束均有可执行 Schema 断言。 |
| fail-closed audit | static Direct contract metadata 独立核对 OperationIntent kind/effect、host/sandbox、ToolExecutionPolicy、recovery declaration、handler/resolver、闭合 Schema、序列化结果及 digest；生产 bootstrap 显式传入 process isolation。未知 MCP 工具保留动态 Schema/runtime 路径，不套用 Direct static map。 |
| 可恢复错误 | `expected` 只允许有限 ASCII shape label；invalid-argument details 仍只有有界 `{path,type}`。脚本 Provider 先发非法 command，再读取 bounded error 并发出合法 command，最终正常停止。 |
| 错误码分离 | 回归矩阵保持 `permission_denied`、`invalid_target`、`not_found`、`search_failed` 与 `execution_failed` 五类结果分别可观察。 |

## Final Provider wire 证据

测试用 OpenAI-compatible SDK stub 捕获实际传入的 kwargs，并逐项核对 `tools` 的最终序列化结果；
没有把 SDK 对象、完整参数或结果写入事件、日志、SQLite、YAML 或终端。普通及
auto-sandboxed Direct inventory 都通过正负 Schema fixture，且静态 audit 数量与定义数量一致。
Provider wire 中只出现公开的 function name/description/parameters；本地测试使用 credential sentinel
做不泄漏断言，sentinel 不进入捕获请求的序列化内容。

## Recovery 与安全边界

`tests/test_agent_run_observability.py::test_scripted_agent_repairs_command_shape_without_durable_raw_sentinel`
验证第一次非法调用返回 `invalid_arguments` 与 `exactly_one_of:argv,shell`，下一次调用成功；原始
命令 sentinel 不进入 tool message、事件 payload 或 durable execution projection。Schema 失败仍由
现有 `ConversationLog`/S7P-01 安全观测边界处理，未增加凭据、reasoning、完整工具参数/结果、SDK
对象或 traceback。

状态相关事实仍由 typed preflight/handler 负责：文件存在性、symlink/containment、Git 状态、
sandbox selection、审批和权限没有被伪装成 Provider Schema，也没有削弱这些权威。

## Reviewer 闭环

同一实施会话启动的只读 reviewer：

- agent id：`01a03eae-b43b-7d51-9a81-6b4bfbd15327`（Laplace）
- formal report：`codex-s7p02-ro-2026-08-26`
- spawn model/reasoning：`gpt-5.6-luna` / `max`
- review 范围：完整 `36f3be18d2d9ce813a827a704ea911aa4baa502a...8ab32ad` diff；未修改文件，未运行 live 或网络测试
- 结论：`REQUEST CHANGES`，F1–F6 全部逐条本地复现并修复；报告无额外未确认 finding。报告自报 metadata 使用了 “Codex (GPT-5)” 标签，但启动配置按要求为 `gpt-5.6-luna` / `max`。

修复摘要：F1 补齐路径、write 分支、NUL、重复项、空 glob；F2 补齐 Unicode/escaped raw budget、
整数上界及组合数组预算；F3 收紧 Skill 环境名、ID、NFC 路径和输出路径 Provider 子集；F4 收紧
配置/Preference 空白、控制字符和 ID/evidence 长度；F5 加入独立 static contract expectations、
recovery/process-isolation/policy audit 与 executor preflight 检查；F6 将预算测试改为确实低于 raw
budget 的 schema-bound proof。完整门禁还暴露了一个大单编辑及匿名测试 `run_command` 的兼容性回归，
已在 `e1b3b1d` 修复并重新验证。

## Commits

- `8ab32ad` `fix(tools): align provider schemas with runtime contracts`
- `667fcdd` `fix(tools): close provider contract review gaps`
- `fc1f04c` `fix(preferences): align provider control constraints`
- `e1b3b1d` `fix(tools): preserve bounded patch compatibility`

## 精确验证记录

命令使用当前 worktree 的 `/Users/ruirui/Documents/Project/Agent/developing/.venv/bin/python`，并以
`PYTHONPATH=src` 确保导入当前 topic worktree。

| 检查 | 真实结果 |
|---|---|
| `pytest -q tests/test_tool_contract_audit.py tests/test_local_tool_factories.py` | 33 passed in 1.07s |
| `pytest -q tests/test_tools.py tests/test_process.py tests/test_provider.py` | 88 passed, 1 skipped in 2.29s；skip 为显式 live checklist `tests/test_provider.py:572` |
| `pytest -q tests/test_agent_run_observability.py tests/test_dynamic_tool_contracts.py` | 36 passed in 1.37s |
| 扩展受影响套件（含 local mutation、Stage 4 permissions/tool persistence） | 259 passed, 1 skipped in 6.88s |
| `pytest -m 'not live'` | 1166 passed, 2 skipped, 2 deselected in 51.25s；2 个 skip 是嵌套 sandbox 中不能运行的真实 Seatbelt host-level 测试 |
| `ruff format --check .` | 459 files already formatted |
| `ruff check .` | All checks passed |
| `python -m compileall -q src tests` | passed |
| `python -m morrow --help` | passed |
| `python -m morrow run --help` | passed |
| 当前 worktree import-path proof | passed；解析到 `src/morrow/runtime/tools.py` |
| `git diff --check` | passed；无输出 |

以上测试均未运行 live Provider/model/Pi/MCP/network/credential 场景。`uv sync` 未执行，以避免修改
依赖或触发网络；本次没有添加依赖。
