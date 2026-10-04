# Computer-use 修复与验收（2026-10-04）

依据：[问题分析与修复方案](computer-use-analysis-and-fix-plan-2026-10-04.md)。生产修复提交 `ea1ad393`、`63d21964`、`d953cb39`，有意基于 `0dec8d48` 的窗口 admission/诊断工作堆叠。仅使用官方 `cua-driver==0.30.4`，没有恢复自编 SDK。

Morrow 可实施的内容链、截图、手势、能力描述和 fixture 判定已经修复。精确旧 token 的 enabled 读回仍缺少官方公开接口，不能宣称 CU-07 全部完成；语义滚动容器 token 在该 SDK 的 fixture 观察中也不可用。真实 Provider 矩阵结果见下方，unsupported/blocked 不计为通过。

## 改动与责任边界

| 问题 | 当前行为与证据 |
| --- | --- |
| CU-01/CU-02 内容与截图 | 删除关键词/凭据样式/secure role 分类、属性隐藏、遮罩和部分树图片拒绝。SDK-readable label/value/value_description、typed enabled 直接投影；公开 SDK 没提供的值保持 None。图片只作解码、几何、像素/字节预算和所属执行授权校验，AX 不完整不阻止有效图片。 |
| 下游重现旧策略 | computer intent、execution、approval、journal 重载、ComputerToolFacts、窗口 scope 与 observation Artifact 都使用内容透明契约；其他工具和 Provider CredentialStore 保留自身契约。默认 intent 的序列化不增加新字段，保持旧 hash，兼容项目最低 Pydantic 2.9。 |
| CU-03 fixture/键盘 | v2/v3 fixture 独立导出 live 普通/secure 合成字符串、Enter committed 值、字段身份、NSControl 通知数、原生 key-down 字符/字段与 key-up 数。SDK 编辑可能绕过 NSControl 通知，NSWindow 更新从真实 field editor 采样，不伪造通知次数。失焦不再擦除或冒充 Enter 提交；v3 增加按钮 action callback 计数。 |
| CU-04 滚动 | token 必须是实际观察到的 axscrollarea/axscrollview；任意文本框/窗口 token 返回 unsupported_scroll_target，投递数 0。有效图像允许在真实滚动区域内选坐标。前台 pixel wheel 在 key/active fixture 上仍无接收事件，因此返回 unsupported_foreground_scroll_delivery；后台坐标路径可用，必须由用户明确授予后台投递，不能自动 fallback。SDK 省略容器的能力边界写入工具说明。 |
| CU-05 unknown | native completion 与 postcondition 分开；不重试、不改成 completed。GUI 显示“SDK 已确认”/“SDK 效果未知”，unknown+passed 保持两个事实，不推导任务完成。 |
| CU-06 双击/右键 | 元素 left/count=1 保持语义激活；前台 count=2 或 right/count=1 在投递前映射精确元素中心到同一新鲜已发布图片，走像素路径。缺图、缺几何、越界、过期/迁移均未投递。AppKit 独立记录 clickCount 和右键事件，业务 counter 不再代替手势证明。0.30.4 在本机后台双击传入正确图片点仍生成错误 AppKit 窗口坐标，因此后台 count=2 提前返回 unsupported_double_click_delivery，0 native entries；不变更投递模式。 |
| CU-07 属性 | schema 只公开 enabled；focused/checked/expanded 有界 unsupported。完整且唯一 selector、typed 已知 bool 的真/假在离线验证；部分树、重名、None、旧 ref 不假成功。官方 SDK 缺少精确对象跨快照 readback，本机验收仍 unsupported。 |
| CU-08/CU-09 提示/验收 | 移除 Credentials must never be entered 等旧禁令，内容判断由 LLM 结合授权意图完成。fresh fixture/独立 Python SDK 实例逐案隔离，strict verdict 验证实际变体、单次 native entry、正确字段/精确字符串、事件数、位移及 Artifact→Provider hash；非 passed 返回非零。 |

