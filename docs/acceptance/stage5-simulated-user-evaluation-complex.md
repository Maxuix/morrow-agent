# Stage 5 复杂模拟用户测试报告

> 日期：2026-08-21  
> 代码基线：`f18c550 fix(stage5): close simulated user remediation`  
> 测试性质：隔离临时状态根中的真实 CLI、多进程重启与持久化验收  
> 结论：未发现新的产品级 issue；此前 Subplan 55 修复的 Candidate、Reject 预览和 Project Knowledge 首次晋升闭环均通过复杂回放。

## 1. 测试边界与方法

本轮没有修改生产代码，也没有使用网络、Live Provider、API Key、Keychain 或真实用户状态。所有命令都通过独立的 `uv run morrow` 进程执行，以验证跨进程持久化。

由于未配置真实模型，本轮用确定性的已完成 Task Outcome、Review、Evidence 和 Candidate 夹具建立用户可见的初始状态；Candidate 的查询、预览、确认、拒绝、Memory 生命周期和诊断/备份全部使用真实 CLI 完成。

临时测试目录：

- Preference：`/tmp/morrow-stage5-complex.07htvd/final/preference`
- Profile：`/tmp/morrow-stage5-complex.07htvd/final/profile`
- Project Knowledge：`/tmp/morrow-stage5-complex.07htvd/final/knowledge2`
- Workspace ID：`ws_1`

## 2. 复杂用户流程与结果

### 2.1 Preference：查看、错误编辑、编辑接受、取消撤销、确认撤销

执行了以下用户路径：

```text
learning show lcn_1 --json
learning edit lcn_1 --statement '错误的 preference 字段'
learning edit lcn_1 --path language --value English   # 确认 y
learning undo <activation_id>                         # 先确认 n，再确认 y
```

结果：

- 使用 Project Knowledge 专用字段编辑 Preference 被拒绝，exit code 为 2；Candidate 仍为 `proposed`，没有产生 Decision。
- 合法编辑显示 `decision_kind: edit_and_accept`，确认后生成 Preference activation 和 `memory.record_activated` 事件。
- 撤销预览先取消时 exit code 为 2 且没有写入；再次确认后生成 reverse activation，配置恢复为未设置。
- 新进程读取 YAML 后确认 `preferences.language=None`，说明编辑和撤销均已持久化。

### 2.2 Profile：取消确认、reject-and-suppress 与后续 Inbox 行为

执行了以下路径：

```text
learning reject lcn_1 --never-suggest --reason '用户先查看影响范围'  # 确认 n
learning inbox --json
learning reject lcn_1 --never-suggest --reason '用户拒绝写入项目 profile' # 确认 y
learning show lcn_1 --json
```

结果：

- 第一次取消后 Inbox 仍有 1 个 `proposed` Candidate，row version 未变化。
- 第二次预览明确显示 `decision_kind: reject_and_suppress`，并说明以后不再建议同类候选。
- 确认后 Candidate 为 `rejected`，产生 active suppression；后续 proposed Inbox 为空。
- Profile 文件没有被修改，`learning show` 能跨进程读取 rejection reason 和 suppression。

### 2.3 Project Knowledge：专用编辑、首次晋升、历史查询与生命周期

执行了以下路径：

```text
learning edit lcn_1 --path statement                 # 预期拒绝
learning edit lcn_1 --statement '...' \
  --semantic-key architecture.persistence \
  --category architecture                         # 确认 y
memory list --json
memory show knw_m6Y8uxAyWbjaTgyh --revision 1 --json
memory disable knw_m6Y8uxAyWbjaTgyh                # 确认 y
memory enable knw_m6Y8uxAyWbjaTgyh                 # 确认 y
memory dispute knw_m6Y8uxAyWbjaTgyh                # 确认 y
```

结果：

- 使用通用 `--path` 被 fail-closed 拒绝；使用 `--statement/--semantic-key/--category` 成功进入 `edit_and_accept`。
- 首次 Project Knowledge 晋升成功，生成 `knowledge_id=knw_m6Y8uxAyWbjaTgyh`、revision 1 和 `memory_revision=1`。
- 新进程 `memory list/show` 能读取当前 head、revision、evidence、digest 和历史 timeline。
- `active -> disabled -> active -> disputed` 生命周期全部成功；disputed 状态仍保留 revision 和证据。
- `memory selection list` 在没有 AgentRun 的情况下返回空页，不报错、不伪造 Selection。

### 2.4 多 Task 生命周期与事件顺序

在 Preference 状态根中保留 1 个已接受 Task，再通过真实 CLI 新建并取消两个 Task：

```text
task list ses_1 --json       # 既有 accepted Task
task new ses_1               # open
task cancel <task-2>         # cancelled
task new ses_1               # open
task cancel <task-3>         # cancelled
task list ses_1 --json       # 1 accepted + 2 cancelled
```

