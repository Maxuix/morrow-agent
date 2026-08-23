from __future__ import annotations

from morrow.application.context import ContextBuilder
from morrow.application.learning.memory_terms import refresh_project_knowledge_terms
from morrow.core.learning_memory import ProjectKnowledgeRevision
from morrow.core.models import AssistantMessage, FinishReason, Preferences, Profile
from morrow.runtime.session import Session
from morrow.testing import FixedIdSource, make_context_builder, seed_user_turn
from test_stage5_memory_agent_run import (
    _knowledge_subject,
    _open,
    _persistence,
    _seed_terms,
    _session,
)


def _system_message(pack, marker: str):
    return next(
        message
        for message in pack.messages
        if message.role == "system"
        and message.content.startswith("以下是")
        and marker in message.content
    )


def test_context_builder_uses_frozen_preferences_and_memory(tmp_path):
    handle, journal, clock = _open(tmp_path)
    try:
        _seed_terms(journal)
        session = _session()
        ids = FixedIdSource()
        ids.counts.update({"task": 1, "ttr": 2})
        persistence = _persistence(journal, handle, ids=ids, clock=clock, session=session)
        result = persistence.submit_user(
            session,
            "Operational SQLite",
            "client_context",
            turn_id="turn_context",
            agent_run_id="arun_context",
        )
        assert result.kind == "accepted"
        assert session.run_context_projection is not None

        builder = make_context_builder()
        before = builder.build(session)
        preference_before = _system_message(before, "冻结的用户 Preferences")
        memory_before = _system_message(before, "冻结的 Project Knowledge")
        assert "回答时默认使用 zh。" in preference_before.content
        assert "[session:" in preference_before.content
        assert '"semantic_key":"architecture.persistence"' in memory_before.content
        assert "Operational state is persisted in SQLite." in memory_before.content

        session.profile = Profile(name="changed while the run is active")
        session.preferences = Preferences(language="fr", instructions=["new live rule"])
        session.global_preferences = Preferences(language="de")
        after = builder.build(session)

        assert after.messages == before.messages
        assert "回答时默认使用 zh。" in preference_before.content
        assert session.run_context_projection.snapshot.profile.name == "frozen profile"
    finally:
        handle.close()


