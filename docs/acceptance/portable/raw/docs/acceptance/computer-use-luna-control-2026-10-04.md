# Computer-use：Luna max 实时控制复测报告

本次由 Codex 子代理 `gpt-6-luna`、`reasoning_effort=max` 实时读取 Morrow 的消息、工具 schema 和实际截图，逐次决定 `computer_observe` / `computer_action` 调用。长文本追加和前台语义点击已确认产生真实效果。其他场景的最终结果见下表；不能据此宣称全部功能通过。

测试代码基线：`35a492162b703da7286eac7d74a867c99bfbfc90`；官方 `cua-driver 0.30.4`。本次没有改动 Morrow 生产代码、没有使用 DeepSeek，也没有把动作序列预先写进 ScriptedProvider。

调用路径：Luna 实际推理 → 手工、逐次 IPC receipt → Morrow `ModelProvider` 流 → 普通 `AgentLoop` / `ToolExecutor` → 官方 computer-use SDK → 独立 AppKit fixture。IPC 仅转发模型决定、记录消息和图片；它不选择元素、坐标或动作。Luna 通过 Codex 运行，**不是 Morrow 原生 Luna API adapter 的集成测试**。Morrow 的外部 HTTP adapter 请求数为 0，不代表 Luna 没有实际推理。

每案启动独立 fixture，按真实 PID、窗口 ID 和 process birth 绑定；delivery 在启动前冻结。控制代理可见工具结果和真实 Artifact 图片，不能读取独立状态 oracle。原始 request、receipt、图片与 native-entry 记录均保留，request SHA 与 receipt 绑定。

结果口径：实际效果通过与 SDK `completed` 分开。`unknown` 不重投；`stale_observation/not_started` 在未进入 SDK 时可以重新观察后恢复。unsupported 的准确拒绝只说明能力边界检查正常，不表示该功能可用。旧判定器的原始 failed/blocked 标签保留，不覆盖成 passed。

共13类场景：**5项实际效果通过，3项负例行为符合预期，4项能力用例未通过，1项被选择过期阻塞而未完成动作测试**。这不等同于所有动作都获得SDK完成确认。

| 场景与原始证据 | 最终判断 | 关键证据 |
| --- | --- | --- |
| [4097字符输入框追加](/Users/ruirui/Documents/Project/Agent/developing/docs/acceptance/assets/computer-use-luna-control-2026-10-04/phase-3/long-text-4097/evidence.json) | 效果通过；SDK completed | 2次工具尝试，首次stale/not_started；原生入口仅1。4097→4113，suffix恰好1次，普通字段正确。 |
| [前台语义元素点击](/Users/ruirui/Documents/Project/Agent/developing/docs/acceptance/assets/computer-use-luna-control-2026-10-04/phase-3/semantic-click/evidence.json) | 效果通过；SDK unknown | 原生1次；Count +1，按钮回调 +1；状态可成功导出，NaN坐标保留为未知。 |
| [前台元素双击](/Users/ruirui/Documents/Project/Agent/developing/docs/acceptance/assets/computer-use-luna-control-2026-10-04/phase-4/foreground-double-click/evidence.json) | 效果通过；SDK unknown | Luna输入element_ref/count2；原生1次，clickCount=[1,2]，Count +2。 |
| [前台元素右键](/Users/ruirui/Documents/Project/Agent/developing/docs/acceptance/assets/computer-use-luna-control-2026-10-04/phase-4/foreground-right-click/evidence.json) | 效果通过；SDK unknown | 原生1次；rightMouse +1，截图出现fixture菜单，未重投。 |
| [后台坐标滚动](/Users/ruirui/Documents/Project/Agent/developing/docs/acceptance/assets/computer-use-luna-control-2026-10-04/phase-2/background-coordinate-scroll/evidence.json) | 效果通过；SDK unknown | Luna从图像选(500,700)；原生1次，3个wheel事件均在独立滚动区内，offset +60，图像row0→row4。 |
| [明确拒绝审批](/Users/ruirui/Documents/Project/Agent/developing/docs/acceptance/assets/computer-use-luna-control-2026-10-04/denied/evidence.json) | 负例行为正确 | 实际approval_rejected，approved=false且call ID匹配；原生0次、状态未变。 |
| [审批通道异常](/Users/ruirui/Documents/Project/Agent/developing/docs/acceptance/assets/computer-use-luna-control-2026-10-04/approval-unavailable/evidence.json) | 负例行为正确；原判定failed | 实际needs_approval，无明确拒绝decision；原生0次。没有误判为“明确拒绝通过”。 |
| [故意省略enabled后置条件](/Users/ruirui/Documents/Project/Agent/developing/docs/acceptance/assets/computer-use-luna-control-2026-10-04/postcondition-omitted/evidence.json) | 负例被正确识别为failed | 原生1次，Count +1；原判定wrong_postcondition，而非unsupported或通过。 |
| [前台enabled精确读回](/Users/ruirui/Documents/Project/Agent/developing/docs/acceptance/assets/computer-use-luna-control-2026-10-04/enabled-readback/evidence.json) | 未通过：verification_unavailable | 点击实际Count +1、SDK unknown；predicate not_checked。有stale恢复，原判定action_count不能替代实际能力结论。 |
| [后台enabled精确读回](/Users/ruirui/Documents/Project/Agent/developing/docs/acceptance/assets/computer-use-luna-control-2026-10-04/phase-2/enabled-readback-background/evidence.json) | 未通过：verification_unavailable | 同一元素ref请求attribute_equals(enabled=true)；原生1次、Count +1，predicate not_checked。 |
| [前台坐标滚动](/Users/ruirui/Documents/Project/Agent/developing/docs/acceptance/assets/computer-use-luna-control-2026-10-04/phase-4/foreground-pixel-scroll/evidence.json) | 未通过：unsupported_foreground_scroll_delivery | Luna用新截图选(500,700)并实际调用；not_started、原生0次、列表未变。 |
| [后台元素双击](/Users/ruirui/Documents/Project/Agent/developing/docs/acceptance/assets/computer-use-luna-control-2026-10-04/phase-2/background-double-click/evidence.json) | 未通过：unsupported_double_click_delivery | 实际element_ref/count2请求；not_started、原生0次、Count未变。 |

