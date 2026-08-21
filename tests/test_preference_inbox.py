from __future__ import annotations

from morrow.adapters.state.preference_yaml import PreferenceYamlStore
from morrow.application.preferences.inbox import PreferenceInbox, PreferenceInboxError
from morrow.application.preferences.proposals import PreferenceProposalPipeline
from morrow.application.preferences.writer import PreferenceWriter
from morrow.core.preference_persistence_models import PreferenceProposalStatus
from morrow.core.preference_review import PreferenceReviewOutput
from morrow.testing import FixedClock, FixedIdSource
from test_preference_proposals import _journal, _operation, _source


def _inbox(tmp_path, *, operation_count: int = 1):
    store, session, journal = _journal(tmp_path)
    job, evidence = _source()
    journal.put_preference_job_with_evidence("ws_1", job, evidence)
    operations = tuple(
        _operation("add", statement=f"规则 {index}") for index in range(operation_count)
    )
    proposals = PreferenceProposalPipeline(
        journal=journal,
        workspace_id="ws_1",
        id_source=FixedIdSource(),
        clock=FixedClock(),
    ).persist(job, evidence, PreferenceReviewOutput(operations=operations))
    writer = PreferenceWriter(
        PreferenceYamlStore(tmp_path / "config"),
        journal,
        "ws_1",
        id_source=FixedIdSource(),
        clock=FixedClock(),
    )
    inbox = PreferenceInbox(
        journal=journal,
        workspace_id="ws_1",
        writer=writer,
        id_source=FixedIdSource(),
        clock=FixedClock(),
    )
    return store, session, journal, writer, inbox, proposals


def test_inbox_preview_and_edit_accept_use_the_same_occ_tokens(tmp_path):
    store, session, journal, _writer, inbox, result = _inbox(tmp_path)
    try:
        proposal = result.proposals[0]
        preview = inbox.preview(proposal.proposal_id, edit="编辑后的规则")
        assert preview.available is True
        accepted = inbox.accept(
            proposal.proposal_id,
            edit="编辑后的规则",
            expected_row_version=preview.expected_row_version,
            expected_document_revision=preview.expected_document_revision,
        )
        assert accepted.document_revision == 1
        saved = journal.get_preference_proposal("ws_1", proposal.proposal_id)
        assert saved is not None
        assert saved.status is PreferenceProposalStatus.EDITED_AND_ACCEPTED
        assert saved.final_operation is not None
        assert saved.final_operation.evidence_ids == ("pev_one",)
    finally:
        session.close()
        assert store.layout.database.exists()


def test_inbox_accept_many_writes_one_same_scope_yaml_revision(tmp_path):
    store, session, journal, _writer, inbox, result = _inbox(tmp_path, operation_count=2)
    try:
        ids = tuple(item.proposal_id for item in result.proposals)
        page = inbox.list()
        assert tuple(item.proposal_id for item in page.items) == ids
        accepted = inbox.accept_many(ids, command_id="cmd_many")
        assert accepted.document_revision == 1
        assert accepted.batch_id is not None
        assert all(item.status is PreferenceProposalStatus.ACCEPTED for item in accepted.proposals)
    finally:
        session.close()
        assert store.layout.database.exists()


def test_inbox_exposes_stale_target_and_never_silently_retargets(tmp_path):
    store, session, journal, writer, inbox, result = _inbox(tmp_path)
    try:
        proposal = result.proposals[0]
        writer.add("workspace", 0, "cmd_external", "外部变更")
        preview = inbox.preview(proposal.proposal_id)
        assert preview.available is False
        assert preview.proposal.stale is True
        assert preview.reason == "document_revision"
        try:
            inbox.accept(proposal.proposal_id)
        except PreferenceInboxError as error:
            assert error.code == "document_revision"
        else:  # pragma: no cover - the stale guard is the assertion
            raise AssertionError("stale proposal was accepted")
    finally:
        session.close()
        assert store.layout.database.exists()
