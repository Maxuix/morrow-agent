# Stage 3 实施计划 Review

> 对象：[`.agent/PLAN.md`](../../.agent/PLAN.md) 与 Subplans 29–34
> 对照：[`docs/roadmap/stage-3-local-tools-and-safety.md`](../roadmap/stage-3-local-tools-and-safety.md)、[`docs/ROADMAP.md`](../ROADMAP.md)、[`docs/ARCHITECTURE.md`](../ARCHITECTURE.md)、[`docs/roadmap/stage-4-task-session-and-persistence.md`](../roadmap/stage-4-task-session-and-persistence.md)、当前代码基线
> 日期：2026-08-18
> 结论：**方向与路线图一致，可以成为 Stage 3 的实施合同；但按原文开工会在现有 RunPolicy / System Prompt / 沙箱落地条件上撞墙。有条件批准，先改阻塞项再授权 Subplan 29。**

---

## 0. 一句话判断

这份计划第一次把 Stage 3 写成了可执行合同：权限三维正交、Pi 行为参照、Morrow 安全加硬、工具面很小、Host 不假装隔离、Full Access 留给 Stage 4。路线图要的闭环它都覆盖了，也没有偷偷把 MCP、Skills、持久会话或 Git 写入拉进来。

问题不在“写偏了”，而在**有几条已经锁死的数字和现有代码契约，计划当作互补条件写进去，实际上互相否定**；另外 **Auto Sandboxed 被写成 Stage 3 完成必要条件，但平台 spike 还没做**。这两类问题不先改，后面不是实现细节，是做到一半无法验收。

**建议：有条件批准。保留权限模型、工具面、切片顺序和安全不变量；先补齐结果预算、系统提示、父目录创建、Auto Safe 范围阈值、沙箱超时/工具链，并预先约定 3E 失败时的阶段出口。不收敛就不要启动 Subplan 29。**

---

## 1. 总体结论

| 问题 | 判断 |
|---|---|
| Stage 3 要不要按当前路线做 | 要。顺序正确：先真实本地 Code Agent，再持久化与 Full Access。 |
| 计划方向是否符合 roadmap | **符合。** 目标闭环、3B–3F 切片、权限三维、Host vs 沙箱、只读 Git、排除项都对齐。 |
| 计划能不能原文成为实施合同 | **不能。** 有 5 个开工前必须改的阻塞项。 |
| 是否存在路线越界 | 没有把 Stage 4–10 能力做成生产主线。`ToolRunContext` / `full_access` 枚举是为后续留位，且明确不激活。 |
| 是否存在范围不足 | 有几处路线图要求被弱化或漏写，多数可在计划里补一句，不改方向。 |
| 是否过度设计 | 安全合同偏重，但这是 Stage 3 的产品本身，不是 Stage 2 那种“未来内核”。真正偏重的是 29 纯基础设施先行，以及 3E 把 macOS+Linux+快照+推广一次做完。 |
| 能否按原文做完并验收 | 小 Fixture + Manual/Auto Safe 能做。Auto Sandboxed 作为硬完成条件，存在平台级失败风险。 |

对应实施决策：

- **不批准将当前 PLAN / Subplans 原文直接开工。**
- **批准收敛后的 Stage 3**：做路线图里的本地 Code Agent 闭环，并用现有 `AgentLoop` + 标准 ToolCycle 承载。
- 现行 [`stage-3-local-tools-and-safety.md`](../roadmap/stage-3-local-tools-and-safety.md) 仍是权威范围；计划应往那份基线收，用子计划锁实现合同，而不是反过来用未验证的数字覆盖基线。

---

## 2. 与路线图的对齐

### 2.1 对上了的主线

这些是计划写得对、而且应该保留的部分。

