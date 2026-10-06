> Portable reading copy. Original bytes and SHA-256 are preserved in [the evidence manifest](../../../../manifest.json). Links alone are adapted for this repository.

# R3 repair rerun error report

Updated: 2026-09-24 08:39 Asia/Shanghai

Job `tb2-high-r3-repair-rerun` has finished all 15 authorized trials: 4 completed, 11 errored, 0 running, 0 pending. Among completed trials, one received official verifier reward 1.0 and three received 0.0. Reward-0 trials completed execution and are verifier outcomes, not execution errors.

## Timeouts

All 11 errors are `AgentTimeoutError`. Their result directories contain no D5 per-turn trace or terminal metrics. The evidence cannot distinguish extended model thinking from a tool loop, so these are not classified as model capability failures and are not rerun.

| Task | Elapsed | Trace | Assessment |
|---|---:|---|---|
| `compile-compcert` | 2431.6 s | Missing | Cause cannot be distinguished; no rerun. |
| `circuit-fibsqrt` | 3653.8 s | Missing | Cause cannot be distinguished; no rerun. |
| `dna-assembly` | 1914.2 s | Missing | Cause cannot be distinguished; no rerun. |
| `make-mips-interpreter` | 1892.0 s | Missing | Cause cannot be distinguished; no rerun. |
| `model-extraction-relu-logits` | 953.1 s | Missing | Cause cannot be distinguished; no rerun. |
| `path-tracing-reverse` | 1849.1 s | Missing | Cause cannot be distinguished; no rerun. |
| `path-tracing` | 1848.0 s | Missing | Cause cannot be distinguished; no rerun. |
| `rstan-to-pystan` | 1861.2 s | Missing | Cause cannot be distinguished; no rerun. |
| `regex-chess` | 3660.6 s | Missing | Cause cannot be distinguished; no rerun. |
| `schemelike-metacircular-eval` | 2519.0 s | Missing | Cause cannot be distinguished; no rerun. |
| `write-compressor` | 1006.0 s | Missing | Cause cannot be distinguished; no rerun. |

No authentication, network, Docker, or verifier failure marker was found in these timeout exceptions. Raw exception text was not copied into this report.

## Usage evidence

Reliable Morrow usage is available from four completed trial metric files: 1,667,596 input tokens and 28,347 output tokens. This is partial usage, not a full-batch total. The timeout trials did not produce usage metrics.
