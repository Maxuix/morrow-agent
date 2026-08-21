# Stage 5 模拟用户测试报告

> 日期：2026-08-21
> 测试性质：隔离临时环境中的真实 CLI/REPL 使用评估
> 代码基线：本地 `main`，`4c0bbe2 docs(plan): close stage5 locally`
> 结论：发现 2 个阻断性功能问题和 1 个用户体验问题；本报告只记录问题，不在本次评估中修复
> 修复计划：[`Subplan 55`](../../.agent/subplans/55-stage5-simulated-user-remediation.md)

## 1. 测试边界

测试使用了临时工作空间和独立状态根目录：

- 工作空间：`/tmp/morrow-stage5-sim.FUU0wo`
- 状态根：`/tmp/morrow-stage5-sim-state.7cLbaU`
- 模拟 Workspace ID：`ws__HLJuaTXPTqyvVAI`
- 未使用真实 API Key、Keychain、网络或 Live Provider。
- 测试前已确认仓库没有已跟踪未提交改动；两个 `docs/research/stage5-overview-*.md` 是既有用户文件，按规则保持未跟踪。

首次以默认状态根启动 REPL 时，嵌套沙箱禁止写入真实 `~/.morrow/locks/workspace-index.lock`，因此该次初始化被环境权限阻断。随后使用隐藏的 `--state-root` 指向临时目录重新测试；REPL 正常进入 Provider 引导，在没有凭据时用 Ctrl+C 退出，没有写入凭据。

## 2. 模拟用户流程

### 2.1 Session、Task 和 accepted Outcome

使用真实 CLI 完成了以下操作：

```text
morrow session create --session-id ses_user ...
morrow task new ses_user ...                         -> open
morrow task new ses_user ...                         -> 前一个 TaskRun 变为 abandoned
morrow task cancel <task-2> ...                      -> cancelled
morrow task list ses_user --json                     -> 3 个 TaskRun 均可见
```

第三个任务通过隔离测试夹具补齐一个“回答完成”的 Turn terminal，然后使用真实 CLI 执行：

```text
morrow task accept <task-3> ...                      -> accepted
```

CLI 正确显示 `已排入 Learning Review`。事件序列包含 `task.accepted` 和
`learning.review_requested`，没有发现重复 Review。

### 2.2 Learning policy 和 Review

- `learning set-mode review-only` 成功持久化。
- `learning set-mode explicit-auto` 正确返回 exit 2，并拒绝该保留模式。
- `review-only -> off -> review-only` 切换后，新进程查询仍得到正确的模式和 row version。
- 未配置 active model 时运行 `learning review <id>` 返回 exit 2 和“尚未配置 active_model”，没有把失败伪装成“没有候选”。
- 使用仅限测试夹具的 Scripted Reviewer 完成一次 Review，真实 Review runner 产生 1 个 Preference Candidate；CLI `learning inbox`、`learning show` 能显示候选、Evidence、digest、scope 和配置预览。

### 2.3 Candidate 决策、配置和 Memory

Preference Candidate 的底层 Promotion 服务成功写入隔离 YAML：

```yaml
preferences:
  language: zh-CN
```

随后独立 CLI 进程仍能读取该配置，`configuration.activated` 和
`memory.record_activated` 事件均存在。

另外建立了一个 Skill Candidate 夹具，验证其 rejection/suppression 的底层服务可以正常写入；但 CLI 决策入口见下方问题 F1。

尝试用底层 Promotion 服务激活全新的 Project Knowledge Candidate 时失败，未创建 Knowledge head，CLI `memory list` 仍为空；详见 F2。

### 2.4 Doctor、Backup 和重启持久化

- 模拟 Turn 尚未写入 terminal 时，`state doctor` 正确报告 `open_turn` warning；补齐 terminal 后恢复 `health=ok`。
- `state backup --name stage5-sim-final` 成功，bundle schema 为 v12。
- `state verify-backup ...` 成功：`database_integrity_ok`、`foreign_keys_ok`、`learning_references_ok`、`memory_references_ok`、`manifest_ok` 均为 true，`credentials_excluded=true`，issues 为空。
- 新进程重新查询 Learning status、Inbox、Review 和 Preferences，持久化状态可见。

## 3. 问题清单与原因分析

