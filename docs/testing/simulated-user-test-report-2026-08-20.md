# Morrow 模拟用户环境测试报告

日期：2026-08-20  
代码提交：`a0bb2fb fix(review): harden recovery and application boundaries`  
测试状态：完成

## 1. 测试目标与边界

本轮在提交代码后执行，目标是从真实终端用户角度检查：

- 基础 coding 任务能否完成；
- 工具审批、拒绝和权限边界是否符合预期；
- Session、TaskRun、ConversationLog、Operational Store 和备份是否可持久化恢复；
- 非常规输入、无效资源、归档对象和路径穿越是否 fail closed；
- CLI 已实现功能的可用性和用户可见错误。

测试使用隔离的临时状态目录和临时工作空间，没有修改真实用户状态，也没有使用真实 Provider 或真实 API Key。模型请求由仅监听 `127.0.0.1` 的本地 OpenAI-compatible 假 Provider 返回，工具执行和持久化仍使用项目真实实现。

临时测试目录：`/private/tmp/morrow-user-sim.naYi04`。测试结束后未删除，便于复核；其中不包含真实凭据。

## 2. 提交前置验证

在提交 `a0bb2fb` 前已完成：

- `uv run pytest -m 'not live'`：680 passed，1 deselected；
- `uv run ruff format --check .`：通过；
- `uv run ruff check .`：通过；
- `uv run python -m compileall -q src tests`：通过；
- `uv run morrow --help`：通过；
- `git diff --check`：通过。

提交后仓库保持干净，`main` 当前 HEAD 为 `a0bb2fb`。

## 3. 用户场景测试记录

### 3.1 Provider 与首次启动

执行了 `provider list`、`provider show`、`model list`、`model current` 和 `provider test`。本地假 Provider 返回成功，模型显示为 `local/fake-model`。

首次执行根命令时：

1. 检测到新工作空间；
2. 要求确认登记；
3. 要求填写项目目标；
4. 进入 REPL，显示 `Morrow 承序 · Workspace terminal agent.`。

结果：通过。确认和 onboarding 流程符合预期。

### 3.2 Coding 任务 A：创建文件

用户输入：要求在 `src/hello.py` 创建简单 Python 程序。

实际流程：

1. 模型调用真实 `write_file` 工具；
2. 终端展示操作类型、路径、Diff、变更行数和审批编号；
3. 用户输入 `y`；
4. 文件成功写入；
5. REPL 显示工具成功、修改 1 个文件；
6. TaskRun 进入 `ready_for_acceptance`；
7. `/accept` 后 TaskRun 进入 `accepted`。

实际文件内容：

```python
print('hello from morrow')
```

结果：通过。基础 coding 任务完成，审批、Diff 和持久化链路均正常。

### 3.3 Coding 任务 B：新 Session 读取已有文件

执行 `/new` 创建独立 Session，要求读取并检查 `src/hello.py`。

结果：真实 `read_file` 工具成功，未产生文件修改；新 Session 可以读取前一 Session 已提交的工作区文件。通过。

### 3.4 用户拒绝写入

要求创建 `src/hello_extra.py`，在 Diff 审批界面输入 `n`。

结果：

- REPL 显示工具失败；
- `tool_executions` 记录为 `write_file / denied`；
- `src/hello_extra.py` 不存在；
- 没有发生部分写入。

结果：通过，审批拒绝是 fail closed 的。

### 3.5 退出、重启与恢复

在有持久化会话后执行 `/exit`，再使用同一 `--session-id` 启动：

```text
morrow --dir <project> --state-root <state> --session-id <session-id>
```

重启后再次读取 `src/hello.py` 成功。Session、ConversationLog、TaskRun 和工具执行记录均保留。

结果：通过。

### 3.6 TaskRun 生命周期

通过 CLI 执行并验证：

- `task accept`：成功；
- `task new`：成功创建 `open` TaskRun；
- `task cancel`：成功进入 `cancelled`；
- 对 `cancelled` TaskRun 执行 `task resume`：返回 exit 2 和稳定错误 `invalid: ... cannot transition from cancelled`。

结果：合法转换成功，非法转换被拒绝，状态没有被破坏。

### 3.7 Session 生命周期与分支

通过 CLI 执行并验证：

- `session fork --cut-position 5`：成功创建 child Session，父 Session 不变；
- `session archive`：成功归档空闲 Session；
- 对归档 Session 执行 `task new`：返回 `invalid: only an active Session can start a TaskRun`。

结果：通过。

### 3.8 备份、校验、Doctor、事件和清理

执行：

- `state backup --name simulated-user`：成功生成 bundle，`integrity_ok=True`，`credentials_excluded=True`；
- `state verify-backup <bundle> --state-root <state>`：成功，数据库完整性、外键、manifest 和 artifacts 全部通过；
- `state doctor --workspace-id ... --json`：`health=ok`，`issues=[]`；
- `state events`：成功列出 Task accepted 事件；
- `state cleanup`（默认 dry-run）：`removed=0`、`quarantined=0`、`refused=0`。

最终 Doctor 统计：6 个 Session、7 个 TaskRun、8 个 ToolExecution、48 条 ConversationRecord、9 个 PermissionSnapshot，未发现 orphan 或权限证据问题。

注意：第一次手工校验 backup 时省略了隐藏的 `--state-root`，命令检查了默认状态目录并返回“operational store is missing”；补上正确的 `--state-root` 后校验通过。这是测试命令参数遗漏，不是 backup 产物问题。

### 3.9 空资源和无效资源

