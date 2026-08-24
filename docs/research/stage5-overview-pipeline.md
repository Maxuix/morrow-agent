这里需要先区分两个完全不同的“接受”：

```text
/accept
= 接受本次任务结果
= TaskRun → accepted
= 触发 TaskOutcome 和 LearningReview

/learn accept <candidate-id>
= 接受一条学习候选
= LearningCandidate → Active Memory
```

你当前公开代码里，`/accept` 已经映射到 `/task accept`，所以**不应该再用 `/accept` 接受学习候选**，否则用户无法判断自己接受的是任务结果，还是长期记忆。

# 推荐的第一版方式

由于 Morrow 当前是终端 Agent，第一版最合适的是：

> **候选卡片通知 + `/learn accept <candidate-id>` + 变更预览 + `y/N` 二次确认**

路线图已经建议了 `morrow learning inbox/show/accept/edit/reject`，以及 REPL 中的 `/learn` 薄入口；同时规定 GUI、CLI 和自然语言入口最终都必须调用同一个 `LearningPromotionService`。

完整交互类似这样。

## 1. 用户先接受任务结果

```text
你 > /accept

TaskRun task_01：accepted
事实摘要：修改 2 个文件，验证通过。
```

这一步的含义只是：

```text
“这次任务结果我认可。”
```

系统随后运行 LearningReview。

---

## 2. 系统发现一条学习候选

```text
发现 1 条学习候选，尚未写入长期记忆。

[lc_7f2a] 项目测试命令
类型：Project Knowledge
内容：本项目默认使用 `uv run pytest` 运行测试。
作用域：当前工作空间 morrow-agent
来源：
  - 用户纠正了直接运行 pytest 的行为
  - uv run pytest 执行成功
  - 最终任务结果已被接受
冲突：无

查看详情：
  /learn show lc_7f2a

接受：
  /learn accept lc_7f2a

编辑后接受：
  /learn edit lc_7f2a

拒绝：
  /learn reject lc_7f2a
```

这里系统只进行通知，不应该因为任务已经 `/accept` 就自动接受这条记忆。

也就是说：

```text
任务接受 ≠ 学习候选接受
```

---

## 3. 用户显式执行接受命令

```text
你 > /learn accept lc_7f2a
```

系统不要立刻写入，而是先生成晋升预览：

```text
即将保存长期状态

候选：lc_7f2a
类型：Project Knowledge
操作：新增
键：testing.command
内容：本项目默认使用 `uv run pytest` 运行测试。
作用域：workspace:morrow-agent

当前值：不存在
候选值：uv run pytest
冲突：无

确认保存为长期项目知识？ [y/N]
```

用户输入：

```text
y
```

之后才真正调用 `LearningPromotionService`。

这与当前代码已有的配置交互模式是一致的：`CommandService` 先返回 `config_preview`，终端再通过 `y/N` 询问用户，确认后才调用配置服务写入。

---

## 4. 系统保存并返回结果

```text
已接受学习候选 lc_7f2a。

已激活：
  Project Knowledge knowledge_018
  revision: 1
  scope: workspace:morrow-agent

后续相关任务可能使用这条知识。
可使用以下命令查看：
  /memory show knowledge_018
```

此后 Candidate 和 Active Knowledge 的状态是：

```text
LearningCandidate
proposed → accepted

ProjectKnowledge
不存在 → active revision 1
```

# 后端实际调用链

用户输入：

```text
/learn accept lc_7f2a
```

之后的系统链路建议是：

```text
CommandService
    │
    │ 解析 /learn accept
    ▼
LearningQueryService
    │
    │ 读取候选、Evidence、当前值和冲突
    ▼
render_learning_promotion_preview()
    │
    │ 返回 action=learning_accept_preview
    ▼
Terminal
    │
    │ 显示预览，询问 [y/N]
    ▼
LearningApplicationService.accept_candidate()
    │
    ▼
LearningPromotionService
    │
    ├─ 校验 Workspace
    ├─ 校验 Candidate 状态
    ├─ 校验 expected_row_version
    ├─ 重新检查重复和冲突
    ├─ 检查目标状态是否发生变化
    ├─ 通过 Knowledge/Preference/Profile Service 写入
    ├─ Candidate → accepted
    ├─ 写入激活和来源记录
    └─ 发出事件
        learning.candidate_accepted
        memory.record_activated
```