| 路线图要求 | 计划落点 | 判断 |
|---|---|---|
| 定位 → 读取 → 搜索 → 修改 → Diff → 验证 → 修正 → 如实报告 | `PLAN.md` 目标闭环；Subplan 34 两条 Fixture 故事 | 对齐 |
| 3A 已完成，不重做 | 基线承认通用策略、审批、配置工具 | 对齐 |
| 3B 路径安全 + list/read/find/search | 29 打策略地基，30 注册只读工具 | 切片拆分合理 |
| 3C 精确补丁、受控写入、实际 Diff、冲突不覆盖 | 31：SHA-256 权威、唯一匹配、原子发布、`show_changes` | 对齐且比路线图更硬 |
| 3D Host 进程、超时取消、项目代码不自动跑 | 32：Host 一律审批；分类器不当成隔离 | 对齐，且更严格 |
| 3E 原生沙箱 + 临时快照 + 无 Host 回退 | 33：Seatbelt / bubblewrap，无 Docker，fail closed | 对齐 |
| 3F 只读 Git + 完整验收 | 34：porcelain-v2、禁 textconv/hook、证据矩阵 | 对齐 |
| 权限三维 + 三个工作区预设；Full Access 不激活 | Locked product decisions | 对齐 4.5 / 第八节 |
| `AgentLoop` / Executor / Orchestrator 不按工具名分支 | Target dependency direction | 对齐 4.1 与架构基线 |
| 工作空间是能力边界，不是默认目录 | Workspace filesystem contract | 对齐 4.2 |
| 修改必须可见、可验证、区分已修改/已验证 | ChangeSet + CommandResult + 诚实终态 | 方向对齐；见第 3 节缺口 |
| 删除/重命名/chmod/链接、Git 写入、网络、MCP、Skills、持久历史不进本阶段 | Hold points + 各子计划排除项 | 对齐第九节 |
| 公开事件不泄漏完整参数/结果/密钥 | 不改 `tool.status` 生命周期；内部 `ToolFact` | 落在路线图 5.7 的“或内部审计”分支，可接受 |
| Pi 作行为参照，不抄安全模型 | 固定 commit `209bc7b`；拒绝绝对路径、模糊补丁、Host 自动跑 | 对齐 4.6 |
| 离线测试为默认门禁 | Fake Provider、注入 adapter、不 sleep 断言 | 对齐 |

计划把路线图 4.3 留下的开放题也答了：不扩展 `ToolEffect` 当授权引擎，另建 `OperationKind` + `CapabilityPolicy`。这正是“激活 3B 前必须决定”的那件事。

### 2.2 路线图有、计划弱化或漏写的点

这些不是方向错误，但实施合同不补的话，后面会按不同理解实现。

| 路线图 | 计划现状 | 影响 |
|---|---|---|
| 5.2 搜索方案“必须通过 ADR 固定” | 直接锁成“已安装 `rg` + Python fallback + 奇偶语料”，无 ADR | 决策本身合理，缺正式记录 |
| 5.2 尊重用户配置的 ignore | 只用内部排除（`.git`、venv/cache/build）；明确不把用户 ignore 当逃逸授权 | 更安全，但搜索体验与 `rg` 默认 `.gitignore` 行为未锁 |
| 5.2 搜索要有耗时/总字节预算 | 只锁了条数/匹配数/单文件 8 MiB | 大仓库 Python fallback 可能挂死在 120s 工具超时里 |
| 5.3 / 第八节：整文件覆盖与**大范围修改**都要强确认 | Auto Safe 自动允许“bounded exact patch”，阈值未定义 | 可能把大补丁当小补丁自动写入 |
| 5.3 `show_diff` 读实际工作树差异 | `show_changes` 只看本轮 ChangeSet；工作树差异放到 3F `git_diff` | 能力可拼起来，但本轮未提交前用户怎么看 Diff 未写清 |
| 4.4 / 第八节：审批前看预览，任务后看实际 Diff | 31 的预览只有 path / operation / **diff stats** / 风险 | 结合现有 8 行预览上限，用户可能在没看见补丁的情况下批准写入 |
| 5.1 新文件最近父目录必须在工作区内 | 有校验，**没说是否创建中间目录**；也没有 `mkdir` | 无法在不存在的包路径下创建新文件 |
| 5.5 复杂 shell 优先 `argv[]`，shell 提升为高风险或默认拒绝 | 对等接受 `argv[]` 或 shell 字符串 | 略松；应用分类器兜底，但默认面更宽 |
| 5.5 可选 `run_tests` 薄封装 | 只有 `run_command` | 可接受 |
| 5.6 可选最近提交信息 | 未做 | 可接受 |
| 5.7 审计字段：风险、是否审批、相对路径、耗时、退出码、ChangeSet 引用 | `ToolFact` 只写“有界脱敏元数据”，字段未锁 | Stage 4 持久化时可能发现模型不够 |
| 第十二节 Windows / macOS / Linux 平台差异与 CI 补测 | 只谈 macOS Seatbelt 与 Linux bubblewrap；路径合同是 POSIX 相对路径 | Windows 是未声明的非目标，应写明 |
| 第十三节与总览 11：从 Stage 3 开始采集本地可关闭指标 | 计划无指标 | 不挡闭环，但和总览不一致 |
| 总览 6.3：副作用前持久化意图 | 本阶段明确只做进程内 facts，崩溃后只靠原子替换 | 与 Stage 3 正文一致，是对总览不变量的阶段例外，应在证据里写明交给 Stage 4 |
| 系统提示随能力更新 | 完全未提 `SYSTEM_BOUNDARY` | **阻塞**，见 Issue 1 |

