# 版本收尾逐项修复 — 2026-10-06

基线：main `9b3e5bf5191d152f91feaa54392b52dbf0bae597`，加用户原有工作区。
本轮根据二次复核 v2 review 修复 R01–R15，不开启新产品阶段、不发布新版本。
R01–R15已处理并通过相关门禁；保留API、用户原工作和外部验收缺口的范围如下。

## Findings 对照

| 条目 | 修复与验证依据 |
| --- | --- |
| R01 | 每个 tracked execution 在内存保留启动时精确脱敏字节；poll/stop 合并启动/当前规则，保留所有权、原 byte cursor、UTF-8 和边界扫描。回归覆盖双通道、短游标、acceptance 跨 Task 和普通 Task 隔离。 |
| R02 | 删除 Provider 只删除配置，保留已发布 Keyring ref，保证 immutable 运行恢复。失败创建/发布的临时 ref 仍清理；没有新增凭据表或引用扫描。 |
| R03 | 冻结非敏感 `credential_source=environment/keyring`，恢复只读原来源。env 消失失败关闭，Keyring 不受新 env 覆盖。旧快照缺字段时维持 Keyring-only 及原序列化形状，不存 secret/secret hash。 |
| R04 | SyncStore start/stop 可重启，各 await 后检查生命周期代次；旧响应不发布状态/开 socket。Draft 可在 StrictMode 重挂后激活，disposed 时保存明确结束，在途未知保存保留原编号与输入。StrictMode DOM/受控 Promise 回归。 |
| R05 | 有缺陷入口只在明确 4xx 拒绝时清编号，status 0 与既有 5xx 均保留；未知结果读回并真实表述。偏好重试冻结完整命令及原revision，读回推进revision后也不会生成第二次新增。确认写入后的读回失败分开处理，正确的 Diagnostics/Review 入口保持原规则。 |
| R06 | refresh/loadMore 检查查询代次、workspace request/cursor，丢弃旧过滤结果与错误，并保留请求期间插入的会话。受控 Promise 回归。 |
| R07 | Vite `/v1` 设置 `ws:true`。真实本地 Vite 与合同上游验证 HTTP200/WS101、accept hash 及上游 Host/Origin/Referer；无外网，不声称真实浏览器或 Morrow 服务进程验收。 |
| R08 | 保留公开 preview API，URL 编码 ID 与路径，保留目录分隔符。loopback GET 覆盖中文、空格、#、?、% 与字面 %2F，返回登记字节。 |
| R09 | wheel 对照全部 `morrow/` 文件，只按 Hatch 显式规则排除 __pycache__/.pyc；TOML、JSON、缺/多资源均参与。保留工作区原 bytecode hash 修复。 |
| R10 | [当前运行时文档](../architecture/runtime.md) 说明累计有工具回合的有限 completion review、最多两次、额外请求、请求预算、deadline≤15秒及 Workflow leaf 边界；算法不变。 |
| R11 | [Portable evidence](portable/README.md)：185份 raw（7,197,016字节）和16份阅读副本，原件逐字节复制+SHA。已 tracked sidecar 不改，source 用 manifest 映射。原本 staged 新审计报告公开为初始字节原件+阅读副本，不消费原 staged 文件。G4保留aggregate和89份structured trial结果；完整agentstream仍本地，目录清单明确此边界，不称完整raw复算包。 |
| R12 | 明确 Git公开/local档案/wheel/sdist边界；[公开验收索引](README.md) 和窄 allow 与选定材料一致。 |
| R13 | 旧 computer-use master/子计划归档，本轮活动索引只记录当前状态；真实 API 模型矩阵余额缺口仍未完成，不能由 offline 转为完成。`.agent` 仍为本地执行材料。 |
| R14 | 本地当前 chat-workbench 指南对齐“项目知识与偏好”；serve 注释符合地址/私有连接文件认证输出；GUI 注释符合 contract 1.1.0。 |
| R15 | 原用户 run_tb2.py 工作仅格式化，全仓 Ruff format 门禁通过。原 verifier 配置工作继续留在工作区，未夹带提交。 |

