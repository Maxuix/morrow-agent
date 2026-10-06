# Computer-use 真实 Provider 功能测试（2026-10-04）

后续分析：[问题分析与修复方案](computer-use-analysis-and-fix-plan-2026-10-04.md)。补充原生诊断已确认 q/Y 在编辑控件中出现、SwiftUI oracle 尚未提交；下文原始状态数据保留，按键“未及时送达”的推断以该分析修正为准。滚动的正确坐标 SDK 组件诊断也已生效，原真实 Provider 场景仍未通过。

结论：**不能认定所有功能正常**。真实模型工具调用与语义输入链路可用；图片发布、滚动、键盘效果反馈与部分验证仍有未通过项。未修复或升级这些结果为成功。

本轮使用用户指定的 `https://api.deepseek.com` / `deepseek-flash`，实际调用生产 `OpenAICompatibleProvider.stream()`，由模型生成工具名和参数，经普通 `AgentLoop`、授权/审批、执行账本及官方 `cua-driver==0.30.4` 操作本地 Swift fixture。没有用 scripted Provider 生成回复，没有 Browser Use 代替原生 SDK。Provider 包装器仅记录元数据、限制公开工具和请求次数。测试组合使用临时工作区与内存凭据存储，未修改用户的常用 Provider 或默认 computer-use 设置。

环境：macOS 27.0 arm64，SDK 环境 Python 3.13，测试前源码 HEAD `6513077de1d8dc398ca98291277f24235a4b8c69`。fixture 按独立状态文件的 PID/native window 选择，测试自己的三个进程实例；同 bundle 的既有用户 fixture 未操作。每个场景最多一个 SDK 动作，不重试 unknown，不切换投递模式。使用合成文本，secure oracle 只导出 populated Boolean。

30 个场景累计 **121 次真实 Provider stream 请求、19 次 SDK 动作入口、91 条闭合执行账本**。所有 turn 正常结束，没有 Provider failure；这只证明运行链路收尾，不代表动作验收通过。DeepSeek 模型列表及纯文本请求均 HTTP 200；独立合成红色方块图片请求也 HTTP 200，模型返回 Red。没有任何本地窗口图像成功进入本轮模型请求。

## 实测结果

| 功能 | 实际结果 | 判断 |
| --- | --- | --- |
| 发现授权窗口、读取 AX | 模型真实调用 discover/window，返回对应 fixture；前后台均可读取；原生 `elements_complete=false` | 基础读取通过，不能声称完整树 |
| 普通 Unicode 输入 | 前后台 `completed`，独立文本包含本次中文/emoji marker；hybrid 的显式 semantic fallback 也可输入 | 通过 |
| token 单击 | 前后台计数 +1，SDK `unknown/unverified_action`；新观察与账本闭合 | 实际有效，完成状态未通过 |
| press_key(q) | 前后台返回 `completed`，该步骤独立 q_delta=0/text_changed=false | 完成反馈与当步效果不一致，未通过 |
| hotkey(shift+y) | 前后台 `unknown/unverified_action`，该步骤 Y_delta=0 | 未确认及时生效 |
| 后续键盘状态 | 两轮 secure 步骤又出现普通文本 q_delta=1、Y_delta=1 | 与前两步按键标记一致；后续诊断确认原生编辑缓冲已变化而 SwiftUI oracle 未提交，不再据此判断事件迟到 |
| token 滚动 down/3 | 前后台都进入 SDK；`unknown/unverified_action`；独立 scroll_delta=0 | 未通过 |
| 合成 secure 输入 | 前后台 populated false→true，但 SDK 都是 `unknown/unverified_action`；普通文本还出现上述 q/Y 变化 | 真实输入发生，完成与隔离效果未通过 |
| 双击 left/count=2 | 模型实际提交 count=2；1 次 SDK 入口，计数 +1，SDK unknown | fixture 无双击事件 oracle，双击语义未证实 |
| 右键 right/count=1 | 1 次 SDK 入口；SDK `unknown/suspected_noop`；计数无变化 | fixture 无上下文菜单 oracle，未证实 |
| text_appears 后置条件 | 普通输入 completed，postcondition=passed，独立 marker 存在 | 通过 |
| element_exists 后置条件 | 点击实际 +1，postcondition=passed，动作仍 unknown | 条件验证通过，未覆盖动作完成不确定性 |
| attribute_equals(enabled=true) | 点击实际 +1，postcondition=not_checked，verification_error=verification_unavailable | 未通过 |
| 人工审批拒绝 | 模型请求动作，测试审批器拒绝；approval_rejected；SDK 动作入口 0，独立状态无变化 | 通过 |
| semantic 图片权限边界 | 最终 smoke 中模型请求未授权图片，返回 image_share_not_granted；之后动作仍经拒绝审批，不进 SDK | 边界通过 |
| hybrid 截图发布 | 干净窗口明确 include_image=true；SDK image_count=1/frame_valid=true，但工具 preflight_failed/image_safety_unconfirmed | 未通过；不是本次 Provider 不接受图片 |
| 视觉坐标点击/滚动 | 图片发布失败；第一次 coordinate_click 模型改用了 element_ref，不能算坐标测试；coordinate_scroll 提交 x/y 被 unknown_scale/not_started 拒绝；严格图片测试均停止，无 SDK 动作 | 未通过/受阻，未证明坐标动作正常 |
| unknown 不重试、临时生命周期结束 | 每场景 SDK 入口 ≤1，所有执行账本 closed，active_turn=false，shutdown 无报错 | 本轮场景通过 |

