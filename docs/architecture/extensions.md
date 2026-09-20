# 工具、扩展与学习

[架构总览](../ARCHITECTURE.md) · [运行时](runtime.md) · [状态](state.md)

## Provider 与工具组合

[Adapter Registry](../../src/morrow/adapters/registry.py) 与 Provider 服务解析精确模型、能力和
非敏感配置；凭据由 CredentialStore/环境变量提供。
[OpenAI-compatible adapter](../../src/morrow/adapters/models/openai_compatible.py) 负责 SDK/wire、
请求白名单、流片段和错误归一化，不把本地权限元数据加入 Provider 工具协议。
不静默切换 Provider/Model，恢复继续使用经过验证的冻结设置。

生产组合在支持 function tools 时启用 `read`、`ls`、`find`、`grep`、`edit`、`write`、`bash`。
`run_skill_script`、`update_configuration`、`manage_preferences`、`read_artifact` 和
`promote_sandbox_changes` 随能力组合。实际注册与冻结以
[bootstrap.py](../../src/morrow/bootstrap.py) 和
[local_tools.py](../../src/morrow/application/local_tools.py) 为准，不由文档里的固定名单授予能力。

`RegisteredTool` 携带参数验证器、handler、effect/approval 策略与恢复声明。
`ToolExecutor` 统一冻结、预算、intent 预检、审批、执行和结果限制；恢复使用已冻结声明，
不根据今天的工具名字重新猜测。新增工具不得要求 AgentLoop 或 Scheduler 增加名称分支。
纯计算可直接使用本地函数；状态/副作用/外部系统访问通过注入的 service/port 完成，
handler 不读取终端输入、不自行发公开事件、不直接依赖具体 SDK 或模块级可变状态。

## 文件、进程与沙箱

| 能力 | 执行 owner | 边界 |
| --- | --- | --- |
| 读文件、目录、搜索 | WorkspaceFileService / WorkspaceSearchService | 冻结工作空间、no-follow、类型与资源预算 |
| edit/write | WorkspaceMutationService / ChangeSetService | 内部冻结 revision/hash/mode，冲突检查、原子发布和真实 diff |
| bash | ProcessExecutionService | Host 或已探测的 NativeSandbox 后端；有界输出、超时、取消、进程组清理 |
| 沙箱变更推广 | SandboxSnapshotService + mutation services | 当前运行、审批、逐项预检；不承诺整批原子回滚 |
| 只读 Git service | GitInspectionService | 固定只读协议、禁 executable 扩展；模型层通过 bash 操作 Git |

结构化文件工具不因 `.git`、`.env`、PEM 或文件名关键词隐藏普通工作区资源，但仍拒绝路径逃逸、
外部符号链接和不支持类型。编辑拒绝陈旧 hash、模糊多匹配和混合换行；发布依赖临时文件与 fsync。
底层 delete/move/rename 服务仍供推广和恢复使用，不能因模型专用包装退役就删除。
它们只接受受控普通文件、no-clobber 发布和冻结身份；源先被原子捕获再核验，不能证明身份时
保留 staging/unknown 事实并拒绝继续。多路径稳定加锁，失败不覆盖第三方文件。

普通 Host `bash` 无 OS 隔离，不按 Git/管道/重定向等命令字符串猜测审批，也不保证工作空间、
网络或凭据 confinement。Auto Sandboxed 使用默认断网的临时快照；取消/超时先等待准备与收集
停稳再清理。Linux 原生后端在真实 runner 验证前仍 unsupported，不能由二进制存在推断支持。

Full Access Manual 是额外授权证据链：grant 绑定前台 AgentRun 与不可变 permission snapshot，
每次执行仍消费相应审批。撤销阻止新 handler、使 pending approval 失效并请求取消；
既成副作用不能伪造回滚，结构化工具不会因此自动获得 elevated 能力。

## Skills 与 MCP

[application/skills](../../src/morrow/application/skills/) 持有 Catalog、Binding 生命周期、
Selection/Context、Draft、Usage、脚本和完整性检查。可编辑包、发布版本和 AgentRun 冻结选择分离；
Draft accept 发布版本，不等于启用或授予权限。Skill context 不得制造 selection 证据。

`SkillScriptExecutionService` 只运行冻结 managed 包声明的脚本，必须有可用原生沙箱。
不接受任意 shell、不继承 ambient 环境/凭据，argv、输入 Artifact、输出路径和大小有界；
输出经脱敏发布为 Session-scoped Artifact。取消与恢复继续使用统一工具合同。

[application/mcp](../../src/morrow/application/mcp/) 持有 desired state、Catalog、run-scoped
runtime、策略桥接、凭据解析与结果归一化。配置保存只存引用；显式刷新在有界作业中执行，
提交时重验 revision，迟到 Catalog 不能覆盖新配置。stdio 子进程只获得明确声明的环境项。
风险与审批证据绑定 AgentRun、Server、配置、Catalog 和精确工具，MCP 内容本身不构成授权。

## 配置、Preference、Learning 与 Memory

[ConfigPatchService](../../src/morrow/services/profile_configuration.py) 是 Workspace Profile 写入边界；
GUI 整页保存和工具/CLI 单项修改复用 revision/OCC，不新增状态 owner。
[PreferenceWriter](../../src/morrow/application/preferences/writer.py) 是原子 Preference 写入边界，
工具、CLI、管理命令与 Inbox 接受共用它；SQLite batch 保存审计和恢复镜像，Active 状态仍归 YAML。

任务后审阅与用户显式配置区分处理：Learning/Preference Reviewer 使用 no-tool、有界输入和严格
输出模型，推断默认形成候选，不直接修改长期配置。accepted TaskOutcome 的 Learning 请求及
普通 Turn 的 Preference 审阅队列沿各自事务边界接纳，worker 的 lease/retry 不等于 Stage 9 daemon。

[application/learning](../../src/morrow/application/learning/) 持有候选决策、Knowledge 生命周期、
Promotion 和 MemorySelection。Promotion 经 ConfigPatchService 发布 YAML，再以 saga 收口审计、
activation 与 receipt；中断交给原恢复链。MemorySelection 引用精确不可变 Knowledge revision，
新 AgentRun 冻结选择，恢复不重读今天的 Head 代替历史上下文。

[ManagementService](../../src/morrow/application/management.py) 与
[knowledge_management.py](../../src/morrow/application/knowledge_management.py) 是 CLI/GUI 共享
类型化入口，委托上述 owner。Context 管理查询冻结快照，Skill 管理查询真实 Catalog/Usage，
不从已脱敏文本回写原文、不在 GET 中隐式晋升或过期变更。

项目指令在工作空间根按 `AGENTS.override.md`、`AGENTS.md`、`CLAUDE.md` 顺序选择首个可读
普通 UTF-8 文件；失败或超大输入产生警告并跳过，不阻断启动。指令、角色、Preference、Memory、
Skill 正文都属于模型上下文，不得覆盖本地权限与执行策略。

## 兼容与维护边界

测试 fixture、离线评估、迁移和 subprocess 入口不能仅按生产 import 计数退役。
旧 Workflow feedback 自动学习与 paired-benefit 门槛已移除，历史 v44 库中的预留 feedback 表
会在 v45 迁移时删除；
用户编排策略仍由 [OrchestrationPolicyService](../../src/morrow/application/workflows/orchestration_policy.py)
解析。规划、Memory、MCP 等仍有真实写入方的表继续保留，不为追求表数强行合并。

安全细节以当前代码、测试和本架构基线为准；历史选型与实施记录不属于公开运行合同。