凭据、准备与预览的对应测试见 [credential lifecycle](../../tests/test_credential_runtime_lifecycle.py)、
[agent preparation](../../tests/test_agent_run_preparation.py)、[preview](../../tests/test_stage9_html_preview.py)。
GUI 对应 [Sync](../../gui/src/state/sync.dom.test.tsx)、[Draft](../../gui/src/state/workflowDraft.test.ts)、
[管理命令](../../gui/src/state/managementWrites.dom.test.tsx)、[代理](../../gui/src/api/proxy.test.ts)。

## 清理与维护性

删除生产不可达的 SourceEditor/lineDiff 及独立测试、SourceLines、FileDownload 与其独立 case、webglAvailable。
保留 BinaryPreview、混合测试、FileBufferStore 读取、公开 preview/WorkspaceFile PUT、恢复 legacy、动态 adapter 和 vendor 来源。
测试 demo lookup/calculator 移入 [morrow.testing](../../src/morrow/testing.py)，原行为测试保留，生产工具 registry 缩减。
没有按体积删除 raw/vendor/发行产物，没有删除用户 `.agents/` 或重写历史判定。

当前维护性/扩展性仍依赖清楚的现有所有权边界；本轮缩减不可达符号并修生命周期，未把大文件体量当成重写理由。
额外采用两项有界维护改进：owned Provider 沿既有异步收尾关闭，保护 injected/shared；
MCP pool 等待 lazy startup 完成后再 drain，关闭后不开始远程调用。没有新增框架、状态机或存储层。
CSS 仍按120KiB原预算验证；未用提高预算解决余量问题。
CI自动化与大文件责任提取仍属可选后续建议，当前未新增质量平台或整体架构重写。

## 验证

- GUI：typecheck、109 files / 736 tests、build+原预算通过。最终 JS1782.1KiB/1900、最大chunk467.2KiB/500、CSS119.5KiB/120，余量464字节。
- Python 全量：最终3200 passed、2 deselected，210.35秒。首轮3185 passed/15 failed是两处旧fake夹具未同步：缺startup secret_bytes、SDK fake缺close；更新夹具后定向54 passed及最终全量通过，未削弱原行为断言。
- Benchmark 工作区独立venv：47 tests OK；纯修复提交树另47 tests OK。
- Ruff check/format、compileall、CLI help、diff-check：通过。
- wheel/sdist 已重建，与全部包内源码/资源一致。独立wheel安装smoke在Python3.12.13/3.13.0、有/无SDK均通过，网络连接和SDK导入尝试为0。已从修复增量的Git导出树构建发行包，包内源码/资源一致，sdist不含本地状态/缓存。
- Fix-only Git archive：185 raw SHA、恢复sidecar→原件链及365本地导航通过；提交后按相同入口核对。

无 Live Provider、外部 MCP、真实 benchmark campaign 或 native 桌面输入。
既有 computer-use 1.7 API模型全矩阵因余额条件未满足继续保留，不影响本轮离线修复交付。

安装仅使用已有依赖与已验证SDK字节，未发起live。首次未锁版本的offline安装因缓存缺少新依赖失败，
改用现有uv.lock导出runtime版本后成功；SDK默认URL不在缓存，改用本机已与发布SHA一致的功能SDK wheel，
不声称本轮验证了远程下载。首次archive检查暴露G4 staged来源和两处ignored vendor/job引用，已补映射后通过。

便携检查入口：[verify_public_evidence.py](../../evals/computer_use/verify_public_evidence.py)，
只校验导出树中的字节和本地导航，不执行历史脚本。raw保留生成日志空白，通过该目录
.gitattributes的限定whitespace规则保护原字节；阅读副本正常清理空白。

本轮日志：[最终Python](evidence/version-closeout-2026-10-06/pytest-final.log)、
[首轮夹具失败](evidence/version-closeout-2026-10-06/pytest-first-run.log)、
[GUI测试](evidence/version-closeout-2026-10-06/gui-tests.log)、
[GUI构建预算](evidence/version-closeout-2026-10-06/gui-build.log)、
[Git导出树benchmark](evidence/version-closeout-2026-10-06/benchmark-export.log)。

发行安装记录：[Python3.12含SDK](evidence/version-closeout-2026-10-06/release-smoke312-present.json)、
[Python3.13无SDK](evidence/version-closeout-2026-10-06/release-smoke313-absent.json)、
[原件/导航检查](evidence/version-closeout-2026-10-06/portable-check.log)。
发行产物仅保存在本地临时目录，本轮没有上传发行包、升级版本或新增真实API/native验收。