### F1 — P1：Learning Candidate 的 CLI accept/reject 在确认后必然失败

复现：

```text
morrow learning accept <preference-candidate> --scope workspace ...
确认接受这项 Learning Candidate？ y
-> application command failed
-> exit 2
```

`learning reject ... --never-suggest` 也出现相同现象。通过隔离的 CLI 调用捕获真实异常，得到：

```text
AttributeError: 'LearningCandidateView' object has no attribute 'candidate_id'
```

原因在 `src/morrow/interfaces/learning_cli.py`：`_candidate_or_error()` 返回的是
`LearningCandidateView`，但 `learning_accept()` 和 `learning_reject()` 分别在候选命令构造时访问了
`candidate.candidate_id` 和 `candidate.row_version`；实际字段位于嵌套的
`candidate.candidate` 对象中。因此用户已经完成预览和确认后，命令仍以泛化错误结束。

底层 `OperationalApplicationService` 的 accept/reject 服务单独调用可以成功，说明问题集中在 CLI adapter，而不是 Candidate 决策事务本身。

影响：Preference/Profile/Project Knowledge/未来 Candidate 的终端 accept/reject 入口均不可用，Stage 5 的核心用户闭环被阻断。

### F2 — P1：首次 Project Knowledge Promotion 因微秒时间戳被误判为 identity immutable

复现：通过真实 Promotion 服务接受一个全新的 `ProjectKnowledgeCandidate`，返回：

```text
ApplicationError: knowledge head identity is immutable
```

原因链：

1. `src/morrow/application/learning/promotion.py` 创建新 `ProjectKnowledgeHead` 时使用了含微秒的 `journal.now()`。
2. `put_project_knowledge_head()` 通过 `_unix()` 按秒写入 SQLite。
3. 随后同一事务用仍含微秒的内存对象调用 `save_project_knowledge_head()`。
4. `src/morrow/adapters/state/learning_memory_journal.py` 将数据库重新读出的秒级 `created_at` 与内存中的微秒级 `created_at` 比较，误认为 head identity 被改变。

本次运行中 `journal.now()` 的微秒部分为 `612413`，因此该问题稳定可复现。事务回滚，Candidate 保持 proposed，未创建 Knowledge head 或 Memory revision。

影响：新项目知识无法首次激活；已有 Knowledge head 的后续更新路径未在本次模拟中声称通过。

### F3 — P2：Reject 预览显示错误的 decision kind

运行 `learning reject` 时，确认前的预览显示：

```text
decision_kind: accept
```

随后才显示“确认拒绝这项 Learning Candidate？”。这是因为 reject CLI 复用了默认的
`preview_learning_candidate_decision()`，没有传入 reject 意图。虽然底层 rejection/suppression 可以正确落盘，但预览会误导用户对即将执行的动作产生判断。

### F4 — 测试环境限制，不判定为产品 bug

默认 REPL 依赖标准 `~/.morrow` 状态根；当前嵌套沙箱禁止写入该目录，导致首次工作空间登记失败。使用产品已有的 `--state-root` 隔离参数后测试继续进行。无真实用户状态、Keychain 或凭据被修改。

## 4. 自动化对照结果

```text
UV_CACHE_DIR=/tmp/morrow-stage5-uv-cache uv run pytest -m 'not live' -q tests/test_stage5*
128 passed, 1 deselected
```

自动化测试通过并不覆盖本次发现的两个问题：现有 CLI 测试没有用真实的
`LearningCandidateView` 字段形状完成确认后的 accept/reject；Project Knowledge 测试使用了整秒的固定时钟，未覆盖真实运行时的微秒时间戳。

## 5. 建议修复顺序

1. 修正 CLI 对 `LearningCandidateView` 的嵌套字段访问，并增加真实 Typer/CLI 确认后的 accept、edit、reject、never-suggest 回归测试。
2. 统一 Project Knowledge head 的时间精度：要么在领域对象创建时截断到持久化精度，要么在 identity 比较时比较规范化后的时间；增加非零微秒时钟的首次 Promotion 集成测试。
3. 为 reject 生成明确的 reject preview/decision kind，并增加终端输出快照测试。
4. 修复后重新执行本报告中的相同模拟流程，再决定是否可以把 Stage 5 标记为用户可用。