| [语义滚动容器](/Users/ruirui/Documents/Project/Agent/developing/docs/acceptance/assets/computer-use-luna-control-2026-10-04/phase-5/background-coordinate-scroll/evidence.json) | 复测阻塞，功能尚不能判断 | 两次discover均preflight_failed/stale_observation，无窗口ref/控件树/截图；未尝试scroll、原生0次。 |

语义容器专项复用后台滚动fixture启动配置，内部case key仍为background-coordinate-scroll；[元数据](/Users/ruirui/Documents/Project/Agent/developing/docs/acceptance/assets/computer-use-luna-control-2026-10-04/phase-5/scenario-metadata.json)与实际request的case_goal明确记录语义容器目标。它不能作为后台坐标滚动复测通过证据。需先修复选择绑定过期，再检验是否暴露合法容器ref及按ref滚动能力。

本次发现的原因与修复方案：

1. **测试 fixture 导出失败导致“点击无效果”的误判。** 原 fixture 把 `NSEvent.locationInWindow` 直接写入 Double 字段。实际前台语义点击产生 `pointerEvents[0].windowX/windowY = NaN`，JSONEncoder 返回 `NSCocoaErrorDomain 4866`，整个 state.json 停留在旧版本。诊断版只把非有限值导出为明确的 `NaN` 字符串，没有替换成 0；同一案成功保存 Count 0→1、按钮回调 +1，截图也显示 Count 1 / State ready。不能继续将旧 Count 0 作为原生点击失败的证据。正式修复应将未知事件坐标表示为 nullable/带状态值，并保证未知坐标不会阻断计数、回调等独立状态导出；判定器须校验 snapshot revision、导出健康和字段有效性。[原始事件采集](/Users/ruirui/Documents/Project/Agent/developing/evals/computer_use/Fixture.swift:90)，[原始编码器](/Users/ruirui/Documents/Project/Agent/developing/evals/computer_use/Fixture.swift:149)，[诊断状态](/Users/ruirui/Documents/Project/Agent/developing/docs/acceptance/assets/computer-use-luna-control-2026-10-04/phase-3/semantic-click/state/state.json)。该问题的具体字段是 pointer 坐标，不能归因为 scrollOffset 或文件权限。

2. **选择绑定与动作观察共用 30 秒寿命，真实模型/IPC 延迟会阻塞。** 候选 publish 时开始倒计时，但选中的绑定直到首次工具调用才由 `open_run_session()` 消费。过期后 selected 被清空，模型再调用 discover 不能自动恢复已经失效的外部选择。第一阶段首案首次 receipt 约 30.052 秒；第二阶段首案约 96.079 秒，包含阶段交接等待，不能当作纯模型推理耗时。正式修复建议在外部选择完成后建立运行绑定，或把已确认选择的寿命与观察寿命分开，并在进入工具时重新校验 PID/process birth/window；动作观察 TTL 单独配置、明确告诉模型剩余有效时间。只允许 `not_started` 恢复，不能重试 `unknown`。[候选有效期](/Users/ruirui/Documents/Project/Agent/developing/src/morrow/adapters/computer_use/candidates.py:49)，[绑定消费](/Users/ruirui/Documents/Project/Agent/developing/src/morrow/adapters/computer_use/owner.py:213)。本次没有放宽 TTL 或伪造时间。

