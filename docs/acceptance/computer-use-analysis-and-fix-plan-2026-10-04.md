# Computer-use 问题分析与修复方案（2026-10-04）

依据：[真实 Provider 功能测试](computer-use-live-2026-10-04.md)、当前 Morrow 源码 `aa506737`、安装的官方 `cua-driver==0.30.4`、同版本官方 Git tag，以及本轮受控原生诊断。

**结论：未通过项不是同一种故障。** 已查明截图拒绝、属性验证不可达、元素双击语义丢失等代码/接口问题；按键“当步无效果”主要是 fixture 的状态提交口径造成的误判；滚动驱动能生效，但模型可见的滚动目标缺失、图片不可用，使端到端目标选择失败。点击、组合键和 secure 输入的 `unknown` 大部分是 SDK 的结果契约，不能通过改枚举把它们变成成功。

实施更新（2026-10-04）：后续已按用户授权进入修复，见 [修复与验收记录](computer-use-repair-2026-10-04.md)。下文保留方案制定时的观察；CU-07 的精确对象读回仍受官方 SDK 公开能力阻碍，不能用收窄 schema 代替完整验收。

本轮只做排查和方案设计，**没有实施生产修复，没有激活实施计划**。新增原生诊断不调用 Provider，不使用真实凭据；直接 SDK 的组件诊断与普通 AgentLoop 的端到端验收分别记录。方案保持官方 SDK、选定窗口、既有审批/互斥/取消/unknown 不重试。

**用户最新决定（2026-10-04）：computer-use 工具层的内容安全判定全部移除，安全由 LLM 根据用户意图和上下文自行判断。** 这项决定覆盖关键词/正则命中、凭据样式识别、字段 role/subrole 推断，以及由这些判定派生的拒绝执行、文本脱敏、属性隐藏、截图遮罩和图像发布阻断。移除整条判定链，不改成更宽松的匹配规则，不引入另一套工具侧分类器或私有字段安全证明。此前方案中“真实 credential 继续脱敏”“真实敏感区域继续遮罩”“不读取 secure 明文”等要求已撤销；以下修复方案与验收以本决定为准。

工具职责是按公开 SDK 契约执行请求并如实返回可获得的观察与结果。保留参数类型/范围、SDK 能力、窗口与进程身份、观察新鲜度、坐标映射、图片解码/尺寸/资源预算、互斥/取消和结果真实性等功能性校验；字段包含 password 或属于 secure 控件不构成额外拒绝条件。OS/SDK 本身不可读的字段仍报告不可读，不能伪造值或成功。Provider 配置凭据的存储/传输属于独立基础设施契约，不作为屏幕内容或操作内容的安全判定入口。

## 1. 问题清单与判断

| 编号 | 原测试未通过项 | 已查明的原因 | 归属/确定性 | 建议优先级 |
| --- | --- | --- | --- | --- |
| CU-01 | hybrid 截图 image_safety_unconfirmed | 菜单项标签命中 `password` 关键词，被当成敏感内容；该节点无 frame，遮罩生成失败 | Morrow 内容分类/遮罩耦合，实机确认 | P1 |
| CU-02 | 坐标 unknown_scale、视觉闭环受阻 | 模型使用 semantic fallback 的无映射 frame；有图时映射正确，但没有发布的图像仍被 admission 拒绝 | CU-01 的下游阻塞；拒绝本身正确，实机确认 | 随 CU-01 |
| CU-03 | q completed 但 oracle 不变；Y 未及时出现 | 原生编辑缓冲区已含 q/Y，SwiftUI `@State`/state.json 尚未提交变化 | 测试 fixture/oracle，实机确认；全字串精确性仍需事件 oracle | P1（先修证据） |
| CU-04 | token 滚动无位移 | SDK 将 AXScrollArea 折叠掉；fixture 滚动区域很窄；模型没有可靠滚动 token/图像定位 | 目标可观测性不足已确认；原场景实际选择的 token 未保存，不能断言其精确落点 | P1 |
| CU-05 | 点击、hotkey、secure 输入有效却 unknown | SDK 对无独立读回的动作返回 Unverifiable；Morrow 正确保留 unknown | SDK 能力/结果语义，源码及实机确认 | P2 |
| CU-06 | 双击仅计数 +1；右键 suspected_noop | SDK 元素路径是单次 AXPress/AXShowMenu；count 只在像素路径生效；按钮不广告 AXShowMenu | SDK 路由与 Morrow 声明语义不一致，源码确认 | P1 |
| CU-07 | attribute_equals verification_unavailable | SDK macOS 的 elements_complete 固定 false；Morrow 属性验证要求完整树，入口因此不可达 | SDK 契约与验证规则不兼容，源码及实机确认 | P1 |
| CU-08 | 工具描述仍禁止凭据输入 | observe 描述残留旧策略；工具层内容安全判定也未完整退出 | 提示词与用户最新决定不一致，源码确认 | 与 CU-01 一并修复 |
| CU-09 | 实测覆盖与自动判定不足 | fixture 无真实双击/右键/live-edit oracle；脚本仅输出 raw tested，未严格验证请求变体 | 测试体系缺口，源码确认 | 与 CU-03 并行 |