对不存在的 Artifact、CapabilityGrant、RecoveryReport 和 Provider 执行 show/pin/release/resolve，均返回 exit 2 及稳定的 `not_found` 或未知 Provider 错误，没有创建幽灵记录。

### 3.10 权限与越狱测试

#### Host 命令越权

假模型尝试调用：

```json
{"argv": ["cat", "/etc/passwd"], "cwd": "."}
```

系统在生成审批前拒绝，`tool_executions` 记录为 `run_command / denied`，没有执行敏感命令。

#### 路径穿越读取

假模型尝试调用 `read_file` 读取 `../outside-secret.txt`。

结果：工具失败，数据库错误码为 `invalid_path`，工作区外文件未被创建或读取。

#### 普通 AgentRun 授予 unconfined Host 权限

CLI 展示了明确警告，即使用户确认，普通 `manual` AgentRun 仍被拒绝：

```text
invalid: CapabilityGrant requires a Full Access Manual AgentRun
```

结果：权限授予没有绕过 AgentRun 权限快照和预设约束。

#### 合法写入审批

已验证用户明确输入 `y` 才会落盘，输入 `n` 不会落盘。

## 4. 发现与问题分析

### P0/P1：未发现

本轮没有发现数据丢失、越权读写、审批绕过、Session 无法恢复或 Operational Store 损坏问题。

### P2：Provider onboarding 的错误信息过于笼统

用假凭据直接执行真实 preset 的 `provider add`，由于测试环境没有真实网络，命令返回：

```text
Provider 添加失败：ModelProviderError
```

问题分析：`ProviderService.add` 将模型连接测试异常重新抛出，CLI `provider_add` 对通用异常只输出异常类型，没有输出稳定的 `network/auth/timeout` 分类和用户下一步建议。用户无法判断是网络、凭据、限流还是 Provider 配置问题。

影响：首次配置失败时诊断成本高，尤其是网络不可用或 API Key 无效时。

建议：让 onboarding 复用 `provider test` 的稳定错误映射，至少输出 `连接失败（network/auth/timeout）` 和脱敏的下一步建议；为连接失败增加离线测试覆盖。

### P2 候选：SDK 连接异常分类需增加兼容性测试

停止本地假 Provider 后执行 `provider test local`，本环境显示 `连接失败（internal）`。OpenAI SDK 在当前运行环境返回的异常类型不是现有 `classify_error` 明确覆盖的连接异常类型，因此被归为 `INTERNAL`。

这可能受本机网络代理/SDK 版本影响，尚不能仅凭本地模拟断网断言为确定产品缺陷；但建议补充对 OpenAI SDK `APIConnectionError`、连接拒绝和代理错误的单元测试，并统一映射为 `NETWORK`。

### 未计为问题的观察

- `provider presets` 是静态命令，不需要 `--state-root`；测试初始尝试附加该隐藏选项导致 CLI 参数错误，但 README 的公开用法没有附加该选项，因此不计为产品缺陷。
- 首次 coding 回合中假模型故意漏传 `write_file.mode`，工具正确返回 `invalid_arguments`；这是测试桩错误，不是产品问题。
- 未运行真实 Provider、真实网络、Linux 原生沙箱和真实 Keychain 交互；这些属于本轮隔离离线测试边界，不能宣称通过。

## 5. 结论

本轮模拟用户环境测试通过核心验收：真实终端交互可用，简单 coding 任务可完成，审批与拒绝正确，Session/TaskRun/文件可持久化恢复，备份和 Doctor 一致性正常，路径穿越和 Host 越权尝试 fail closed。

Provider onboarding 的稳定错误分类和用户提示已在下述后续修复中处理；本地 coding、持久化和权限安全主流程的原验收结论不变。

## 6. 后续问题复核与修复

复核日期：2026-08-20。

### 6.1 Provider onboarding 错误信息：确认并修复

根因确认：OpenAI-compatible Adapter 已经把 SDK 错误归一化为带 `ModelErrorCode` 的
`ModelProviderError`，但 `provider add` 和 `provider configure` 的 CLI 通用异常分支只输出异常
类名，导致稳定分类与可操作建议在最外层丢失。

修复结果：

- `provider add`、`provider configure` 和异常形式的 `provider test` 现在展示稳定错误码；
- `provider test` 对持久化失败结果使用同一套脱敏提示；
- `network` 提示包含网络/代理检查建议，`auth`、`rate_limit`、`timeout`、
  `invalid_response` 和 `internal` 也都有稳定下一步建议；
- CLI 不回显 Provider/SDK 原始异常文本，避免把凭据、代理地址或底层实现细节带到终端。

### 6.2 SDK 连接异常分类：部分既有、缺口确认并修复

复核发现，OpenAI SDK 的 `APIConnectionError` 和 `APITimeoutError` 原本可由类名正确分类；真正
缺失的是 `httpx.ProxyError` 等代理传输错误，以及外层异常包装底层连接错误的因果链。这两种情况
会落入 `internal`。

修复结果：分类器现在以有界、防循环的方式检查异常因果链，并将 connection、network、proxy、
transport 与底层 `ConnectionError`/`OSError` 统一映射为 `network`；超时仍优先映射为
`timeout`，认证和限流仍分别映射为 `auth`、`rate_limit`。

新增测试完全离线构造 OpenAI/httpx 异常对象，没有发起网络请求。验证结果：

- Provider、CLI 与架构边界针对性测试：68 passed，1 个显式 live 用例跳过；
- 完整离线测试：683 passed，2 skipped，1 deselected；
- Ruff format/check、compileall、CLI help 和 `git diff --check`：通过。

结论：报告中的确定 P2 已修复；候选 P2 的实际分类缺口也已确认并修复。
