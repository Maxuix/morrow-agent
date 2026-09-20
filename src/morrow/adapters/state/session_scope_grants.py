"""Revocation generations over existing exact Session approval scopes.

Historical decisions stay immutable. A revoked precedent and all approvals
derived from it lose future authority; a later explicit decision binds anew.
"""

from morrow.core.application import ApplicationError, ApplicationErrorCode


class SessionScopeGrants:
    def __init__(self, backend, get_execution):
        self.backend, self.get_execution = backend, get_execution

    def revision(self, session_id, scope):
        row = self.backend.read_one(
            "SELECT revision FROM session_scope_revisions WHERE session_id=? AND scope=?",
            (session_id, scope),
        )
        return row[0] if row else 0

    def bind(self, workspace_id, approval):
        if not (approval.granted_scope or "").startswith("session:"):
            return
        execution = self.get_execution(workspace_id, approval.tool_execution_id)
        revision = self.revision(execution.session_id, approval.granted_scope)
        self.backend.executor().execute(
            "UPDATE approvals SET scope_revision=? WHERE approval_id=? AND scope_revision IS NULL",
            (revision, approval.approval_id),
        )

    def active(self, workspace_id, session_id, approval):
        if not (approval.granted_scope or "").startswith("session:"):
            return True
        execution = self.get_execution(workspace_id, approval.tool_execution_id)
        if execution is None or execution.session_id != session_id:
            return False
        row = self.backend.read_one(
            "SELECT scope_revision FROM approvals WHERE approval_id=?",
            (approval.approval_id,),
        )
        return (row[0] if row else 0) == self.revision(session_id, approval.granted_scope)

    def list(self, workspace_id, session_id, *, cursor="", limit=50):
        rows = self.backend.read_all(
            "SELECT a.granted_scope, COALESCE(s.revision,0), MAX(a.approval_id) FROM approvals a "
            "JOIN tool_executions e ON e.tool_execution_id=a.tool_execution_id "
            "LEFT JOIN session_scope_revisions s ON s.session_id=e.session_id AND s.scope=a.granted_scope "
            "WHERE e.workspace_id=? AND e.session_id=? AND a.granted_scope LIKE 'session:%' "
            "AND a.resolution='approved' AND a.revoked_at_unix IS NULL "
            "AND COALESCE(a.scope_revision,0)=COALESCE(s.revision,0) AND a.granted_scope>? "
            "GROUP BY a.granted_scope ORDER BY a.granted_scope LIMIT ?",
            (workspace_id, session_id, cursor, limit + 1),
        )
        return {
            "items": [{"scope": r[0], "revision": r[1], "approval_id": r[2]} for r in rows[:limit]],
            "next_cursor": rows[limit - 1][0] if len(rows) > limit else None,
        }

    def revoke(self, workspace_id, session_id, approval, expected_revision):
        if not (approval.granted_scope or "").startswith("session:"):
            raise ApplicationError(ApplicationErrorCode.INVALID, "该审批没有会话范围授权")
        if (
            not self.active(workspace_id, session_id, approval)
            or self.revision(session_id, approval.granted_scope) != expected_revision
        ):
            raise ApplicationError(ApplicationErrorCode.STALE, "会话授权已变化，请刷新")
        self.backend.executor().execute(
            "INSERT INTO session_scope_revisions VALUES (?,?,1) ON CONFLICT(session_id,scope) DO UPDATE SET revision=revision+1",
            (session_id, approval.granted_scope),
        )