## 发现与定位边界

1. 图片链路是当前完整视觉闭环的阻塞项。`hybrid-error-detail.json` 保留明确 `image_safety_unconfirmed` 的工具错误；原生捕获存在，发布为 0，Provider 图像哈希列表始终为空。`session.py`/`projection.py` 的遮罩及内容保护路径可能拒绝发布，但本轮没有绕过保护，也没有确认具体是哪一个区域/投影条件触发。
2. 单键反馈 `completed` 与当步独立状态不一致，组合键及滚动未确认生效。后续 q/Y 变化提示需要调查原生焦点、事件投递和完成/观察时序；目前不能把问题归因于模型参数。前台场景记录了精确 q 和 shift+y 参数。
3. 点击和 secure 输入有效却维持 unknown，符合当前 SDK 保守结果语义，但尚不足以对用户承诺可验证完成。后置条件 passed 不会升级 unknown。属性验证实际返回 verification_unavailable。
4. 当前 `src/morrow/application/computer_tools.py` 的 observe 工具描述仍包含 `Credentials must never be entered.`，与既有撤回输入禁令的范围决定不一致。合成 secure 测试确实进入了 SDK；本轮没有输入真实账号凭据，也没有更改产品提示词。

本轮是功能测试，没有修改生产源码或现有执行计划。取消/撤销、跨 workspace 互斥、故障恢复、所有按键/方向/按钮组合、其他应用和 GUI 人工交互未做真实 Provider 场景穷举；它们的离线覆盖通过，但不应被写成全部实机通过。浏览器应用原生兼容性与打包安装也未重新验收。

## 验证和复现

实际命令结果：定向 `tests/test_computer*.py` 443 passed；全量 `pytest -m 'not live'` **3058 passed / 2 deselected（205.36s）**；GUI **107 files / 724 tests passed**，typecheck 通过；Ruff check 全树通过；compileall(src/tests) 和 CLI help 通过；新脚本 scoped format 与 compileall 通过；新增脚本后又运行相关 native loop/action/verification/permission/image 测试 **73 passed**。

全树 Ruff format **未通过**：用户原有 dirty 文件 `evals/benchmarks/run_tb2.py` 需要格式化。本轮未编辑它，未把全树 format 称为通过。用户原有 staged/dirty 文件保持。

可复现入口：[live_provider.py](../../evals/computer_use/live_provider.py)，命令及 opt-in 说明见 [README](../../evals/computer_use/README.md#real-provider-functional-campaign)。脚本使用已启动的受控 fixture、隐藏密钥输入或环境变量；最终脚本经过真实 observe/denied smoke。密钥只在进程内存中，不在源码、YAML、报告、输出或证据中。临时应用存储与原生 session 均已关闭；本轮创建的最后 fixture 已结束。

本地详细证据：[summary.json](portable/raw/docs/acceptance/assets/computer-use-live-2026-10-04/summary.json)。该 assets 目录按仓库惯例 gitignored；汇总包含每个场景文件及最终脚本 SHA-256。场景原始 JSON 的 `status=tested` 是记录完成，不是验收成功；脚本在测试过程中补充了元数据，最终 smoke 文件明确记录 fixture identity unchanged。早期尚未接入网络的脚本调试结果未计入以上统计。