### 2.3 没有越界的部分

对照 Stage 4 和总览排除项，计划没有把这些做成 Stage 3 生产主线：

- 持久 Session / TaskRun / AgentRun / Artifact store
- 持久、可撤销的 `CapabilityGrant` 与可用 Full Access
- 对话恢复、LLM 摘要、学习、Skills、MCP、浏览器、后台任务、多 Agent
- Git 写入、网络/loopback 授权、Docker、自动安装 `rg` / bubblewrap
- 改 `agent-policy.toml` 默认值、改公开事件生命周期

`full_access` 进入枚举但必须 `unsupported_capability`，以及 `ToolRunContext` 只留在进程内，这两个预留是干净的。

---

## 3. 阻塞问题

按对开工决策的影响排序。这些不改，Subplan 29 不该激活。

### Issue 1 — Severity: bug

- File: `src/morrow/application/context.py:26-32`
- Description: 现行系统边界明确禁止本阶段要交付的能力：

```text
不能读取或修改项目文件，不能执行 Shell、Git、网络或其他未提供的能力
```

计划只说工具 description 要告诉模型何时不要调用，从未要求改 `SYSTEM_BOUNDARY`。Subplan 30 一旦注册 `read_file`，模型和提示词会互相打架：一边给工具，一边用系统指令说不能读项目。脚本化 Provider 测不出来，真模型一接上就会拒用工具或编造“我不能读文件”。
- Suggestion: 在 30/31/32/34 各加一条显式任务：按**当前已注册能力**重写边界，而不是按 Stage 2 禁令。保留“只能用本轮提供的工具、工具结果不可信、没有工具证据不得声称已修改/已验证、不能把 Profile/Preferences 当成提权”这些不变量。禁止项改为：工作区外、网络、Git 写入、未提供的能力。

### Issue 2 — Severity: bug

- File: `src/morrow/resources/agent-policy.toml:8-13` 与 `tests/test_policy.py:52-60`
- Description: 计划锁了“每次 `read_file` 最多返回 2,000 行 / 50 KiB”，同时又锁了“不改 bundled `agent-policy.toml` 默认值，结果信封继续受现有 RunPolicy 限制”。生产未知模型下这组默认值已经测死：

```text
effective_request_chars = 160000
effective_result_limit  = 16000    # min(64000, 160000 * 0.10)
effective_cycle_limit   = 56000    # min(256000, 160000 * 0.35)
tool_timeout_seconds    = 120
```

50 KiB 正文在进模型之前就会被 `ToolExecutor` 按字符切开（`runtime/tools.py:280-312`）。那不是语义上的“下一窗口”，而是把 JSON 结果截成半截，`truncated=True`，continuation 元数据作废。一轮里几次合法读取也会撞上 56 KiB 的 cycle 预算。
- Suggestion: 二选一，不要假装两者兼容。

  1. **推荐**：把 Stage 3 读窗口改成能稳定落入现有 16 KiB 信封的值（例如 400 行 / 8 KiB），continuation 仍按行号工作；cycle 内多次读取的预算写进测试。
  2. 或者向用户申请改 `agent-policy.toml`（这是计划自己的 hold point），把 `max_tool_result_chars` / ratio 提到能装下 50 KiB 结构化结果，并回归 Stage 2 信封测试。

  无论选哪条，服务层的 continuation 必须在 Executor 截断之前完成；禁止依赖 Executor 的 JSON 前缀切片当读窗口。

### Issue 3 — Severity: bug