P1 表示阻塞对应功能验收或存在明确语义缺陷，不表示所有项都存在安全事件。

## 2. CU-01：截图拒绝的精确触发链

本轮只读探针返回 132 个 SDK 元素，Morrow 也保留 132 个：`omitted=0`、`truncated=false`、SDK 截图 1040×1024、frame_valid=true。因此本次拒绝**不是元素预算、树截断、TCC 权限或 Provider 图片协议失败**。

唯一 `sensitive=true` 的投影节点是索引 94 的 `axmenuitem`：无 value、有 label、frame=null；标签匹配 `password`。`_element_label()` 对 value/value_description/label 都调用默认 `legacy_strict` 的 `refuse_secret_material()`。这个规则只要文本包含 password 等关键词就拒绝，分类随后变成 sensitive。`project_sensitive_regions()` 遇到该敏感节点的空 frame，在第 224 行抛出 image_safety_unconfirmed。错误经观察服务上抛，图像没有进入发布/Provider 阶段。

证据：[read-probe.json](assets/computer-use-analysis-2026-10-04/read-probe.json)。代码：[projection.py](../../src/morrow/adapters/computer_use/projection.py)、[domain.py](../../src/morrow/core/domain.py)、[computer_visuals.py](../../src/morrow/application/computer_visuals.py)。

**修复方案：**

1. 移除 computer-use 调用链对 `refuse_secret_material()` 的内容判定：应用/窗口标题、候选标签、AX label/value/value_description 和文本后置条件均按数据契约处理，不因关键词、凭据样式或赋值形式拒绝、脱敏。SDK 提供的可读内容按观察契约投影，role/subrole 只描述控件与能力。
2. 移除 `_element_label()` 的 secure/password 角色判定，以及 `sensitive` 导致的 label/属性清空、registry 分类和验证过滤。同步调整 core validator，避免 adapter 放行后 DTO 或后置条件再次拒绝。
3. 移除 `project_sensitive_regions()`、自动遮罩和“敏感区域必须覆盖”的发布要求，删除 `image_safety_unconfirmed` 的内容安全拒绝分支。AX 树部分可见、截断或某个节点没有 frame，不再因“无法证明图片安全”而阻断有效截图。树完整性仍用于判断元素缺失能否证明不存在；图片自身的解码、尺寸、窗口几何和资源限制仍单独校验。
4. 清理数据契约中的 `sensitive`/`SensitiveCaptureRegion`/`sensitive_regions`、computer-use 专用的 `CaptureMask` 接口及关联导入、错误映射、恢复提示、GUI 文案与测试。若持久化兼容需要暂时读旧字段，只用于读取历史记录，不继续参与当前内容过滤或准入。取消新截图的自动敏感性分类；Artifact 元数据记录实际处理事实，不能宣称截图“已脱敏”或“已证明非敏感”，现有共享契约不支持时一并调整 computer-use 接入。
5. 审查工具结果进入上下文、Artifact、事件 DTO 与 GUI 预览的共享校验链，确保 computer-use 内容不会在下游再次被同类规则拒绝或改写。保留结构化、大小及类型边界；不通过调低正则强度或增加字段资格证明保留原有内容策略。

**已定位的改动面：**

