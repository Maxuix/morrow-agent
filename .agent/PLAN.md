# Stage 7 Preflight Reliability — S7P-09 Repeated Direct/Pi Baseline

> Status: active but blocked; legacy tool cleanup verified, 50M capacity insufficient
> Active subplan: Subplan 90 — S7P-09 Repeated Direct Evaluation and Same-Condition Pi Baseline
> Execution branch: `feat/s7p-09-direct-pi-baseline-run`
> Stack base: verified local `main@1fd7e229bef276d1a0361e775ce800ade4b318fc`
> Source authority: current user request, S7P-00 protocol v1, completed S7P-08/S7P-91, code/tools

## 1. Current objective

Refreeze and run the auditable S7P-09 campaign: 10 Morrow tasks × 2 repetitions and the four
protocol-pinned Pi tasks × 2 repetitions under the same Provider/model and a new content-hashed
comparison contract. The next Morrow profile uses the verified Pi-aligned `read`, `bash`, `edit`,
`write`, `grep`, `find`, and `ls` core surface.

Detailed result contracts, immutable schedule rules, thresholds, failure handling and retention
policy remain owned by Subplan 90. Subplan 91 changed the product profile, not S7P-00 tasks or gates.

## 2. Frozen decisions

- S7P-00 protocol v1 thresholds, task bytes, Gold states and verifiers remain immutable.
- A formal campaign contains exactly 28 admitted runs: Morrow 20 and Pi 8; observed runs are never
  discarded or replaced.
- Both Agents use `opencode-go/mimo-v2.5`, the same sampling contract, baseline tree, verifier and
  1,800-second external deadline.
- Product-native prompts may differ. The seven core tool names/fields now align with Pi; permission
  and execution-side confinement remain equivalent and content-hashed.
- Morrow still uses ordinary bootstrap, TaskRun, AgentRun, AgentLoop, ToolExecutor, permission,
  recovery and ConversationLog boundaries.
- Raw events, reasoning, full tool payloads, credentials and tracebacks stay outside Git.

## 3. Live hold point

The user explicitly requested cleanup of the legacy model-facing tool layer after Subplan 91.
Subplan 92 removed obsolete Provider schemas, argument models and factories while retaining
underlying services and narrowly separated historical recovery metadata. The verified cleanup is
integrated and control has returned to Subplan 90.

After Subplan 92 is verified and integrated, the following Subplan 90 capacity hold still applies:

No retained campaign or pre-repair profile may be continued. Formal attempts have accounted for
16,754,419 known tokens plus six requests with unavailable usage. The user-approved hard total is
50,000,000 tokens with no currency ceiling, leaving at most 33,245,581 known-token capacity before
allowance for the six unknown requests.

The ordinary new profile has 9 tools and 5,923 canonical schema bytes, down from 15 tools and
11,951 bytes; its seven core tools use 2,742 bytes. This does not change the 28-run conservative
reservation: 28 × 1,500,000 = 42,000,000 tokens. Added to retained known usage, the minimum total is
58,754,419 tokens before any allowance for the six unknown-usage requests. The approved 50M cannot
admit a campaign that is expected to complete. Record the blocker rather than admitting a partial
campaign or silently changing the protocol.

## 4. Execution order

1. Obtain a total token ceiling that covers the retained usage, fresh 42M reservation and unknown
   request allowance; 80M remains the recommended safe ceiling.
2. Rebuild the comparison plan using a clean source pin and the cleaned Morrow profile.
3. Execute and immediately finalize all 28 immutable entries sequentially after capacity passes.
4. Mechanically aggregate, classify, publish acceptance evidence and run final offline gates.

## 5. Completion

S7P-09 completes only with all 28 primary bundles, mandatory token evidence, exact failure/tool
accounting, frozen Morrow thresholds, Pi quality deficit ≤ 1, safe evidence publication and all
offline quality gates. Insufficient approved capacity is BLOCKED, never a conditional PASS.
