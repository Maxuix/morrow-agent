# Subplan 6 — Serial Multi-Agent Artifact Pipeline

- [x] Task 1: ChangeArtifactCapture gate at durable tool handler-completion (complete before
  pipeline assembly).
- [x] Task 2: internal `submit_node_result` mechanism tool + committer extension for structured
  slots and capture-derived ImplementationPatch/TestReport.
- [x] Task 3: published Coder and Reviewer Versions; Explorer reused; declared tool requirements.
- [x] Task 4: isolated leaf Session/TaskRun + Artifact-only input rendering.
- [x] Task 5: Explorer -> Coder -> Reviewer with Coder sandbox bash and Host-bash rejection.
- [x] Task 6: truthful `needs_revision` from exported ReviewReport blocking verdicts.
- [x] Task 7: materialization/submission failure mapping; known side effects preserved.
- [x] Task 8: Artifact cannot expand ToolSet/permission; `submit_node_result` cannot be smuggled.
- [x] Task 9: focused offline matrix in `tests/test_stage7_multi_agent_pipeline.py` plus declared
  regression files and full offline gate.

Subplan 6 is complete. Implementation commit: `bfcf869` on `feat/stage7-multi-agent-pipeline`.
Offline gate: 1483 passed, 2 Live deselected; ruff format/check, compileall and `git diff --check`
clean. Remote publication remains blocked pending explicit authorization; no Live tests were run.