def test_next_turn_reloads_changed_knowledge_and_preferences(tmp_path):
    handle, journal, clock = _open(tmp_path)
    try:
        _seed_terms(journal)
        session = _session()
        ids = FixedIdSource()
        ids.counts.update({"task": 1, "ttr": 2})
        persistence = _persistence(journal, handle, ids=ids, clock=clock, session=session)
        first = persistence.submit_user(
            session,
            "Operational SQLite",
            "client_first_context",
            turn_id="turn_first_context",
            agent_run_id="arun_first_context",
        )
        assert first.kind == "accepted"
        old_projection = session.run_context_projection
        assert old_projection is not None

        session.preferences = Preferences(instructions=["next run rule"])
        session.global_preferences = Preferences(language="fr")
        head = journal.get_project_knowledge_head("ws_1", "knw_1")
        assert head is not None
        journal.put_project_knowledge_revision(
            "ws_1",
            ProjectKnowledgeRevision(
                knowledge_revision_id="krv_2",
                knowledge_id="knw_1",
                workspace_id="ws_1",
                revision=2,
                statement="SQLite state is frozen per AgentRun.",
                statement_digest=ProjectKnowledgeRevision.digest_for(
                    "SQLite state is frozen per AgentRun."
                ),
                source_candidate_id="lcn_1",
                source_decision_id="lcd_1",
                created_at=clock.now(),
                last_confirmed_at=clock.now(),
            ),
        )
        head = journal.save_project_knowledge_head(
            "ws_1",
            head.model_copy(
                update={
                    "current_revision_id": "krv_2",
                    "row_version": head.row_version + 1,
                    "updated_at": clock.now(),
                }
            ),
            expected_row_version=head.row_version,
        )
        state = journal.get_memory_workspace_state("ws_1")
        assert state is not None
        journal.save_memory_workspace_state(
            "ws_1",
            state.model_copy(
                update={
                    "memory_revision": state.memory_revision + 1,
                    "row_version": state.row_version + 1,
                }
            ),
            expected_row_version=state.row_version,
        )
        journal.transact(
            lambda txn: refresh_project_knowledge_terms(
                txn,
                "ws_1",
                head,
                previous_revision_id="krv_1",
            )
        )

        same_run = make_context_builder().build(session)
        assert "回答时默认使用 zh。" in _system_message(same_run, "冻结的用户 Preferences").content
        assert (
            "Operational state is persisted in SQLite."
            in _system_message(same_run, "冻结的 Project Knowledge").content
        )
        assert old_projection.selected_knowledge[0].revision.revision == 1

        session.append_assistant(AssistantMessage(content="已记录。"))
        session.finish_turn(FinishReason.STOP)
        next_result = persistence.submit_user(
            session,
            "Operational SQLite",
            "client_next_context",
            turn_id="turn_next_context",
            agent_run_id="arun_next_context",
        )
        assert next_result.kind == "accepted"
        next_projection = session.run_context_projection
        assert next_projection is not None
        assert any(
            item.statement == "回答时默认使用 fr。"
            for item in next_projection.snapshot.frozen_preferences
        )
        assert next_projection.memory_selection.source_memory_revision == 1
        assert next_projection.selected_knowledge[0].revision.revision == 2
        next_pack = make_context_builder().build(session)
        assert "回答时默认使用 fr。" in _system_message(next_pack, "冻结的用户 Preferences").content
        assert "next run rule" in _system_message(next_pack, "冻结的用户 Preferences").content
        assert (
            "SQLite state is frozen per AgentRun."
            in _system_message(next_pack, "冻结的 Project Knowledge").content
        )
    finally:
        handle.close()


def test_zero_item_selection_still_produces_a_frozen_projection(tmp_path):
    handle, journal, clock = _open(tmp_path)
    try:
        _knowledge_subject(journal)
        session = _session()
        ids = FixedIdSource()
        ids.counts["task"] = 1
        persistence = _persistence(journal, handle, ids=ids, clock=clock, session=session)
        result = persistence.submit_user(
            session,
            "unrelated request",
            "client_empty_context",
            turn_id="turn_empty_context",
            agent_run_id="arun_empty_context",
        )
        assert result.kind == "accepted"
        projection = session.run_context_projection
        assert projection is not None
        assert projection.memory_selection is not None
        assert projection.memory_selection.item_count == 0
        assert '"records":[]' in projection.memory_block
        assert projection.memory_content_digest is not None
    finally:
        handle.close()


def test_process_local_context_fallback_remains_live():
    session = Session(
        session_id="ses_local",
        preferences=Preferences(language="zh"),
    )
    seed_user_turn(session, "hello")
    builder: ContextBuilder = make_context_builder()
    first = builder.build(session)
    session.preferences = Preferences(language="fr")
    second = builder.build(session)
    assert '"language": "zh"' in _system_message(first, "用户状态数据").content
    assert '"language": "fr"' in _system_message(second, "用户状态数据").content


def test_persisted_session_without_projection_does_not_use_live_state(tmp_path):
    handle, journal, clock = _open(tmp_path)
    try:
        session = _session()
        seed_user_turn(session, "hello")
        ids = FixedIdSource()
        persistence = _persistence(journal, handle, ids=ids, clock=clock, session=session)
        builder = make_context_builder()
        first = builder.build(session)
        assert not any(
            message.content and message.content.startswith("以下是用户状态数据")
            for message in first.messages
        )
        session.preferences = Preferences(language="fr")
        second = builder.build(session)
        assert not any(
            message.content and '"language": "fr"' in message.content for message in second.messages
        )
        assert persistence.current_agent_run_id is None
    finally:
        handle.close()