- File: `.agent/PLAN.md`（Auto Safe / “exact bounded patches”）与 `.agent/subplans/31-stage3-file-mutation-diff.md` S3.31.4
- Description: 路线图第八节把“新文件、精确小范围 Patch”和“整文件覆盖、大范围修改”分成两档。后者在 Manual / Auto Safe / Auto Sandboxed 都是强确认。计划把 Auto Safe 写成“结构上可强制的精确补丁自动执行”，但没有锁：

  - 单文件最大补丁字节数 / 行数
  - 一次 `apply_patch` 的最大 edit 数
  - 什么叫“大范围”（例如超过 N 行、超过文件的 M%、或多文件）
  - `write_file(mode=create)` 的内容上限

  按原文，模型可以用一组“每段都唯一”的大补丁在 Auto Safe 下重写整个文件，而不走 replace 审批。这不是实现细节，是权限矩阵被掏空。
- Suggestion: 在主计划锁一组保守数字，例如：自动补丁单文件 ≤ 64 行或 ≤ 4 KiB 净变更、≤ 8 条 edit；超限或 `write_file(replace)` 一律 `require_approval`。用测试钉死“刚好低于阈值自动通过 / 刚好超过必须审批”。

### Issue 4 — Severity: bug

- File: `.agent/PLAN.md` Workspace filesystem contract；`.agent/subplans/31-stage3-file-mutation-diff.md` S3.31.1
- Description: 新路径只要求“最近已存在的父目录在根内且无符号链接分量”。没有 `mkdir` 工具，也没说 `write_file` 是否创建中间目录。真实编码任务经常是 `src/pkg/mod.py` 而 `pkg/` 尚不存在。按原文这会变成 `invalid_path` / `not_regular_file`，Fixture 故事 1 若要加测试文件也会卡住。
- Suggestion: 锁其中一条，不要留给实现时发挥：

  1. `write_file(create)` 可以创建有界中间目录（仍在根内、无符号链接、有深度上限），ChangeSet 把新建目录记为附属 create；或
  2. 增加极窄的 `create_directory`，默认 Manual 审批、Auto Safe 对空目录自动允许。

  推荐 1，少一个工具。无论哪条，都要有符号链接父目录和逃逸测试。

### Issue 5 — Severity: bug

- File: `.agent/subplans/33-stage3-native-sandbox.md`；`src/morrow/resources/agent-policy.toml:6`
- Description: Stage 3 完成标准第 6、7 条和计划 DoD 第 10、11、17 条要求 Auto Sandboxed 在真实后端上证明隔离。但：

  1. 工具超时是 120 秒且不能改。快照拷贝 + 启动沙箱 + 跑测试 + 算 Diff 都算在同一次 handler 里。对真实仓库，光拷贝就会超时。
  2. 快照排除 venv / cache / build / VCS objects 是对的，但验收故事是 `uv run pytest` 一类项目命令。没有只读绑定真实 `.venv` / toolchain、或 Fixture 纯标准库，沙箱里的测试会因缺依赖失败。
  3. macOS 上 `sandbox-exec` 仍是 agent 常用入口，但 profile 维护成本高，且计划把“证明不了就整阶段阻塞”写成默认。

  33.1 的 spike 方向对，可是**没有预先约定失败出口**。若 Seatbelt 不能同时满足“能跑 Python 测试 + 禁网络 + 禁 Home + 真实工作区不可写”，Stage 3 会在 29–32 全部做完后无法收口。
- Suggestion:

  - 快照必须用 APFS `clonefile` / 硬链接或等价 CoW，禁止默认整树字节拷贝；准备时间单独计，且必须远小于 120 秒。
  - 锁 toolchain 策略：系统解释器与项目 `.venv` 只读绑定，或验收 Fixture 只依赖标准库。
  - 在授权 29 之前先定 3E 出口：平台 spike 失败时，是（a）Stage 3 只验收 Manual + Auto Safe，Auto Sandboxed 标为明确不支持；还是（b）整阶段保持阻塞。不要等 33 才问。

---

## 4. 重要但不阻塞开工的问题

### Issue 6 — Severity: suggestion

- File: `src/morrow/runtime/tools.py:26-28`；`.agent/subplans/31-stage3-file-mutation-diff.md` S3.31.4
- Description: 审批预览最多 8 行、每行 200 字符。这装得下路径、操作、哈希、风险和 Host 警告，装不下 unified Diff。路线图要求“写入前生成预检摘要，用户可在审批前看预览”。stats-only 预览等于让用户在 Manual 下盲批写文件。
- Suggestion: 预览预算是代码常量，不是 `agent-policy.toml`。为 mutation 提高到可展示有界 Diff（例如 40 行 / 4 KiB），并测试截断标记。Host 命令预览保持短文本。

### Issue 7 — Severity: suggestion

