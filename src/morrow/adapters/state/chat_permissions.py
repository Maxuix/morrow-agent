"""Bounded subject indexes; application services own permission projection and mutation."""


class ChatPermissionJournal:
    def __init__(self, backend):
        self.backend = backend

    def run_ids(self, workspace_id, session_id, *, offset=0, limit=50):
        rows = self.backend.read_all(
            "SELECT r.agent_run_id FROM agent_runs r JOIN sessions s ON s.session_id=r.session_id "
            "WHERE s.workspace_id=? AND r.session_id=? ORDER BY r.rowid DESC LIMIT ? OFFSET ?",
            (workspace_id, session_id, limit + 1, offset),
        )
        return [r[0] for r in rows[:limit]], str(offset + limit) if len(rows) > limit else None

    def grant_ids(self, workspace_id, agent_run_id, *, offset=0, limit=50):
        rows = self.backend.read_all(
            "SELECT grant_id FROM capability_grants WHERE workspace_id=? AND agent_run_id=? "
            "ORDER BY rowid DESC LIMIT ? OFFSET ?",
            (workspace_id, agent_run_id, limit + 1, offset),
        )
        return [r[0] for r in rows[:limit]], str(offset + limit) if len(rows) > limit else None
