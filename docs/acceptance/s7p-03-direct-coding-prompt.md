# S7P-03 Direct Coding Prompt Acceptance

日期：2026-08-27  
状态：实现、review、review finding 修复及离线验证完成；等待根会话合并。  
基线：`30368876457ac740ae36565929190c4cc855e83c`  
分支：`codex/feat/s7p-03-direct-coding-prompt`

本记录只覆盖 S7P-03。没有运行真实 Provider、model、Pi、MCP、network 或 credential 测试，
没有改变公开事件生命周期、runtime-policy 默认值、ToolSet、权限/沙箱权威或依赖，也没有触碰
用户自有 research 文档。项目指令正文和任务正文只存在于一次运行的内存 projection，不在本记录
或 durable evidence 中展开。

## 合同闭环

| 合同 | 实现与可执行证据 |
|---|---|
| 可复用 Direct Coding profile | `DirectCodingProfile` 固定 `direct-coding`/`v1`/canonical digest；`DirectCodingPromptAssembler` 提供 fresh、rehydrate、verify 和 system-message assembly seam。固定安全/tool-truth boundary 永远是第一条，coding protocol 紧随其后。 |
| 工作代码协议 | 固定协议覆盖勘察、最小修改、保护用户改动、风险相称验证、不能把工具成功当完成、具体 blocker，以及禁止非请求计划/报告/临时脚本；role/project/Skill/Memory/Preference/user 均被标为低权限不可信指导。 |
| AGENTS.md scope | 生产默认只启用 `AGENTS.md`；兼容名称必须显式配置。根指令自动进入上下文；明确目标按根到目标目录选择，深层只影响其 scope，兄弟目录隔离，顺序和 source metadata digest 确定。 |
| 只读、bounded、fail closed resolver | resolver 使用 confirmed workspace、`O_NOFOLLOW`/directory fd 和 read-only fd；限制目标、深度、来源数、单文件和总字节。invalid UTF-8、NUL/control、non-NFC、越界、symlink、非普通文件、oversize、目标 iterable/路径过长、TOCTOU identity race 和 frozen hash drift 均拒绝。诊断仅有稳定 code 和 bounded workspace-relative location。 |
| durable evidence | `AgentRunSnapshot` 与 `PreparedAgentRunSpec` 只保存 profile ID/version/digest、role digest、resolver version、有序 source ID/version/path/scope/content hash/byte count/source digest 和 selection digest。`RunContextProjection` 持有 process-local body；task text、instruction/role 正文、凭据、reasoning、完整工具参数/结果、SDK 对象和 traceback 不进入 snapshot、事件、日志、SQLite 或 YAML。 |
| fresh/recovery | fresh run 先解析一次并把同一 projection 贯穿 context、admission 和 post-commit projection；rehydration 只按 frozen references 重读并严格比对路径、版本、字节数和 hash。缺少 rehydrator 或 evidence drift 转为 `NEEDS_REPAIR`，恢复路径将 Session quarantine。旧的无 prompt evidence snapshot 仍走 legacy recovery 兼容路径。 |
| message order/budget | 最终 request 顺序固定为 boundary → coding → role/project → Skill/state/Preferences/Memory/checkpoint → conversation user/history；structured view 不获得 tools。完整 OpenAI-compatible wire 使用同一 context budget，受保护层超预算时显式失败，不静默改变指令集合。 |
| capability boundary | ordinary 与 auto-sandboxed production tests 捕获最终 ToolSet、permission profile、process isolation 和 provider-visible messages；项目/角色文本没有解析成工具或权限，也没有改变审批、沙箱、恢复声明或工作区边界。文档中的命令不会执行或自动执行。 |
| evidence boundary | scripted Provider 只证明 Morrow 自身发送了协议和没有生成未请求 artifacts；fake OpenAI-compatible SDK stub 额外验证真实 serializer 入口。两者都没有被当作外部模型重复阈值测量，重复外部阈值留给 S7P-09。 |

## Reviewer 闭环

同一实施会话启动的只读 reviewer：

- agent id：`01a03f2e-192a-7b42-b151-54d639ca80fa`（Boole）
- spawn model/reasoning：`gpt-5.6-luna` / `max`
- review 范围：完整 `30368876457ac740ae36565929190c4cc855e83c...fb9e4dc` diff；reviewer 未修改、暂存、提交或格式化任何文件
- 结论：`COMPLETE_WITH_FINDINGS`；8 个 confirmed P1/P2 findings 已在 `288bba6` 修复并补回归

修复项：role body 与 digest/current assembler provenance 绑定；缺少 prompt rehydrator 时拒绝
boundary-only 静默降级；regular-file identity race 检查；缺失 `O_NOFOLLOW`/directory safe-open
能力时 fail closed；snapshot/prepared source scope 顺序锁定；URL target false positive 过滤；
target iterable、路径长度和 bounded diagnostic 加固。另补充 OpenAI-compatible serializer wire
证据及恢复后 Session quarantine 证据。审查期间未运行 live 或网络测试。

## Commits

- `fb9e4dc` `feat(prompt): add direct coding prompt assembly`
- `288bba6` `fix(prompt): close review boundary gaps`

## 精确验证记录

命令使用当前 worktree 的 `/Users/ruirui/Documents/Project/Agent/developing/.venv/bin/python`，并以
`PYTHONPATH=src` 确保导入当前 topic worktree；`uv` 环境未联网同步且没有添加依赖。

| 检查 | 真实结果 |
|---|---|
| `pytest -q tests/test_project_instructions.py tests/test_direct_coding_prompt.py` | 27 passed in 1.11s |
| `pytest -q tests/test_context_projections.py tests/test_context_runtime.py` | 31 passed in 0.92s |
| `pytest -q tests/test_agent_run_preparation.py tests/test_stage4_recovery_crash.py` | 30 passed in 4.37s |
| `pytest -q tests/test_code_agent_mini_eval.py tests/test_agent_run_observability.py` | 52 passed in 12.09s |
| `pytest -q tests/test_provider.py tests/test_skill_selection.py` | 51 passed, 1 skipped in 1.25s；skip 为 `tests/test_provider.py:572` 的显式 live checklist |
| `pytest -m 'not live' -q` | 1193 passed, 2 skipped, 2 deselected in 50.42s；2 个 skip 是嵌套 Codex sandbox 中不能运行的真实 Seatbelt host-level 测试 |
| `ruff check src tests` | All checks passed |
| `ruff format --check src tests` | 395 files already formatted |
| `python -m compileall -q src tests` | passed |
| `python -m morrow --help` | passed |
| `python -m morrow run --help` | passed |
| 当前 worktree import-path proof | `/Users/ruirui/.codex/worktrees/75c2/developing/src/morrow/__init__.py` |
| `git diff --check` | passed；无输出 |

以上测试均未运行 live Provider/model/Pi/MCP/network/credential 场景；没有开始 S7P-04。topic
branch 保留给根会话做 fast-forward merge、状态核对和后续退休。