- File: `.agent/subplans/34-stage3-git-and-acceptance.md` S3.34.2
- Description: 最终生产工具清单把 Stage 2 演示工具 `lookup_record`、`calculate` 和配置工具一起锁死。Code Agent 每个请求都要带着套餐价格/税率 schema，占上下文，也干扰选工具。路线图没要求保留它们。
- Suggestion: 不要把演示工具写成 Stage 3 终态。30 或 34 把它们从生产注册拿掉，测试改为显式夹具注册。`update_configuration` 可留。

### Issue 8 — Severity: suggestion

- File: `.agent/subplans/33-stage3-native-sandbox.md` S3.33.5
- Description: 沙箱改动不自动推广是对的。但“模型必须再用 `apply_patch` / `write_file` 一条条写回”会把快照 Diff 再手抄一遍，冲突和漏改概率高。路线图要的是受控 ChangeSet 路径，不是禁止任何推广工具。
- Suggestion: 增加极窄的 `promote_sandbox_changes`，只接受快照里已记录的文本 create/modify 子集，内部走 31 的冲突安全服务，默认仍要审批。不要做无差别 bulk apply。若坚持不加工具，至少把“模型手抄”写成已知产品限制，并在 Fixture 里测失败恢复。

### Issue 9 — Severity: suggestion

- File: `docs/ROADMAP.md` 第十一节；`docs/roadmap/stage-3-local-tools-and-safety.md` 第十三节
- Description: 总览要求从 Stage 3 开始采集本地、可关闭、可导出的运行指标。计划的验收是证据矩阵和命令输出，没有指标面。
- Suggestion: 34 加最小进程内计数即可：任务成功/失败、工具失败、审批拒绝、超时、取消、修改后测试是否通过。不上传、不持久化也可以，但不要假装路线图没写过。

### Issue 10 — Severity: suggestion

- File: `.agent/subplans/30-stage3-read-search-tools.md` S3.30.3
- Description: `rg` 的固定 argv 未锁。默认 `rg` 会读 `.gitignore` / `.rgignore`，这通常是对的，但与“不把用户 ignore 当授权”需要写清：ignore 只能少看文件，不能让匹配路径逃出根。也没锁 `--hidden`、`--follow`（不应 follow）、超时、以及 Python fallback 的总字节/时间预算。
- Suggestion: 锁一份 argv 和 fallback 预算（例如 10 秒、32 MiB 扫描上限）。奇偶语料覆盖字面量、简单正则、大小写、glob；超集差异靠结果里的 `engine` 字段暴露，不要藏。补一行 ADR 或主计划段落，满足路线图 5.2。

### Issue 11 — Severity: suggestion

- File: `.agent/subplans/34-stage3-git-and-acceptance.md` S3.34.1；`src/morrow/services/workspace.py:263-269`
- Description: 当前工作空间在存在 Git 时会把身份规范到 git root，所以 `WorkspaceIdentity.path` 通常等于仓库根，git 工具从根起步是自洽的。仍缺两道边界：

  1. worktree 的 `.git` 文件指向工作区外的主仓库对象目录；
  2. `run_command` 里的 `git --git-dir` / `-C` 绝对路径。

  Host 模式下用户批准即接受非隔离，这点计划写清楚了。但 Git 只读工具必须自己保证：只检查工作区内的工作树，禁用可执行扩展，不把区外对象当普通文件读出。
- Suggestion: 34 明确 `git_dir` / worktree 解析失败或落在根外时返回 typed 结果；32 的分类器拒绝 `--git-dir`、`--work-tree` 和绝对 `-C`。

### Issue 12 — Severity: suggestion

- File: `docs/roadmap/stage-3-local-tools-and-safety.md` 5.7
- Description: 不改公开 `tool.status` 是合理选择（现有 payload 白名单在 `core/events.py:64-78` 钉死了）。但内部 `ToolFact` 字段没锁，终端也不展示 ChangeSet。用户可见性完全依赖模型最终陈述 + `show_changes`。脚本化 Provider 可以“诚实”，真用户在 Auto Safe 自动打补丁后，终端只看到 `↳ 工具步骤：apply_patch`。
- Suggestion: 锁最小 `ToolFact` schema（kind、相对路径、operation、revision、diff 截断、command class、exit/signal、duration、redacted、approval verdict）。Session 保留最近一轮 facts 之后，终端在 `turn.completed` 打一行有界变更摘要。公开事件仍不带 Diff/密钥。