| 环节 | 文件 | 移除内容 |
| --- | --- | --- |
| 发现与投影 | [census.py](../../src/morrow/adapters/computer_use/census.py)、[projection.py](../../src/morrow/adapters/computer_use/projection.py) | 标题/标签内容检查、secure/password 角色分类、label/属性隐藏、敏感区域推导 |
| 数据契约与引用 | [computer_use.py](../../src/morrow/core/computer_use.py)、[computer_actions.py](../../src/morrow/core/computer_actions.py)、[registry.py](../../src/morrow/adapters/computer_use/registry.py) | 内容 validator、`sensitive_has_no_label`、敏感标记及区域 DTO、文本后置条件内容禁令 |
| 捕获与发布 | [session.py](../../src/morrow/adapters/computer_use/session.py)、[images.py](../../src/morrow/adapters/computer_use/images.py)、[computer_visuals.py](../../src/morrow/application/computer_visuals.py) | AX 完整性触发的图片安全拒绝、自动黑块遮罩、遮罩集合校验与发布安全证明 |
| 结果验证 | [computer_verification.py](../../src/morrow/services/computer_verification.py) | 按 `sensitive` 排除节点、限制文本匹配和属性验证的分支 |
| 模型/用户说明 | [computer_tools.py](../../src/morrow/application/computer_tools.py)、[computer_recovery.py](../../src/morrow/application/computer_recovery.py)、GUI 与相关文档 | 凭据输入禁令、图片安全拒绝提示、工具自行保证内容安全的说明 |
| 诊断与回归 | `evals/computer_use/native_*.py`、`tests/test_computer_use_*`、`tests/test_computer_visual_service.py` | 遮罩调用和敏感节点过滤；把旧“拒绝/隐藏/遮罩”断言改为内容透明与真实能力断言 |

**验收：** 使用合成内容覆盖 password 等关键词、凭据样式、赋值形式和 secure/password 控件角色；在 SDK 能力允许时，观察、输入、文本后置条件与图片发布均不因内容触发额外拒绝、隐藏、改写或遮罩。无 frame 的密码菜单以及部分 AX 树不得阻断几何有效的截图。前后图片成功发布到 Artifact，Provider 输入 hash 与 Artifact 一致，测试像素不被 Morrow 内容策略涂黑。SDK 未提供的值继续明确 unavailable；窗口失效、无有效坐标映射和损坏图片仍按功能契约失败。测试只用合成数据，不把真实 Provider 凭据作为屏幕测试样本。

## 3. CU-02：坐标失败属于图片链路的下游

`project_frame()` 在无截图时保留逻辑窗口尺寸，但 scale/crop 为空，`map_image_point()` 返回 unknown_scale。第一次真实模型坐标滚动发生在 semantic fallback 后，拒绝符合契约；不应拿 Retina 倍率猜坐标。

本轮有截图的只读观察得到 SDK screenshot_scale=2，Morrow frame 为 1040×1024、scale_x/y=1、crop=1040×1024。这里的 1 **是正确值**：Morrow 输出送入 SDK 的已交付图片像素，SDK 再完成 backing/downscale 转换。尝试通过 Morrow admission 使用该 frame，错误变为 `image_not_published`，说明映射本身可用，但模型尚未获得合法发布的图片。证据：[coordinate-probe.json](assets/computer-use-analysis-2026-10-04/coordinate-probe.json)。

**修复方案：** 优先修 CU-01，保留当前“图像已经发布且观察新鲜”才能执行坐标的约束。在模型指引中明确 semantic 观察只支持元素目标；视觉动作必须使用同一次发布图像。图像失败时将坐标场景记为 blocked，不能自动改成元素点击后算作坐标通过。

**验收：** 发布图片后做真实前台坐标点击/滚动；覆盖 Retina、缩放和窗口移动。仅语义观察依然拒绝 x/y；图片 hash、观察 ID、窗口绑定必须一致。本轮直接 SDK 滚动诊断不满足这项端到端验收。

## 4. CU-03：按键失败结论需要修正

对同一个受控普通文本框，补测结果如下；读取的是公开 SDK 的原生观察及 fixture 自己的状态文件，没有读取 secure 内容：

| 时点 | 原生编辑控件 | SwiftUI state.json | SDK 状态 |
| --- | --- | --- | --- |
| 普通 text 输入后 | marker 已存在 | marker 已存在，revision=17 | completed |
| q 后 | q_count 从 0→1 | q_count=0，revision 仍为 17 | completed |
| Shift+Y 后 | Y_count 从 0→1 | Y_count=0，revision 仍为 17 | unknown |

这直接证明原报告中 state.json 无变化不能推出按键未送达。fixture 只在 SwiftUI `@State` 的 `.onChange` 中写文件；原生 field editor 与绑定提交不是同一个观察时点。旧场景在 secure 输入改变焦点时才出现 q/Y，与旧编辑状态被提交相符，**不能继续称为输入事件到 secure 步骤才抵达**。

