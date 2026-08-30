# Subplan 98 — S7P-10 GO Upgrade Proof

> Status: active
> Branch: `chore/s7p10-go-proof`
> Activation base: local `main@59789bc`
> Authorization: the user explicitly requested execution after the CONDITIONAL GO decision

## Outcome

Run one bounded current-code complex Morrow task in an isolated workspace and state root. Upgrade
S7P-10 to GO only if the run completes or returns a safely attributable non-runtime terminal with
complete tool accounting, and no leaf runtime blocker remains.

## Selected proof

- Task: `EXTERNAL-003` (difficult, Reactive Cells stable propagation).
- Reason: it is a multi-file implementation/validation task that passed once on the older r20
  source and therefore tests current-main non-regression without selecting an unproven easy case.
- Runtime: current `main`, current configured `opencode-go/deepseek-v4-flash`, isolated config that
  retains only the matching Provider/Model and CredentialRef.
- Limits: ordinary v2 RunPolicy plus the evaluator's 1,800-second external bound; no task network,
  outside-workspace access, Git mutation or privilege escalation.

## Execution

1. Freeze the current commit and create a mode-0700 evidence root outside the repository.
2. Prepare a fresh EXTERNAL-003 baseline workspace and isolated Morrow state.
3. Run exactly one Morrow AgentRun through the ordinary bootstrap/AgentLoop/ToolExecutor path.
4. Run the frozen verifier and capture only normalized safe runtime evidence, workspace status and
   hashes; do not publish raw prompt/response/tool content.
5. If a leaf runtime defect appears, repair only that defect, validate it and rerun this one proof.
6. Publish the GO/CONDITIONAL GO/NO-GO result and run complete offline/static gates.

## Boundaries

- Do not rerun the 14-run S7P-09 campaign or Pi.
- Do not mutate the protected r19/r20 evidence.
- Do not read, print or copy credential values; the CredentialStore resolves the existing reference.
- Do not implement Workflow behavior in this subplan.
- Model-quality or verifier failure is acceptable only when the terminal is attributable and proves
  no Morrow runtime/basic-tool blocker; GO still requires truthful reporting of task quality.

## Completion

- One current-code difficult run has a verifier result and normalized terminal/tool evidence.
- Every tool call has exactly one terminal state and no unexpected path or false-diff success exists.
- The S7P-10 verdict is updated without overstating one-sample quality.
- Focused and complete offline/static gates pass; verified changes are integrated into local `main`.