### Issue 13 — Severity: nit

- File: `.agent/PLAN.md` Provider-visible tool surface
- Description: 路线图允许工具改名，计划锁了具体名字，这是好事。但 `list_directory` 会撞上 `test_stage_boundary.py` 里基于子串的 `FORBIDDEN_TOOL_KEYWORDS`（含 `"file"`、`"git"`、`"run_command"`）。计划说要原子更新边界测试，没说必须放弃子串禁用表。
- Suggestion: 改成精确允许清单 + 仍然禁止的能力族（network/browser/mcp/skill/commit/push/delete_path）。不要在旧关键字表上打补丁。

### Issue 14 — Severity: nit

- File: `.agent/subplans/29-stage3-policy-workspace-foundation.md` S3.29.6
- Description: 权限预设冻结在进程内 Session，模型 / Preferences 不能改，这是对的。缺一条测试：`update_configuration` 与 Preferences schema 没有 permission/mode 字段，自然语言配置不能把 `manual` 写成 `auto`。
- Suggestion: 29 加一条 ConfigPatch 拒绝测试即可。`Preferences` 目前只有 language / response_detail / instructions，本来就没有该字段，用测试锁住。

---

## 5. 过度设计与可以简化的地方

和 Stage 2 审批稿不同：这里变重的部分大多就是 Stage 3 要卖的东西。不建议再砍权限三维、路径真实解析、冲突哈希、进程树清理或原生沙箱合同。

真正偏重、可以收的是实施形状：

### 5.1 Subplan 29 不要做成又一次“先内核、后切片”

29 有 7 个任务，生产工具面零增加。策略引擎对 Stage 3 是必要的，但完整 Manual / Auto Safe / Auto Sandboxed 真值表在还没有 `run_command` 和沙箱后端时就会开始腐。

更简单的做法：29 只迁现有三个工具、冻结 `PermissionProfile` / `WorkspaceCapability`、打通 `validate → intent → verdict → approve → handler → facts`。Auto Sandboxed 的 process=allow 行等到 33 再钉死；29 只保证该预设 fail closed。

### 5.2 同一文件的 mutation lease 可以先不做通用并发运行时

当前 `ToolExecutor` 是串行的。同文件串行在今天是自动成立的。keyed async lease 是给“未来并发 executor”的预留。Stage 3 需要的是：取消时清掉自己的临时文件，失败不删原文件。一个按路径持有的实现锁就够，不必先设计并发租约协议。

### 5.3 不要同时把 Linux bubblewrap 当成首个验收门

开发机是 macOS。计划要求“声称支持的平台必须有真实逃逸测试，缺失不能算 skip 通过”，这是对的。但 33 把 macOS 和 Linux 写进同一个完成标准，等于没有 Linux CI 就不能结束 33。

更简单：33 先做当前宿主后端 + 在所有平台上测规则/argv 生成。Linux 实跑列为“有 runner 才声称支持”。不要让第二平台变成 Stage 3 的隐式 CI 依赖。

### 5.4 SensitiveResourcePolicy 保持小而硬

路径名 + 明确模板例外 + 私钥魔数头，这个范围对。不要在 29/30 做成通用 DLP：不要读项目文件来构建脱敏词典（计划已禁止，保持住），不要对任意高熵字符串做猜测性脱敏。Host 输出只用 CredentialStore / 环境里的精确值 + 很少的 token 模式。

---

## 6. 按子计划的执行风险

| 子计划 | 与路线图切片 | 主要风险 | 未改计划就开干会怎样 |
|---|---|---|---|
| 29 策略与工作区地基 | 3B.1（路线图未单列，但是 4.3/4.5 的前置） | 抽象政策与现有静态 `ToolApproval` 双轨；真值表超前 | 能写出很多测试，30 才发现 intent 形状不对 |
| 30 读/搜 | 3B | 结果预算撞 16 KiB；系统提示仍禁止读文件；`rg` argv 未锁 | 工具注册了，模型不用，或读窗口被切碎 |
| 31 修改/Diff | 3C | Auto Safe 阈值、父目录、预览看不见 Diff | 权限矩阵和“可见修改”名存实亡 |
| 32 Host 进程 | 3D | 与 120s 超时的关系清楚；分类器可能被写成“隔离证据” | 计划已警告，保持住即可 |
| 33 原生沙箱 | 3E | 平台、超时、工具链、快照体积 | 最大进度风险；可能卡住整阶段 |
| 34 Git 与验收 | 3F | 演示工具残留、指标缺失、证据把 skip 写成 pass | 文档会好看，产品面仍像 Stage 2+文件补丁 |

