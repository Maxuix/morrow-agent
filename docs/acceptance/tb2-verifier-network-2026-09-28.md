# TB2 官方 verifier 下载出口修复验收

日期：2026-09-28。范围：`diagnostic-12-c4cdb8acecb2` 的 verifier 网络出口；不改官方任务或评分脚本。

## 故障证据

- `configure-git-webserver` verifier 在 900 秒超时，stdout 最后停在下载 `uv 0.9.5`。
- `financial-document-processor` verifier 在 1200 秒超时，stdout 停在下载 CPython 3.13.9。
- 两个任务的官方 `tests/test.sh` 都会独立安装 `uv`，再通过 `uvx` 获取 Python 和测试依赖；Morrow 的离线安装资产不覆盖这些 verifier 下载。
- 宿主机经 `127.0.0.1:6152` 代理可完整下载 GitHub 的 `uv` release。Docker Desktop 容器直连该代理或 `http.docker.internal:3128` 曾出现 `SSL_ERROR_SYSCALL` / TLS 隧道中断；仅有 TCP 可达或 CONNECT 200 不足以证明下载可用。

## 修复

TB2 驱动读取宿主机代理配置，只用 Harbor 的 `--verifier-env` 把代理交给 verifier。宿主机代理监听 loopback 时，驱动在评测运行期间启动临时 TCP 转发：容器连接 `host.docker.internal:<临时端口>`，转发进程从宿主机连接原代理，运行结束即关闭。代理配置摘要进入运行指纹；端口不进入指纹，因为每次启动都会变化。显式的非 loopback `MORROW_BENCH_VERIFIER_PROXY_URL` 可直接供容器使用。含凭据或格式错误的代理 URL 在预算接纳前拒绝。

官方 task.toml、verifier 脚本、reward 读取方式和 agent 安装 PATH 均未改变。原运行的两项超时没有官方 reward，不能归入模型得分；改变驱动后须开新 run，不能以新的指纹续用原 run。

## 验证

- 容器内探测：用正式 `VerifierProxyRelay` 在 `alexgshaw/financial-document-processor:20251031` 临时容器中按官方脚本的依赖版本获取 `uv 0.9.5`、CPython 3.13、`pytest==8.4.1`、`pandas==2.3.2`、`pytest-json-ctrf==0.3.5`，最后 `pytest --version` 返回 `pytest 8.4.1`，命令退出 0。未运行官方测试、未产生 reward，也未发起模型请求。
- 定向测试：`PYTHONPATH=evals/benchmarks uv run pytest -q evals/benchmarks/tests/test_run_tb2.py evals/benchmarks/tests/test_verifier_proxy.py` → 11 passed。
- `uv run pytest -m 'not live' -q` → 2564 passed、2 deselected。
- `uv run ruff format --check .`、`uv run ruff check .`、`uv run python -m compileall -q src tests`、`git diff --check` → 通过。

未对已暂停的 diagnostic job 执行续跑或重评分。
