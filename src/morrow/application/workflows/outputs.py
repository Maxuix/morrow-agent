"""The sole lineage-aware Workflow output lookup path."""

from morrow.core.workflows.contracts import ArtifactBinding


class EffectiveOutputResolver:
    """Resolve current-run outputs first, then exact immutable lineage imports."""

    def __init__(self, journal, *, workspace_id: str) -> None:
        self.journal = journal
        self.workspace_id = workspace_id

    def resolve(
        self, workflow_run_id: str, node_id: str, output_slot: str
    ) -> ArtifactBinding | None:
        return self.journal.workflows.get_effective_output(
            self.workspace_id, workflow_run_id, node_id, output_slot
        )

    def list_imports(self, workflow_run_id: str):
        return self.journal.workflows.list_artifact_imports(self.workspace_id, workflow_run_id)