切片顺序 29→34 是对的：先有通用策略，再只读，再写入，再 Host，再沙箱，最后 Git 和验收。不要为了“先看到读文件”而跳过 29；但 29 要收薄，见 5.1。

每个实现子计划开头对照固定 Pi commit 记“借用 / 加硬 / 拒绝”，并写进最终证据矩阵，这点应保留。

---

## 7. 与当前代码的接缝

计划对基线的描述是准确的：`AgentLoop.run_task()` 唯一写聊天历史、`ToolSet` 冻结、生产三个工具、`ToolEffect` 三值、`test_stage_boundary.py` 拒绝 Stage 3 工具名、公开 `tool.status` 白名单固定。下面是计划没写进任务、但一改代码就会碰到的接缝。

| 接缝 | 现状 | 计划必须补的动作 |
|---|---|---|
| `RegisteredTool.handler` 只返回可 JSON 的对象 | `runtime/tools.py:272-273` 直接 `_dump({"ok": True, "result": result})` | 29 要改 outcome 形状：payload + 本地 facts，且 facts 不得进 Provider 信封 |
| 审批仍是注册时的 `never\|required` | `runtime/tools.py:233` | 29 必须在迁完三个旧工具后删除这条静态分支，否则新工具的动态策略是摆设 |
| `ToolApprovalRequest` 只有 `call_id/effect/preview` | `core/models.py:157-164` | 扩字段可以，但不要把 raw arguments 放进去；`TerminalApprovalPort` 要同步 |
| `build_session_application()` 不接收权限预设 | `bootstrap.py:114-191` | 29 要从 CLI 冻进 executor，测试里的 session factory 都要能注入 |
| `WorkspaceIdentity.path` 在有 Git 时是 git root | `services/workspace.py:263-269` | 文件根 = 身份路径，不要再解一层“启动时的 --dir 子目录” |
| `relink` 是独立 CLI，不会改正在跑的 Session | `interfaces/cli.py:218` | 保持“能力在构造时冻结”；不要在 REPL 里热更新根路径 |
| `Session.read_only` 今天只表示 Profile 损坏 | `interfaces/cli.py:78-98` | 与“只读能力求交”兼容；不要发明第二种只读标志 |
| 演示工具仍由 `_default_tool_executor` 注册 | `bootstrap.py:67-73` | 见 Issue 7 |
| 边界测试用子串禁词 | `tests/test_stage_boundary.py:25-54` | 见 Issue 13 |

---

## 8. 安全阅读：计划本身的质量

安全合同整体是认真的，明显强于“再包一层 shell”。应明确保留：

- 真实路径 + 符号链接分量检查，而不是字符串 `startswith`
- 变异路径拒绝符号链接分量；读路径仅当最终目标仍在根内
- 新路径在发布前重新校验父目录；swap/race 必须用区外 sentinel
- SHA-256 冲突，不做 last-write-wins，不做模糊匹配
- 同目录临时文件、`fsync`、原子替换；失败只删自己的临时文件
- Host 预览必须写明非沙箱、可访问区外/网络
- 沙箱无网络、无 Home/凭据/socket、真实工作区不可写、无 Host 回退
- 快照排除受保护凭据文件；不读这些文件来做脱敏词典
- Git 关掉 pager / ext-diff / textconv / prompt / fsmonitor
- 分类器测试不得写成操作系统隔离证据

残留攻击面是计划承认的，不是漏洞：被批准的 Host 项目代码仍是当前用户。文档和预览必须一直这么说。不要在 README 里写成“Auto Safe 已沙箱化”。

`SensitiveResourcePolicy` 对 `.env` 变体、私钥文件名和魔数头是对的。风险在两边：漏掉项目自己的 `secrets.yaml`，或把 `.env.example` 之外的模板误杀。保持显式例外表，不要上宽后缀启发式。

---

## 9. 保留 / 修改 / 删除 / 延后

### 保留