证据：[component-probe.json](assets/computer-use-analysis-2026-10-04/component-probe.json)，代码：[Fixture.swift](../../evals/computer_use/Fixture.swift)。SDK 单键以同一原生对象的 value/selection 变化作为确认依据，解释了为什么 q 已返回 completed；这仍不能当成业务应用保存成功。[官方 press_key 实现](https://github.com/trycua/cua/blob/cua-driver-rs-v0.30.4/libs/cua-driver/rust/crates/platform-macos/src/tools/press_key.rs)。

**修复方案：**

- 优先修 fixture：使用 AppKit `NSControlTextDidChange`/field editor delegate 导出 live-edit 状态，同时单独保留 SwiftUI/业务 committed 状态；普通和 secure 场景均使用合成输入，fixture 可独立记录预期值、实际值和事件计数。产品层不另加 secure 内容禁读规则；Boolean oracle 只作为有限效果证据，不充当精确字串验证。
- 每个动作测试使用新 fixture 实例或明确重置并验证焦点、输入法和已提交状态；记录原生事件计数、字段身份及输入后 live 值和 committed 值。不要用固定 sleep 或用下一次输入顺便提交前一次，再把变化归因于后一动作。
- Morrow 层先不把 completed 改成 unknown。对“必须提交/保存”的任务使用明确业务后置条件，区分编辑输入与提交成功。

**验收：** q/Shift+Y 在 live oracle 中一次出现；commit oracle 可独立验证提交。精确字串、无额外字符、事件次数、字段身份仍需新 oracle；本轮仅证明 q/Y 已存在于原生控件，不声称完整按键变体已经通过。

## 5. CU-04：滚动失败的目标问题

官方 SDK 的 AX walker 遇到 AXScrollArea/AXGroup 会直接递归子节点而不输出该节点。Morrow 实测的角色集合确实没有 axscrollarea，模型无法引用滚动容器；图片又被 CU-01 阻塞。

Apple 原生只读 AX 探针找到了 fixture 真实滚动区域：屏幕 frame `(498,484,87,200)`。其宽度只有 87 点，属于 SwiftUI 当前布局；窗口中心不在该区域。对照组件诊断中，公开 SDK 在窗口中部滚动无变化；改为实际滚动区域中心对应的图片点 `(127,764)`，同样 foreground/down/3，使独立 scrollOffset 从 **0→60**。SDK 仍返回 unknown。这说明 SDK 的轮事件路径在此环境可以工作，并非“滚动 API 完全失效”。

证据：[ax-scroll-probe.json](assets/computer-use-analysis-2026-10-04/ax-scroll-probe.json)、[wheel-sdk-targeted-probe.json](assets/computer-use-analysis-2026-10-04/wheel-sdk-targeted-probe.json)。组件诊断直接调用公开 SDK，未经过 Morrow 的图像发布/admission，不能当作产品坐标链路通过。

**修复方案：**

1. 修好图片链路，允许模型在新图上选择实际滚动区域。没有正确 token 或图片定位时应返回无法确定目标，不能以任意窗口/文本框中心作为滚动容器。
2. fixture 的 ScrollView 加明确宽度及 accessibility identifier，并保留真实可滚动区域的独立 frame/offset；这样测试不再依赖隐含布局。
3. 如果要承诺语义 token 滚动，需要官方 SDK 保留滚动容器及 token/action 能力。现有 0.30.4 的 actionable tree 缺少该目标，单靠 Morrow 重命名 AXWindow 不能补上。
4. live harness 保存动作所引用节点的 role/能力/几何元数据。原始报告只保留动作类型，无法追溯原 token 的精确落点，这一证据缺口不能用推测补齐。

**验收：** 同一已知容器，token 路线（若 SDK 支持）与图像坐标路线分别产生独立位移；区域外点不得算通过；轮方向/amount 可核对；不重复 unknown 动作。

## 6. CU-05：unknown 的真实含义及处理方案

Morrow `outcome_from_action()` 将 SDK `UNVERIFIABLE` 映射为 `unknown/unverified_action`，没有丢掉成功证据：

- 普通按钮 AXPress 只证明 native API 被调用，SDK 未读回业务 counter，通常保持 Unverifiable。
- hotkey 的 SDK 实现不做 read-back，固定 unverifiable。
- secure 文本无法通过公开 AXValue 读回完整插入文字，SDK 的文本确认逻辑没有肯定证据，独立 Boolean 又仅证明“有内容”，不证明完整、正确和次数。
- 后置条件 passed 与 native effect 是两个字段；`element_exists` 是观察状态，不能证明本次点击造成了状态变化。

**修复方案：** 保留原结果，不将 unknown 批量升级为 completed。对用户展示“已投递/SDK 效果未知/任务后置条件已证实”三个事实。给可读业务结果使用强后置条件；需要改变类型时单独设计验收语义，普通 element_exists 不能充当因果证据。secure 场景如实返回公开 SDK 可获得的观察，不因角色另加读取或输入禁令；SDK 本身不可读时保持 unavailable/unknown，不为了证明完成再次输入。

**验收：** native unknown+postcondition passed 能明确展示；只有具备相应独立证据的任务条件通过；日志不得把全部 SDK 调用计为 completed。按钮 counter、secure Boolean 与文本精确匹配分开计量。

## 7. CU-06：元素双击与右键的路由差异

Morrow 对有 element_ref 的 ClickAction 无条件生成 `ClickPosition.ELEMENT(token)`，并传入 button/count。SDK 0.30.4 的元素路径是语义动作：左键通常单次 AXPress，右键映射 AXShowMenu；`count` 在这个路径不产生两次鼠标事件，SDK 的 schema 明确 count 仅用于 pixel path。原 double_click 的 count=2 确实传入，counter +1 与该路由一致，不是模型忘了设置 count。

右键 Increment 时，目标没有广告 AXShowMenu，SDK 记录 suspected_noop；“没有计数增长”本身不是右键失败 oracle，按钮也没有上下文菜单，因此当前 fixture 无法证明鼠标右键事件。

**修复方案：**

- 将“语义激活”与“鼠标手势”的实现能力明确区分。count=2 和物理右键必须在动作前选择可表达相应事件的像素路径，使用同一新鲜已发布图像、精确目标几何和现有窗口/投递授权；无这些证据时明确 not_started，不能悄悄变成 AXPress。
- 如果继续提供语义 show-menu，依据 SDK 广告的 action 能力选择，并向模型说明能力；不要先发送可能无效的 AXShowMenu，再自动补一次像素右键。
- fixture 使用可计数的 AppKit 视图，记录 mouseDown 的 clickCount、rightMouseDown 及上下文菜单动作。单个 SwiftUI Button 的 counter 无法证明双击/物理右键。

**验收：** count=2 路由不能落入单次语义 AXPress；原生 oracle 观察到 clickCount=2。右键事件和菜单命令有独立记录。所有 fallback 在原生投递前选择，未知效果后不重试。

## 8. CU-07：属性验证在当前 SDK 上不可达

探针里 Increment 的 `enabled=true` 已被 SDK 返回并正确投影，字段没有丢失。但 `evaluate_postcondition()` 对 attribute_equals 要求 `observation.complete=true`；SDK macOS 的 get_window_state 在 0.30.4 **直接将 elements_complete 设置为 false**。这是主动的完整性契约，并非本轮 132 个节点太少或超时，因此增加 max_elements/depth 或等待更久也不能解决。[官方观察实现](https://github.com/trycua/cua/blob/cua-driver-rs-v0.30.4/libs/cua-driver/rust/crates/platform-macos/src/tools/get_window_state.rs)。

此外，Morrow schema 允许 focused/checked/expanded，而投影只写 enabled；官方 WindowElement 也没有这三个同名字段。focused、expanded 当前不可读；selected 存在，但不能无条件等同于所有控件的 checked。

**修复方案：**

1. 为属性验证建立“同一精确原生节点”的绑定/读回，避免依赖整窗树完整性才能证明一个节点的 enabled。旧 element_ref 不能直接在新快照靠角色/索引重建身份，需明确同一对象的可验证稳定关系；只使用公开 SDK 观察能力，原生 token 不进模型上下文。
2. 维持部分树中的“未找到≠不存在”；如果只有 label selector 而没有精确对象身份，就继续 unavailable，不能把局部唯一当成全窗唯一。另一选择是重新定义为存在式匹配，但这改变语义，必须显式命名和评审。
3. 模型工具公开真实属性能力。当前 unsupported 属性应提前报有界 unsupported，或由官方 SDK 提供类型化观察字段后再开放；checked 仅在明确 checkbox 语义和 boolean 数据一致时从 selected 映射。

**验收：** enabled 真/假在同一绑定对象上可验证，即使全树不完整；重名、缺失、不可读均不假成功。password 等标签或 secure 角色不额外排除已知属性。focused/checked/expanded 要么有对应原生证据与测试，要么明确不支持。

## 9. CU-08/CU-09：提示词与测试口径

observe 描述中的 `Credentials must never be entered.` 残留在 `computer_tools.py:129`，与用户最新决定不一致。删除这句旧禁令，并同步清理自动脱敏、遮罩、字段安全资格和图片安全证明的工具描述、恢复提示及 GUI/文档说明。内容与动作的安全判断由 LLM 根据用户意图和上下文完成；工具描述提供功能、参数、窗口范围、SDK 能力和 unknown 的真实语义，不再规定凭据类内容一律禁读/禁输。

live_provider.py 的 status=tested 和 CLI 正常结束目前只证明“收集到了记录”。模型把 coordinate_click 改成 element_ref、选择错误字段、双击被单击处理，都需要单独的 verdict。修复测试口径时建议输出 `passed/failed/blocked/unsupported` 与 expected/actual 变体，并为未通过场景设置可用于门禁的退出码；raw evidence 保持不可改写。

fixture 还需分别提供 live-edit/commit、滚轮 offset、按钮 mouse event 与业务动作 oracle。每场景独立状态，固定 synthetic marker，记录预期节点身份、动作次数、所选 role/frame、原生 completion 和验证结果。图像拒绝不得用 semantic fallback 掩盖坐标未覆盖。

## 10. 推荐实施顺序与验收门槛

| 顺序 | 工作包 | 主要文件/责任层 | 完成标准 |
| --- | --- | --- | --- |
| 1 | 修 fixture/oracle 与结果分类 | Fixture.swift、live_provider.py、native_text.py / evals | 区分 live/commit；严格验证动作变体；错误目标和被阻塞场景不会“通过” |
| 2 | 全面移除 computer-use 工具层内容安全判定 | CU-01 改动面全部环节，含数据契约、投影、验证、图片、提示和测试 | 关键词/凭据样式/secure 角色不触发拒绝、隐藏或遮罩；前后图到真实 Provider；无下游同类残留 |
| 3 | 重跑视觉坐标与滚动 | computer_admission 保持边界，SDK 官方观察能力、fixture | 已发布图像下的正确区域产生位移；缺图拒绝；token 支持边界明确 |
| 4 | 修点击变体的路由契约 | session.py、registry.py、computer_actions.py、tool 描述 | double/right 不静默降为语义 press；真实事件 oracle 通过 |
| 5 | 修属性验证能力 | computer_verification.py、projection.py、registry.py、tool schema | 精确节点 enabled 可验证；unsupported 属性不伪装可用 |
| 6 | 对齐 unknown 展示，最终实机回归 | computer_tools.py、结果/GUI 展示及验收文档 | native completion 与 task verification 分开；LLM 负责内容安全的决定在所有入口一致 |

每包先做针对根因的离线测试，再通过真实 SDK/独立 oracle；最终用原指定真实 Provider 重跑 semantic 前后台与 hybrid 图片/坐标矩阵。图片阶段应核对 Artifact→Provider hash；手势阶段核对事件次数；属性阶段核对节点身份。之后再执行仓库所需 offline/Ruff/GUI gates。

固定官方 0.30.4 下先完成可由 Morrow/fixture 修复的部分；需要滚动容器或新的属性观察时，提出明确的官方 SDK 契约需求并单独验证版本。此报告没有证据证明其他版本已修复，不建议凭版本号盲目升级，也不建议恢复自定义 guarded SDK。

## 11. 证据范围与剩余事项

补充诊断依据见 [summary.json](assets/computer-use-analysis-2026-10-04/summary.json)。原始 JSON 保留；assets 按仓库惯例仅本地保存，汇总含文件 hash。源码分析使用官方 tag `cua-driver-rs-v0.30.4`、commit `bf6c76786d938070f4ecf1e44004752f69f518b8`，排除了本地 SDK checkout 后续实验提交对结论的影响。

已定位的根因不需要继续按“所有动作失败”处理。仍未证实的是：原场景所选滚动 token 的精确落点、全量键盘组合的精确事件次数/字串、其他框架和应用的兼容性，以及修复后的完整视觉链路。报告明确保留这些边界，没有将 SDK 组件生效写成产品验收通过。

本轮运行的是临时只读探针与少量受控组件动作，没有重跑全量 pytest/GUI，也没有生产改动；前一轮通过的 3058 offline/724 GUI 仍是原功能测试的证据，不能写成修复后验证。该 fixture 进程已结束，现有用户 staged/dirty 内容与 `.agent/` 状态保持。