3. **旧测试判定器不适合实时模型恢复。** 它要求一案恰好一次 action 工具调用，因此长文本案“1 次 stale/not_started + 1 次真实 SDK completed”仍原始标为 `failed/action_count`。应分别核对 action 尝试数、SDK 入口数、投递状态和独立效果，允许可证明未执行的过期恢复；真实原生输入仍必须只有一次。`long_text_projection_evidence` 中两个旧布尔字段依赖 ScriptedProvider 的 `scripted_target`，实时桥没有填它，因此其 false 不能用于判断映射失败。本次以实际 receipt、当前 observation 和 native token hash 重新导出引用证明。另一个实际限制是公开值只返回前4096字符：Luna能识别completed，但无法从前缀或当前截图确认末尾suffix；独立oracle确认追加成功，不能把这当成模型已经验证尾部。若要求模型闭环确认长文本，应增加有范围或尾部的读回方式。[长文本实际引用证明](/Users/ruirui/Documents/Project/Agent/developing/docs/acceptance/assets/computer-use-luna-control-2026-10-04/phase-3/long-text-4097/live-reference-proof-bound.json)。

4. **属性读回和投递能力仍有边界。** `enabled=true` 在公开观察中存在，不代表 SDK 能按原生目标 token 精确读回属性；实际返回 `verification_unavailable/not_checked`。后台双击实际返回 `unsupported_double_click_delivery`，SDK 未执行。正式方案应提供原生 token 的精确属性读取接口，或明确声明该 predicate 不可验证；保留能力错误的清楚说明。前台滚动的最终工具错误见结果表。这里不能通过把 unknown 改成 completed、把 observed enabled 当成精确验证或自动换 delivery 来制造通过。

5. **实时桥的终止记录不足。** 第二阶段 enabled 案末尾 receipt 和右键案 action receipt 的 hash 都正确，但未计入已接受记录；右键未进入 SDK、finish_reason=error。原始证据缺异常细节，不能确认是文件非原子写入还是其他桥错误。后续 receipt 已采用临时文件 + os.replace 原子落盘；正式测试桥应记录脱敏错误类别、是否接受 receipt 和取消原因，保证未接受的动作不算执行。第三阶段中断时，一案等待 receipt 后以 driver_error 结束、下一案没有模型决定；保留为未完成，不能归为对应功能回归。

测试过程中 Luna 曾触及账户用量限制；恢复可用后继续补测。所有阶段、失败预检与中断记录保留。本次只修改测试副本与报告，没有把上述正式修复方案写入生产代码。此前 ScriptedProvider 报告只能作为脚本回归证据，不作为 Luna 实时控制证据；其语义点击“无效果”判断受 fixture 导出问题影响。

完整记录包含 **28次独立fixture用例执行、109个已接受的Luna模型决定、13次真实原生SDK入口**；39份图片文件共28个不同图像hash。全部receipt request绑定、图片文件hash、原生PID/窗口匹配校验通过；所有本次fixture进程已退出，Morrow HTTP/OpenAI构造与发送guard均为0。[证据索引与逐案完整性检查](/Users/ruirui/Documents/Project/Agent/developing/docs/acceptance/assets/computer-use-luna-control-2026-10-04/evidence-index.json)。两份未接受receipt单独记录，不计入106次决定或执行通过；第三阶段的额度中断、超时和未执行案也保留。

五阶段材料分别保留：第一阶段在[素材目录](/Users/ruirui/Documents/Project/Agent/developing/docs/acceptance/assets/computer-use-luna-control-2026-10-04/campaign-complete.json)，第二阶段[归档](/Users/ruirui/Documents/Project/Agent/developing/docs/acceptance/assets/computer-use-luna-control-2026-10-04/phase-2-archive.json)，第三阶段[归档](/Users/ruirui/Documents/Project/Agent/developing/docs/acceptance/assets/computer-use-luna-control-2026-10-04/phase-3-archive.json)，恢复后的第四阶段[归档](/Users/ruirui/Documents/Project/Agent/developing/docs/acceptance/assets/computer-use-luna-control-2026-10-04/phase-4-archive.json)，语义容器第五阶段[归档](/Users/ruirui/Documents/Project/Agent/developing/docs/acceptance/assets/computer-use-luna-control-2026-10-04/phase-5-archive.json)。归档保存源文件、构建日志、状态JSON、request、receipt和图片；省略编译app二进制与缓存。原始request保留当时临时路径，不改动字节以免破坏hash绑定；归档清单给出原路径与存储路径。

本轮实际执行并通过：三个桥接/runner辅助脚本的Ruff check、Ruff format --check；这些脚本和实时composition的py_compile；git diff --check。fixture构建和原生结果见逐案日志。本轮未重复全套离线回归，不将历史离线通过数冒充此次实时模型测试数。仓库原有无关改动保留。

修复范围应继续遵循用户要求：不恢复password、credential、secure等关键词命中后的工具层安全过滤；本次普通长文本目标由Luna根据实际观察选择，未通过工具关键词规则选取。能力错误应对应可用性事实和明确投递模式；要让这些未通过功能可用，需补齐投递/精确读回实现并用真实模型重验，不能只取消错误或伪造成功。