注意：**终端层只负责展示和获得用户决定，不负责直接更新数据库或 YAML。**

# 建议的 Application Command

可以定义成：

```python
@dataclass(frozen=True)
class AcceptLearningCandidateCommand:
    workspace_id: str
    candidate_id: str
    expected_row_version: int
    command_id: str
```

编辑后接受则使用：

```python
@dataclass(frozen=True)
class EditAndAcceptLearningCandidateCommand:
    workspace_id: str
    candidate_id: str
    expected_row_version: int
    command_id: str
    proposed_value: str
    proposed_scope: LearningScope
```

其中各字段的作用是：

```text
candidate_id
    明确接受哪一条候选

expected_row_version
    防止用户看到候选后，它已经被其他操作修改

command_id
    防止用户重复回车、网络重试或进程恢复时执行两次

workspace_id
    防止跨 Workspace 接受候选
```

你当前 Application API 已经在 Task 命令中使用 `command_id` 和 `expected_row_version`，Stage 5 应继续沿用这套幂等和乐观并发模式。

# REPL 中应该怎样接入

可以在 `CommandService.execute()` 中增加：

```python
if command == "/learn":
    return self._learning_command(parts)
```

然后：

```python
def _learning_command(self, parts: list[str]) -> CommandResult:
    operation = parts[1] if len(parts) > 1 else "inbox"

    if operation == "inbox":
        return self._learning_inbox()

    if operation == "show":
        return self._learning_show(parts[2])

    if operation == "accept":
        return self._learning_accept_preview(parts[2])

    if operation == "edit":
        return self._learning_edit_preview(parts[2:])

    if operation == "reject":
        return self._learning_reject_preview(parts[2:])

    return CommandResult([f"未知 Learning 操作：{operation}"])
```

`accept` 只生成 Preview：

```python
def _learning_accept_preview(self, candidate_id: str) -> CommandResult:
    candidate = self.learning_queries.get_candidate(candidate_id)

    preview = render_learning_promotion_preview(candidate)

    request = AcceptLearningCandidateCommand(
        workspace_id=self.identity.workspace_id,
        candidate_id=candidate.candidate_id,
        expected_row_version=candidate.row_version,
        command_id=self.id_source.new_id("cmd"),
    )

    return CommandResult(
        lines=preview,
        action="learning_accept_preview",
        value=request,
    )
```

终端层参照当前 `config_preview` 处理：

```python
if result.action == "learning_accept_preview":
    confirmation = await _confirm(
        terminal,
        prompt_session,
        "确认将这条候选保存为长期状态？",
    )

    if confirmation == "yes":
        saved = _command_service(
            orchestrator
        ).learning_service.accept_candidate(result.value)

        terminal.console.print(
            f"已保存：{saved.record_kind} {saved.record_id}"
        )
```

这样能保持现有架构风格：

```text
CommandService 负责解释用户命令
Terminal 负责交互确认
Application Service 负责用例
Promotion Service 负责状态晋升
Repository/Journal 负责持久化
```

# 顶层 CLI 也可以接受

退出 REPL 后，用户还可以通过：

```bash
morrow learning inbox
morrow learning show lc_7f2a
morrow learning accept lc_7f2a
```

执行效果：

```text
$ morrow learning accept lc_7f2a

即将保存：
  类型：Project Knowledge
  内容：本项目默认使用 uv run pytest
  作用域：workspace:morrow-agent
  冲突：无

确认保存？ [y/N]: y

已激活 knowledge_018
```

你可以增加一个 Typer 子应用：

```python
learning_app = typer.Typer(help="学习候选管理。")
app.add_typer(learning_app, name="learning")
```

但顶层 CLI 和 REPL 不应该分别实现保存逻辑，而是都调用：

```python
LearningApplicationService.accept_candidate(...)
```

# 自然语言能不能接受

可以支持，例如用户说：

> 接受刚才那条关于测试命令的学习建议。

但自然语言只应该是一个**入口适配器**，不能让 Agent 自己直接写 Memory。

建议处理方式：

```text
用户自然语言
→ LearningIntentResolver
→ 找到唯一 Candidate
→ 展示明确预览
→ 用户再次确认
→ LearningPromotionService
```