结果：3 个 TaskRun 均可跨进程查询；事件顺序为两组 `task.created -> task.cancelled`，没有重复或悬挂 Task。

## 3. 异常与可靠性测试

| 场景 | 结果 |
| --- | --- |
| 不存在的 `--dir` | 正确返回 exit 2，未创建状态 |
| 不存在的 Candidate | 正确返回 `not_found` 和 exit 2 |
| Preference 使用 Project Knowledge 字段 | 正确拒绝，无 Decision 写入 |
| Project Knowledge 使用通用 `--path` | 正确拒绝，无 Decision 写入 |
| 确认提示输入 `n` | 退出并保持原状态 |
| 篡改 bundle 放在受管目录外 | 正确拒绝“outside the managed store” |
| 篡改受管 bundle 中的 Decision digest | `learning_references_ok=False`，issue 为 `learning_decision_candidate`，exit 2 |

最后一项说明 backup 验证会检查 Learning 引用一致性，而不仅是 SQLite 文件完整性。

## 4. 持久化、Doctor 与 Backup

三个最终状态根均通过：

```text
state doctor --json -> health=ok, issues=[]
```

关键计数：

- Preference：3 个 TaskRun、2 个 Candidate Decision、2 个 configuration activation/reverse operation。
- Profile：1 个 reject Decision、1 个 active suppression，Profile 未被意外写入。
- Project Knowledge：1 个 Knowledge head、1 个 revision、1 个 Memory workspace state、1 个 Memory head。

以下三个 bundle 均通过 `state verify-backup`：

- `complex-preference-final.bundle`
- `complex-profile.bundle`
- `complex-knowledge.bundle`

每份结果均为 `database_integrity_ok=True`、`foreign_keys_ok=True`、`manifest_ok=True`、`memory_references_ok=True`、`learning_references_ok=True`、`credentials_excluded=True`，`issues=[]`。

## 5. 测试过程中发现的问题分析

### 5.1 产品级问题

本轮没有发现新的产品级 issue。此前报告中的 F1（CLI typed view 字段访问）、F2（Project Knowledge 时间精度）和 F3（Reject preview decision kind）在本轮真实 CLI 确认路径中均已通过。

### 5.2 测试夹具问题：固定时间导致 Candidate 首次过期

第一次预检使用了 `2026-01-01` 固定时钟，但当前日期为 `2026-08-21`；CLI 重启后按真实时钟将候选正确标记为 expired。该行为符合 TTL 设计，不是产品缺陷。随后将隔离夹具有效期设置到 2030，并重新建立干净状态后完成正式测试。

### 5.3 测试夹具问题：Project Knowledge 初始对话不完整

第一次 Project Knowledge 夹具只有 User message，补写 terminal 后被 doctor 正确识别为非法消息语法，因为 `stop` Turn 还必须有最终 Assistant message。重新建立 `User -> Assistant -> terminal` 的完整对话后，doctor 恢复为 `health=ok`。这是夹具构造问题，也验证了 doctor 对 open/incomplete Turn 的检测能力。

### 5.4 环境限制：UV 默认缓存目录不可写

首次运行因嵌套沙箱无法访问 `/Users/ruirui/.cache/uv` 而失败；改用临时 `UV_CACHE_DIR=/tmp/morrow-stage5-uv-cache` 后所有命令正常。未修改项目或用户状态。

### 5.5 未覆盖项：真实 Provider 质量

本轮没有真实模型、网络或凭据，因此没有验证自然语言 Review 的候选分类质量、模型超时或真实 Provider 重试。该项保持为授权/凭据条件下的后续 Live 测试，不判定为本地产品 bug。

## 6. 自动化门禁

```text
UV_CACHE_DIR=/tmp/morrow-stage5-uv-cache uv run pytest -m 'not live'
831 passed, 2 skipped, 2 deselected in 21.01s

Stage 5 focused suites
52 passed in 2.69s

ruff check       All checks passed
ruff format      260 files already formatted
compileall       passed
git diff --check passed
```

两个跳过项是嵌套 Codex sandbox 无法运行真实 Seatbelt 的既有限制；Live 测试另有 2 个被明确排除。

## 7. 最终结论

在无真实 Provider 的确定性环境中，Stage 5 的复杂用户闭环已通过：多 Task 状态变化、Candidate 查询与决策、取消确认、never-suggest、Preference 撤销、Project Knowledge 首次晋升与生命周期、跨进程持久化、doctor、backup 和篡改检测均表现正常。

本轮不需要新增修复计划。真实 Provider 的 Review 质量和网络异常路径仍需在具备明确授权与兼容凭据后单独验收。