结构约束仍保留：选定进程/窗口、审批、scope、互斥、观察新鲜度、坐标映射、解码、预算、取消与 native unknown 真实性。会话历史仍由 Session ConversationLog 唯一写入，普通聊天仍走 AgentLoop。所有 native 动作只操作本次启动的合成 fixture；不会关闭已有同 bundle 的用户进程。Provider 凭据仅存于进程内，未写入配置、证据或模型请求。

## 官方 SDK 外部缺口

检查安装的公开 Python ABI 和官方 tag `cua-driver-rs-v0.30.4` / commit `bf6c76786d938070f4ecf1e44004752f69f518b8`。`WindowElement` 提供 enabled/value/selected，但无 focused/checked/expanded；ElementSelector 只有 role 与 label_contains，ElementPredicate 没有旧 element_token 精确对象字段。macOS get_window_state 固定 elements_complete=false，跨快照 token 改变。不能用新树同位置/同名节点假装旧节点，也不能把局部唯一解释为全窗唯一。

官方接口需求：保留 actionable scroll container token；修正后台双击的 AppKit 窗口坐标投递与前台 assist/PID wheel 丢事件；增加可在同一对象绑定上执行属性读回的公开接口，并给出对象失效/属性不可读的类型化结果。只有该能力与独立真/假、重名、失效节点原生测试通过后，才可开放完整 CU-07。当前不盲目升级版本，也不改为弱存在式属性断言。

## 离线与构建验证

| 命令/范围 | 实际结果 |
| --- | --- |
| uv run pytest -q tests/test_computer* | 465 passed；随后新增无帧元数据回归包含在最终全套中，最新 harness/counter/text 定向 24 passed |
| uv run pytest -m 'not live' -q | Luna 诊断改动后的最终复跑 3087 passed，2 deselected，212.93s；之前生产版本3081 passed。首次新增回归全套有一项既有 sandbox 1秒timeout，单独复查通过，原失败日志保留。 |
| uv run ruff check . | passed |
| uv run ruff format --check src tests evals/computer_use | 755 files already formatted |
| uv run ruff format --check . | 未通过；仅用户已有 evals/benchmarks/run_tb2.py 格式差异。未改动、未提交该文件。 |
| compileall src tests / morrow --help / git diff --check | passed |
| pnpm GUI install / typecheck / test / build | passed；107 files / 724 tests；bundle budget passed |
| Swift fixture build | passed，独立 private state directory |
| uv build | wheel/sdist 成功，先完成 GUI build |
| Python 3.12.13 wheel no-extra / extra installed smoke | 两环境 passed，56/57 依赖兼容；30 GUI 文件 hash 对齐，普通任务 completed，网络/SDK 导入 0 次，desktop not_activated；extra SDK 0.30.4，no-extra SDK absent |

这些离线结果不代表其他真实应用、全部键盘组合或其他系统已通过。GUI 结果是单元/DOM 与构建门禁，不冒充本轮真实 GUI 鼠标验收。

## 真实 Provider 结果

运行环境：macOS 27.0 arm64，项目 Python 3.13.0，官方 cua-driver 0.30.4；DeepSeek OpenAI-compatible base_url https://api.deepseek.com，model deepseek-flash。普通 AgentLoop、生产 Provider adapter、临时隔离 store 和每案一次批准动作。

首轮修复后矩阵（生产 `ea1ad393`，当时的 v2 fixture）：**36 场景，29 passed / 4 unsupported / 3 failed**。semantic 前后台输入/按键/提交/secure、text_appears/element_exists、拒绝审批，以及 hybrid 观察/坐标点击/前台双击/左右键和后台坐标滚动有独立效果证据；hybrid 图像 SHA 与 Provider 请求一致。3 个失败为 foreground 坐标滚动（1920/640）与 background 双击，随后用原生指针事件定位并增加投递前 SDK 能力拒绝。首轮不是最终源码全量通过证据。

