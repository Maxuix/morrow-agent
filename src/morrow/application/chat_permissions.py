"""Safe permission inspection and revocation through the existing Core owner."""

from morrow.core.application import ApplicationError, ApplicationErrorCode


def permissions_view(
    context, session_id, *, run_id=None, run_cursor=None, grant_cursor=None, scope_cursor=""
):
    context.chat.require_session(session_id)
    api, journal, wid = context.api, context.journal, context.workspace_id
    ids, next_run = journal.chat_permissions.run_ids(
        wid, session_id, offset=api._offset(run_cursor, 50)
    )
    run_id = run_id or (ids[0] if ids else None)
    run = journal.get_agent_run(wid, run_id) if run_id else None
    if run_id and (run is None or run.session_id != session_id):
        raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "运行不属于当前对话")
    grant_ids, next_grant = (
        journal.chat_permissions.grant_ids(wid, run_id, offset=api._offset(grant_cursor, 50))
        if run
        else ([], None)
    )
    grants = []
    for gid in grant_ids:
        grant = api.get_grant(gid)
        grants.append(
            {
                "grant_id": gid,
                "row_version": grant.row_version,
                "capabilities": [c.value for c in grant.capabilities],
                "expires_at": grant.expires_at.isoformat(),
                "status": "revoked"
                if grant.revoked_at
                else "active"
                if grant.is_active(journal.now())
                else "expired",
            }
        )
    snapshot = None
    if run:
        runtime = run.snapshot.provider_runtime
        permission = (
            api.get_permission_snapshot(run.permission_snapshot_id)
            if run.permission_snapshot_id
            else None
        )
        snapshot = {
            "agent_run_id": run.agent_run_id,
            "model": run.snapshot.model.model_dump(mode="json"),
            "generation": runtime.generation.model_dump(mode="json") if runtime else {},
            "sources": {k: v.model_dump(mode="json") for k, v in runtime.settings_sources.items()}
            if runtime
            else {},
            "permission_preset": runtime.permission_preset if runtime else None,
            "permission": permission.model_dump(
                mode="json",
                include={
                    "permission_snapshot_id",
                    "access_scope",
                    "approval_mode",
                    "process_isolation",
                    "workspace_read_only",
                    "grant_id",
                    "granted_capabilities",
                },
            )
            if permission
            else None,
        }
    return {
        "run_ids": ids,
        "next_run_cursor": next_run,
        "snapshot": snapshot,
        "grants": grants,
        "next_grant_cursor": next_grant,
        "session_scopes": journal.session_scopes.list(wid, session_id, cursor=scope_cursor),
    }


def revoke_permission(context, session_id, request):
    context.chat.require_session(session_id)
    api, journal, wid = context.api, context.journal, context.workspace_id
    if request.kind == "grant":
        grant = api.get_grant(request.subject_id)
        run = journal.get_agent_run(wid, grant.agent_run_id) if grant else None
        if run is None or run.session_id != session_id:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "授权不属于当前对话")
        result = api.revoke_grant(
            request.subject_id,
            expected_row_version=request.expected_revision,
            command_id=request.command_id,
            reason="revoked by local Chat interface",
        )
        # Revocation commits before waking any waiting tool; durable authority is rechecked.
        for approval in journal.list_approvals_for_grant(wid, grant.grant_id):
            if approval.revoked_at and context.approval_waiters.claim(approval.approval_id):
                context.approval_waiters.deliver_claimed(approval.approval_id, approved=False)
        disposition = result.receipt.disposition.value
    else:
        approval = journal.get_approval(wid, request.subject_id)
        execution = journal.get_execution(wid, approval.tool_execution_id) if approval else None
        if execution is None or execution.session_id != session_id:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "授权不属于当前对话")
        operation = "chat_session_scope_revoke"
        command_id, digest, replay = api._prepare(
            operation,
            {
                "session_id": session_id,
                "approval_id": approval.approval_id,
                "expected_revision": request.expected_revision,
            },
            request.command_id,
        )
        if replay is None:

            def work(txn):
                journal.session_scopes.revoke(wid, session_id, approval, request.expected_revision)
                return api._receipt(
                    txn,
                    command_id=command_id,
                    operation=operation,
                    digest=digest,
                    session_id=session_id,
                    result_kind="approval_scope",
                    result_id=approval.approval_id,
                    event_cursor=None,
                )

            replay = journal.transact(work)
        disposition = replay.disposition.value
    context.chat.streams.changed(session_id)
    return {"disposition": disposition}