- 阶段目标：Pi 级编码工具体验 + Morrow 级工作区安全
- 三维权限 + 三个工作区预设；`full_access` 只占位
- 小工具面：list/read/find/search、patch/write/`show_changes`、`run_command`、只读 git
- 通用 `CapabilityPolicy`，Intent 由参数决定，而不是按工具名写死审批
- SHA-256 修订、唯一精确编辑、原子发布、任务内 ChangeSet
- Host 与沙箱两个后端、同一 `CommandResult`
- 快照内自动跑项目命令；推广走冲突安全写路径
- 不改公开事件生命周期，不改 bundled policy 默认值（除非 Issue 2 选择改预算）
- 离线 Fake Provider 验收；Live 单独授权
- 串行子计划；每个切片原子打开对应工具名

### 修改（开工前写入计划）

- 更新 `SYSTEM_BOUNDARY` 任务
- 让读窗口与 `effective_result_limit` 一致
- 锁 Auto Safe 的“小补丁”阈值
- 锁 `write_file` 创建中间目录的语义
- 锁沙箱快照性能/工具链，以及 3E 失败出口
- 扩大 mutation 审批预览，使之能看见有界 Diff
- 把 `ToolFact` 字段和终端变更摘要写成最小合同
- 锁 `rg` argv、fallback 预算，并补 ADR 一段
- 边界测试改为精确允许清单

### 删除或不要锁进终态

| 项 | 去向 |
|---|---|
| 生产注册 `lookup_record` / `calculate` | 退出 Stage 3 终态清单 |
| 为未来并发 executor 设计的通用 mutation lease 协议 | 收成实现细节 |
| 29 里完整的 Auto Sandboxed 进程允许表 | 移到 33 |
| 把 Linux 实跑当成 33 的硬门禁 | 有 runner 再声称支持 |
| 无阈值的“精确即安全” | 换成带数字的 Auto Safe |

### 延后（符合路线图，不要提前做）

- Full Access / `CapabilityGrant`
- 副作用前的持久意图日志
- 公开事件流式 stdout
- loopback、网络、Docker、TTY、sudo
- Git 写入、delete/rename/chmod/link
- 通用撤销系统
- 指标上传或 Operational Store

---

## 10. 建议的收敛后实施形状

目标不变：在指定工作空间里安全地定位、修改、验证，并如实报告。

1. **先改计划，再授权 29。** 把第 3 节五条写进 `PLAN.md` 和对应子计划。用户明确选择 3E 出口（沙箱做不到时，是降级完成还是整阶段阻塞）。
2. **29 只打通通用路径。** 现有三工具迁到 intent/verdict；冻结 manual 默认预设；`auto-sandboxed` fail closed；facts 管道存在但还没有领域事实。
3. **30 给出第一条产品垂直切片。** 改系统提示；读窗口符合 16 KiB（或先获准改政策）；Fake Provider 走 list → search → 分窗读取 → 解释；逃逸/符号链接/受保护文件测试必须红过再绿。
4. **31 让修改可见。** 阈值化 Auto Safe；create 可建中间目录；审批能看见有界 Diff；冲突与原子失败测试按路线图 11.4。
5. **32 只在审批后跑 Host。** 预览带非隔离警告；超时/取消清进程树；非零退出是普通 `CommandResult`。
6. **33 先 spike 再写适配器。** 当前宿主证不实就不启用 CLI 预设。快照用 CoW；toolchain 策略先写进计划。
7. **34 收口。** 只读 Git、两条 Fixture、真实终端 + Scripted Provider、包和秘密扫描。演示工具退出生产清单。没有后端的平台写 `unsupported`，不写 pass。

不要先把 29 写成小型操作系统，最后才在 34 第一次把它们串起来。30/31/32 已经有切片验收，这点比 Stage 2 的九模块合同健康，保持住。

---

## 11. 最终建议

Stage 3 应该做，这份计划的方向也值得做。它没有重复 Stage 2 审批稿那种“用未来内核替换当前闭环”的错误；安全边界和路线图是同一套东西。

它现在还不能当开工合同，因为：

1. 现有系统提示禁止它要交付的能力；
2. 现有 RunPolicy 会切碎它承诺的读窗口；
3. Auto Safe 的“有界”没有数字，权限表会被精确大补丁绕开；
4. 没有创建中间目录的合法路径；
5. Auto Sandboxed 被写成完成门禁，却还没有平台证据和超时/工具链方案。

**有条件批准 Stage 3 开工，不批准本文原计划直接激活 Subplan 29。** 条件就是第 3 节和第 9 节的收敛。收敛之后，这会是一份比路线图更可执行、且没有越界的实施合同。

不需要再写一份新的大设计。在现有 `PLAN.md` 和五个相关子计划上补齐上述合同即可。
