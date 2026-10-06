> Portable reading copy. Original bytes and SHA-256 are preserved in [the evidence manifest](../../../../manifest.json). Links alone are adapted for this repository.

# TB2 high-effort pilot report

## Scope and configuration

- Scope: same 25-task set as `tb2-pilot-low` (Terminal-Bench 2.0 pilot), not the 89-task full suite.
- Reasoning effort: `high` only; no other effort levels are included in this run.
- Provider/model: Volcengine OpenAI-compatible API, `glm-5.3-flash`.
- Concurrency: 2; agent timeout multiplier: 1.0 (same as the original low pilot).
- The benchmark runner's default reasoning effort has been set to `high` for future runs; this job also explicitly selected `high`.
- Job: `tb2-high-pilot-rerun`.
- Credential: reused from the prior run; intentionally not recorded.
- Environment correction: `assets/uvx-wrapper` now bases the NumPy compatibility fallback on the interpreter actually selected. If requested Python 3.11 is unavailable and the bundled Python 3.12 fallback is used, the wrapper substitutes a wheel-supported NumPy release for the task's NumPy 1.24 pin; task tests and thresholds are unchanged.

## Status at launch

- Started: 2026-09-22 18:15:15 Asia/Shanghai.
- Harbor accepted all 25 pilot tasks. Initial counts: 0 completed, 0 errored, 2 running, 23 pending.
- All 25 tasks were admitted with 1,000,000-token reservations. After admission, the shared ledger showed 59,440,486 charged/reserved tokens of 100,000,000, leaving 40,559,514.
- Job artifacts: `runs/jobs/high/tb2-high-pilot-rerun/`.
- `run_tb2.py` compile check and `uvx-wrapper` shell syntax check passed. A dry-run with reasoning effort omitted resolved to `high`, confirming the new default.

## Results

### Final summary

- Completed: 25/25 trials; Harbor job finished at 2026-09-22 20:39:50 Asia/Shanghai (elapsed 2 h 24 min 35 s). No trials were retried, cancelled, left running, or pending.
- Mean reward: **0.48** (12/25 passed; 13/25 scored 0).
- Eight trials ended with `AgentTimeoutError`; the remaining 17 had no Harbor agent-timeout exception. The timeout trials have no available per-trial token usage.
- Available Morrow usage metrics: **3,360,010 input + 141,210 output = 3,501,220 tokens** across 16 trials. `train-fasttext` emitted a metrics file but usage was unavailable; the eight timed-out trials emitted no usage metrics. Cost is unavailable.
- Evidence sources: `runs/jobs/high/tb2-high-pilot-rerun/result.json`, each trial's `result.json`, `agent/morrow-terminal-metrics.json`, verifier outputs, `job.log`, and `runs/budget-ledger.json`.

### Per-task results

Elapsed seconds are full trial wall time (setup, agent, and verifier); token counts are Morrow usage when available. `n/a` means unavailable, not zero.

| Task | Reward | Exception | Timeout cap (s) | Elapsed (s) | Input tokens | Output tokens |
|---|---:|---|---:|---:|---:|---:|
| adaptive-rejection-sampler | 0 | — | — | 861 | 267,113 | 33,263 |
| bn-fit-modify | 0 | — | — | 423 | 481,715 | 9,913 |
| break-filter-js-from-html | 1 | — | — | 526 | 133,203 | 15,836 |
| build-cython-ext | 1 | — | — | 626 | 754,528 | 8,225 |
| build-pmars | 0 | — | — | 134 | 80,407 | 1,981 |
| caffe-cifar-10 | 0 | AgentTimeoutError | 1,200 | 1,262 | n/a | n/a |
| chess-best-move | 0 | AgentTimeoutError | 900 | 934 | n/a | n/a |
| cobol-modernization | 0 | AgentTimeoutError | 900 | 935 | n/a | n/a |
| constraints-scheduling | 1 | — | — | 76 | 27,198 | 2,176 |
| count-dataset-tokens | 1 | AgentTimeoutError | 900 | 973 | n/a | n/a |
| db-wal-recovery | 1 | — | — | 69 | 33,550 | 1,032 |
| extract-moves-from-video | 0 | AgentTimeoutError | 1,800 | 1,823 | n/a | n/a |
| feal-differential-cryptanalysis | 1 | — | — | 822 | 104,564 | 17,591 |
| financial-document-processor | 0 | — | — | 263 | 414,618 | 5,131 |
| fix-code-vulnerability | 1 | — | — | 67 | 55,080 | 1,153 |
| fix-git | 1 | — | — | 70 | 84,204 | 1,348 |
| hf-model-inference | 1 | — | — | 206 | 135,364 | 3,372 |
| largest-eigenval | 0 | AgentTimeoutError | 900 | 920 | n/a | n/a |
| llm-inference-batching-scheduler | 0 | — | — | 766 | 372,311 | 27,387 |
| mcmc-sampling-stan | 0 | AgentTimeoutError | 1,800 | 1,824 | n/a | n/a |
| overfull-hbox | 0 | AgentTimeoutError | 750 | 801 | n/a | n/a |
| portfolio-optimization | 1 | — | — | 220 | 86,946 | 3,238 |
| prove-plus-comm | 1 | — | — | 51 | 36,686 | 609 |
| sparql-university | 1 | — | — | 447 | 292,523 | 8,955 |
| train-fasttext | 0 | — | — | 1,217 | n/a | n/a |

### Timeout and verifier evidence

- All eight exceptions are Harbor `AgentTimeoutError` results at the per-task agent wall-time cap. The recorded traceback shows Harbor's `asyncio.wait_for` expiring while awaiting the agent's Docker `environment.exec` output; it is not a verifier setup exception. This run provides no evidence that the timeouts were caused by the benchmark runner being blocked, so no timeout task was blindly rerun.
- The `train-fasttext` verifier ran under **Python 3.12.14**. Its captured installer output selected `numpy>=1.26.0` and successfully installed the `numpy-2.5.3-cp312` wheel. `pytest-json-ctrf` ran and produced `verifier/ctrf.json` with 2 tests, 0 passed, 2 failed. Both failures are because `/app/model.bin` was absent (`test_accuracy` cannot load it; `test_model_size` cannot stat it). The NumPy/Python compatibility repair therefore worked; the reward 0 is due to the task output not being produced, not a verifier/import/CTRF environment failure. Task tests and verifier definitions were not changed.
- The agent run returned `MORROW_EXIT=2` for this task; the Harbor trial itself completed with reward 0 and no `exception_info`.

### Budget accounting

- This job admitted 25 tasks at 1,000,000 tokens each: **25,000,000 budget tokens reserved/charged** under the ledger's conservative per-task floor. Seventeen entries are `finalized`; eight timeout entries remain `admitted` because no usage log was available to finalize them. Their reservations remain counted rather than silently treated as zero.
- After the job, the shared 100,000,000-token ledger totals were 59,440,486 `charged_tokens` and 59,000,000 in reservations, leaving **40,559,514** under the ledger's `budget_total - charged_tokens` accounting. These ledger charges are budget accounting, not a claim that the model consumed 25,000,000 tokens; observed available Morrow usage for this job was 3,501,220 tokens.