| 额外定向证据 | 可核验结果 |
| --- | --- |
| q / Shift+Y | live 精确一次 q/Y，正确 fixture-text，各一次 key-down/up；通知事件数 0 时不伪造 NSControl 通知。 |
| Enter commit | 一次动作将独立 live seed 提交到 committed text。 |
| background pixel scroll | SDK 3 个 wheel 事件到同一 NSScrollView 区域，offset 0→60；native unknown 保留。 |
| background double-click SDK 缺口 | 传入 count=2 / LEFT / BACKGROUND、选中 Increment 的准确 role/frame；SDK 产生 clickCount 1/2 的窗口内 x=558（窗口宽 520），按钮收到事件数 0。 |
| foreground wheel SDK 缺口 | 对正确区域传入图片坐标，key/active fixture 仍收到 wheel 事件数 0 / offset 0。 |

最新矩阵（生产 `d953cb39`、harness `11a67402`）**36 场景，1 passed / 35 failed**，其中 33 个场景含 Provider invalid_response，1 个 fixture startup timeout，另 1 个 semantic foreground click 进入 SDK 但独立 counter 未变化。独立 Provider 最小请求诊断确认 **HTTP 402 / balance_marker=true**；不能把这些记录解释为 35 个 Morrow 根因回归，也不能删除或改写为通过。新增 metadata 一度把 semantic 合法 window_bounds=None 当错误，已通过独立回归修复；那次中止的 v2 矩阵另外保留。

用户确认 Provider 无余额，并要求改用 Luna max 测试；停止 DeepSeek 调用。Codex Luna max 辅助受控原生测试单独记录，不作为 Morrow 已接通 Luna API Provider 的证据。余额恢复后的真实 Provider 重跑仍待执行。原型 fixture 后续调整把 focus 诊断移出 SwiftUI 渲染状态，以避免诊断影响控件；定向组件 semantic click 仍为 SDK unknown、counter 0→0。下方 Luna 对照也未证明此路径，不以首轮正例掩盖此缺口。现有 SDK 固定不更换，unknown 不自动重试，不用另一个 delivery 伪装原路线通过。

native unknown 始终保持；独立 oracle 通过只证明对应 fixture 效果。640-pixel moved-window 的最终后台滚动场景因 Provider 402 未获得最终产品效果证据，不声称缩放/移动矩阵全部通过。

原始证据按仓库惯例保存在 gitignored assets/computer-use-repair-2026-10-04/；汇总及 SHA-256 见 [summary.json](assets/computer-use-repair-2026-10-04/summary.json)。原分析/旧 Provider 证据不改写。首批 smoke 发现的 fixture/模型参数问题保留为诊断记录，不计入最终通过率。

## Luna max 辅助受控验收

按用户要求使用 Codex 的 `gpt-6-luna` / `max` 测试代理，未使用用户真实 Provider 凭据。测试代理选择并检查动作，有效产品闭环由 ScriptedProvider 驱动普通 AgentLoop；其余原生组件使用公开 SDK。只操作本案启动的 fixture PID/window，每个有效动作 case 使用独立 Python 进程；复用进程造成的窗口发现失败另存原始记录，native entries=0，不计为动作通过。

| 检查 | 结果与限制 |
| --- | --- |
| 普通 AgentLoop 输入闭环 | 单场景 passed；一次批准、一次后台输入，SDK completed、独立 live text 精确追加，前后两个不同图像的 Artifact/Provider SHA 匹配。记录中的 native_product_complete=false 保留；ScriptedProvider 不代表真实 API 联通或整体产品完成。 |
| q | SDK completed，独立 fixture-text 精确 q、一次 key-down/up、fresh observation。 |
| Shift+Y | SDK unknown/unverified_action 保留；独立 fixture-text 精确 Y、一次 key-down/up、fresh observation。 |
| secure 合成输入 | SDK unknown 保留；独立 live buffer 与请求 input SHA 完全匹配，NSControl 通知25次。该结果是 fixture 精确效果证据，不把 SDK 状态升级。 |
| semantic AXPress | foreground active/inactive 的 NSButton oracle 均 counter/callback 0→0；独立 SwiftUI Button 对照亦0→0。核对的 SDK PID/window 与本案实例一致；只读 AX 检查唯一 Increment/fixture-increment 明确广告 AXPress。当前证据不支持 Coordinator 绑定是根因，也未证明语义点击生效；仅限此环境与 fixture，不推导所有应用都失败。 |

