# 阶段 5：Skills 与扩展生态

> 状态：未开始  
> 阶段结果：核心保持精简，同时可接入 Skills、MCP 与更多模型服务  
> 累计时间参考：2–4 周（达到带会话、记忆和扩展能力的个人可用版本）  
> 上级文档：[开发路线总览](../ROADMAP.md)  
> 上一阶段：[阶段 4：会话、上下文与记忆](stage-4-sessions-context-and-memory.md)  
> 下一阶段：[阶段 6：自动化与复杂任务](stage-6-automation-and-complex-tasks.md)  

## 一、阶段目标

把变化频繁、面向特定场景的能力放到扩展层，而不是持续膨胀 Agent 核心。用户可以按工作空间选择能力，第三方扩展也有明确的权限和生命周期边界。

## 二、进入条件

- Agent 循环、工具系统和错误模型已经稳定。
- 会话、上下文与记忆具备可靠的持久化边界。
- 工作空间隔离和工具安全策略可以被扩展层复用。

## 三、计划范围

### Skill 机制

- 定义 Skill 的目录结构、清单文件与版本信息。
- 支持发现、加载、启用、停用和卸载。
- Skill 可声明提示词、工作流、工具依赖与权限需求。
- Skill 的启用状态按全局与工作空间分层保存。
- 记录能力来源，便于定位冲突和复现任务。

### MCP 接入

- 提供 MCP 客户端和服务配置入口。
- 支持工具发现、命名空间、超时与错误隔离。
- 明确凭据、授权和工作空间可见范围。
- MCP 工具沿用本地工具的审批与审计规则。

### 模型服务扩展

- 复用阶段 1 已固定的 `ModelProvider`、`ProviderFactory`、动态 Provider ID 和 `ModelRef` 边界。
- 通过注册新的 Adapter Factory、Provider 预设和实例增加 Provider，不修改会话与 Agent 核心。
- 明确能力差异，例如流式响应、工具调用和上下文上限。
- 保留工作空间级模型选择，但不在本阶段建设复杂路由系统。
- 在阶段 1 命名空间上增量提供自定义 Adapter 和模型管理命令：

```text
morrow provider add --adapter <adapter-id> --name <provider-id>
morrow model add --provider <provider-id>
morrow model sync --provider <provider-id>
morrow model show <provider-id>/<model-id>
morrow model use <provider-id>/<model-id>
morrow model remove <provider-id>/<model-id>
```

`model use` 是切换当前模型的唯一命令，不再维护一个可能与模型选择冲突的“默认 Provider”。模型自动路由、择优和故障回退仍需单独验证后再决定是否进入本阶段。

### 扩展治理

- 处理名称冲突、版本兼容与缺失依赖。
- 展示扩展请求的权限及其实际来源。
- 扩展失败时只隔离对应能力，不破坏主进程和已有状态。

## 四、暂不包含

- 托管式公开扩展市场。
- 默认捆绑大量第三方集成。
- 未经确认自动运行不受信任扩展。
- 多 Agent 调度和后台自动化。
- 为不同扩展复制独立的 Agent 核心。

## 五、阶段交付物

- 一套可版本化的 Skill 规范及加载器。
- MCP 客户端、配置方式与安全适配层。
- 额外的模型 Adapter、Provider 预设以及多 Provider、多模型配置管理能力。
- 扩展安装、查看、启用、停用和诊断命令。
- 最少一个示例 Skill、一个 MCP 示例和一个额外 Provider 示例。

## 六、阶段完成标准

1. 用户能创建或安装一个 Skill，并仅在指定工作空间启用。
2. Agent 能连接一个 MCP 服务并调用其工具。
3. 新增模型 Provider 不需要修改会话或 Agent 循环核心。
4. 停用扩展后，其能力立即从当前工作空间消失。
5. 权限、来源、版本和失败原因对用户可见。
6. 一个扩展崩溃不会损坏主状态或阻断其他能力。

## 七、进入阶段时再确认

- Skill 清单格式和兼容策略。
- MCP 客户端库、传输方式与认证方案。
- 扩展代码与声明式工作流的边界。
- 多 Provider 的选择界面与回退规则。
- 本地扩展、可信扩展与未知扩展的信任模型。