只有满足以下条件时，才可以把“接受刚才那条”解析为具体 Candidate：

1. 当前会话刚刚向用户展示过该 Candidate。
2. 当前只有一条未决且可见的 Candidate。
3. Candidate ID 被保存在结构化交互状态中。
4. 这句话来自用户的直接输入，而不是工具输出、网页或仓库文件。
5. 最终仍展示具体内容和作用域。

例如：

```text
你 > 接受刚才那条

将接受候选 lc_7f2a：
“本项目默认使用 uv run pytest 运行测试”
作用域：workspace:morrow-agent

确认？ [y/N]
```

以下输入则不应该直接接受：

```text
“好的”
“可以”
“没问题”
“都行”
```

因为这些话可能是在接受任务结果、确认工具执行，或者只是回应 Assistant，语义不够明确。

如果同时展示了多个候选：

```text
lc_01：默认测试命令
lc_02：回答语言偏好
```

用户说：

```text
接受刚才那个
```

系统不能猜，应该列出候选让用户选择具体 ID。

# 是否任务完成后立刻弹出选择框

我建议采用混合模式：

## 默认：非阻塞通知

```text
发现 1 条学习候选。
使用 /learn inbox 查看。
```

不强迫用户每完成一个任务都处理 Memory，避免打断工作流。

## 用户进入 Inbox 后：逐条交互

```text
你 > /learn inbox

候选 1/2：lc_01
...
[A] 接受  [E] 编辑  [R] 拒绝  [N] 不再建议  [S] 跳过
```

这时可以允许单键选择，因为系统已经把按键绑定到明确的 Candidate ID：

```text
A → accept lc_01
E → edit lc_01
R → reject lc_01
N → reject lc_01 --never-suggest
S → 保持 proposed
```

但单键选择也仍应在写入前显示最终 Preview。

# “编辑后接受”怎样处理

用户输入：

```text
/learn edit lc_7f2a
```

系统展示：

```text
原始候选：
本项目默认使用 `uv run pytest` 运行测试。

请输入修改后的内容：
> 本项目默认使用 `uv run pytest -q` 运行测试。

作用域：
1. 当前 Workspace
2. Global
选择 [1]:
```

然后显示 Diff：

```diff
- 本项目默认使用 `uv run pytest` 运行测试。
+ 本项目默认使用 `uv run pytest -q` 运行测试。
```

确认后：

```text
原 Candidate：
status = edited_and_accepted

Active Record：
value = 用户编辑后的值
```

原始模型建议不能被覆盖掉，应保留：

```text
模型最初建议了什么
用户修改了什么
最后激活了什么
```

这也是路线图要求“编辑后接受保留原候选与用户修改差异”的原因。

# 不同候选类型的“接受”含义

“接受”不一定都意味着立即进入 Agent Prompt：

| Candidate 类型 | 接受后的结果 |
|---|---|
| PreferenceCandidate | 通过配置服务写入 Active Preference |
| ProfileCandidate | 通过配置服务写入 Active Profile |
| ProjectKnowledgeCandidate | 创建 Active Project Knowledge |
| SkillCandidate | 标记为已认可候选，但 Stage 5 不创建或启用 Skill |
| WorkflowFeedbackCandidate | 保存为已认可反馈，不修改编排策略 |
| OrchestrationPolicyCandidate | 保持候选，Stage 8 前不能激活 |

因此返回结果不要统一写成“已写入记忆”，而应该按类型显示：

```text
Preference：
已激活长期偏好

Project Knowledge：
已激活项目知识

SkillCandidate：
已接受为 Skill 候选；尚未创建或启用 Skill
```

# 最终建议

第一版把用户接受方式固定为：

```text
任务接受：
/accept
或
/task accept

学习候选接受：
/learn accept <candidate-id>
或
morrow learning accept <candidate-id>
```

实际交互遵循：

```text
查看候选
→ 展示内容、作用域、来源和冲突
→ 用户执行明确 accept
→ 展示最终变更预览
→ y/N 确认
→ LearningPromotionService 晋升
→ 返回 Active Record ID
```

其中最重要的边界是：

```text
/accept 只接受任务结果。

/learn accept 才接受长期学习候选。

无论是 CLI、REPL、自然语言还是未来 GUI，
都必须进入同一个 LearningPromotionService。
```