v3 fixture/native_counter 增加 callback 与选择对象身份诊断，投递前核对 PID/window。官方 tag 的 AXPress 调用在 AX API 成功返回且无读回时报告 unverifiable；它不证明业务回调已执行。Luna 未对 unknown 动作重试，未修改生产结果枚举或 delivery。

追加坐标闭环的测试脚本替换失败：`runpy.run_path()` 返回字典的替换未进入 `run_fixture.__globals__`，仍构造真实 DeepSeek adapter，并以占位凭据走到 `real.stream` 后返回 auth。未使用用户实际 API key；不能声称没有触发外网，也未保存 HTTP status，不能进一步断言确切响应或计费。该路径已停止。原 JSON 的外层 scripted 标签不可信，由单独 sidecar 撤销；批准0/原生投递0/未发布图片，不能计为有效坐标或 scripted 验收。此前 `native_loop.py` 的独立 ScriptedProvider 输入闭环不受此注入错误影响。

直接坐标组件另外被 image_not_published 拒绝，投递0；没有伪造发布事实。Luna 本轮未取得新的背景坐标滚动/前台 double/right 产品闭环证据，也未重跑 fresh Enter；先前矩阵与独立 AppKit/commit 证据仍保持原版本边界。Luna 原始记录与实验脚本由 [证据清单](assets/computer-use-repair-2026-10-04/luna-evidence-manifest.json) 索引，误路由由 [纠正旁注](assets/computer-use-repair-2026-10-04/luna-agentloop-background-pixel-scroll-correction.json) 撤销错误标签。

## 交付与后续条件

Morrow 代码与离线/GUI/build 修复已提交；Luna 诊断提交为 `f639c152`。真实验收仍有 Provider 余额、当前 fixture semantic click，以及官方 SDK 精确属性/容器与两种投递的缺口。未将整个方案标为完成。用户已有 benchmark/docs dirty 与 staged audit 保留，未提交。

关键证据 SHA-256（本地 raw assets，不随 Git 推送）：

- `live-matrix-v1.json`：`57f8443245721532b62925e95fc6db34ebbacd2e4b4516c7c79490997a26f8f7`
- `live-matrix.json`：`90851df27133649b7bbb3b4565d646c5e003813e5c601e83318de71a435980e9`
- `provider-diagnostic.json`：`b487da1a10b6c9a6d0248d131469a656b3d2999f4683907949e1b5bd47530f9b`
- `background-pointer-diagnostic.json`：`8bd3a7b97f4a1bffcd678e0005f04f3bb53263c33ef3c6e1bea03b81451c6b68`
- `foreground-wheel-active-diagnostic.json`：`b99dc35a39cfa37ca37a44f0d25df7d7a19ff524619bd5b80eeb6c7120b9d0e2`
- `source-manifest.json`：`5a0e63b62fd6a715940b0b75077d014fdd4d62ebd9a6b7e925ff520af0d3c32b`
- `sdk-contract.json`：`350c31b0153511e53e7a84b63f4029215e1dfd0bce17da23e541a9a7167edf76`

- `delivery-source-manifest.json`：`c834bc4db5d879b257093d7af1899aa5559c1175df4cc98c3640f65c6a3c4c34`（Luna 诊断前的源码版本，区别于真实矩阵启动时版本）
- `delivery-source-manifest-luna.json`：`4f09d0a844c82f5175f01f7f46debdd9d3b862a5bafd7dacf225956ae8322692`（Luna 后最终源码，`f639c152`，含753个文件hash；文档另行提交）
