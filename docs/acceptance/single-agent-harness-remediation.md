# 单 Agent harness 修复验收

用户授权：按 Pi 对照审查中已确认的问题修复，避免过度工程和新增阻塞。
范围限于既有 AgentLoop、ContextBuilder、ConversationLog、工具异常归类和请求账本。
实现基于 Stage 8 Subplans 1–13 完成后的本地主线；未激活后续阶段。

## 修复与证据

| 问题 | 修复 | 重点回归 |
|---|---|---|
| 空或歧义摘要推进边界 | 拒绝空内容、仅未知字段、多对象；失败保留旧边界，后续仍可重试 | `test_s7p06_pi_parity.py` |
| 工具产生副作用后普通异常被当成确定失败 | 按既有 recovery declaration 保留 UNKNOWN，提示核对实际状态 | `test_stage4_tool_persist.py`，真实 SQLite 加进程内副作用夹具 |
| 摘要 finish、输出上限和用量丢失 | 规范化 completion facts；非 STOP 不安装；显式输出 cap；活动运行的摘要 admission/settlement 复用请求账本 | `test_provider.py`、`test_agent_run_observability.py` |
| 下一轮工具结果使 usage 锚点失效 | 保存模型、工具和包含已完成 Assistant 的前缀身份，复用 usage 并估算尾部 | `test_s7p06_pi_parity.py` |
| 摘要提升为 System 指令 | 历史摘要和上次摘要移入标注为历史数据的 User-role 消息 | `test_s7p06_pi_parity.py`、`test_context_projections.py` |
| 整个响应完成后才显示文本 | 完整行可提前发送；分块凭据脱敏；失败预览不写历史；最终修订发出 reset 提示 | `test_harness_streaming.py`、既有 Context/Orchestrator 测试 |
| 每次追加重复扫描整段历史 | 当前不可变 append 增量提交；恢复和外部快照仍完整校验；普通持久化追加不重载历史 | `test_conversation_and_loop.py`、`test_agent_run_observability.py` |

扫描次数回归：100 个含工具周期的 Turn 共 500 条记录，普通追加及快照生成不触发全历史语法扫描；
恢复时完整扫描 500 条记录。该测试计数而不依赖运行耗时。快照仍需复制引用、上下文仍需投影，
不宣称整个会话处理为 O(1)。

请求账本使用 v30 迁移扩展现有表的 CHECK，允许 compaction purpose 和已有的 context_overflow
错误分类。升级测试保存 v29 请求后比较全部列，并检查外键及 workspace guards；既有旧版本升级
测试同步覆盖到 v30。摘要不消耗普通 agent-generation 配额，但仍受 Workflow admission deadline
约束。没有新增表、依赖、策略默认值、公共事件类型或第二历史写入者。

## 行为边界

- 结构校验防止确定的空摘要丢失上下文；不声称能证明自然语言摘要语义完整，也不增加第二模型评审门禁。
- 空闲手动压缩没有新 AgentRun，usage/cost 保存在 CompactionEntry；注入的旧 string-only Provider
  继续兼容，未知 usage 明确标为 unavailable，生产 Adapter 则使用带输出 cap 的 completion facts。
- 提前显示以完整行为单位；未换行的末段在响应收口后显示。无提前输出且无需脱敏时，保留既有分块。
- 工作流 TextResult 对已有 `<redacted>` 标记保守标注 content_complete=false；占位符仍合法、
  可显示，标记不阻止节点结束，也不增加原始敏感内容的持久化字段。
- OpenAI 官方端点使用 max_completion_tokens，其他 compatible 端点使用 max_tokens；不在参数
  不兼容时静默取消输出限制。未运行真实 Provider/MCP 测试。

## 验证状态

修复代码提交：`fac636c`、`e4bed83`、`12c1422`。

- 完整离线门禁：`uv run --no-sync pytest -q -m 'not live'`，**1783 passed、2 skipped、
  2 deselected**，613.31 秒，exit 0。使用已有开发环境，并以 `PYTHONPATH=src` 指向隔离工作树。
- 两项 skipped 为嵌套 Codex 沙箱内无法执行的宿主 Seatbelt 测试；两项 Live 明确排除。
- `ruff check .`、`ruff format --check .`：通过（638 个 Python 文件格式正确）。
- `python -m compileall -q src tests`、`morrow --help`、`git diff --check`：通过。
- 首轮全量暴露的旧分块兼容性、工作流脱敏完整性和迁移版本预期问题均已修复，并由最终全量覆盖。

交付范围为本地主线；远端 push 和 Live 测试未授权，未执行。没有修改 GUI 源码。
