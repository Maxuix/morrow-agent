"""Subplan 68 observational Skill Usage regressions."""

from __future__ import annotations

from datetime import UTC, datetime

from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import OperationalStore
from morrow.application.skills.usage import SkillUsageService
from morrow.core.domain import (
    AgentRunSnapshot,
    DurableAgentRun,
    DurableSession,
    DurableTaskRun,
    DurableTurn,
)
from morrow.core.models import ModelRef
from morrow.core.skills.catalog import SkillDefinition, SkillVersion
from morrow.core.skills.selection import SkillSelection
from morrow.core.skills.trust import SourceKind, TrustLevel
from morrow.core.store import StoreOpenMode

NOW = datetime(2026, 8, 24, tzinfo=UTC)


def test_skill_usage_is_observational_and_comparison_is_not_a_router(tmp_path) -> None:
    store = OperationalStore(tmp_path / "state", maintenance_timeout=0)
    store.initialize().close()
    with store.open(StoreOpenMode.READ_WRITE) as handle:
        journal = SqliteOperationalJournal(handle)
        journal.create_session(
            DurableSession(session_id="ses_usage", workspace_id="ws_usage"),
            task=DurableTaskRun(
                task_run_id="task_usage", session_id="ses_usage", workspace_id="ws_usage"
            ),
        )
        journal.create_turn(
            "ws_usage",
            DurableTurn(
                turn_id="turn_usage",
                session_id="ses_usage",
                task_run_id="task_usage",
                client_message_id="usage-client",
                created_at=NOW,
            ),
        )
        snapshot = AgentRunSnapshot(
            model=ModelRef(provider_id="test", model_id="test"),
            provider_id="test",
            run_policy_digest="a" * 64,
            tool_schema_digest="b" * 64,
            permission_profile_digest="c" * 64,
            runtime_instance_id="usage-test",
        )
        journal.create_agent_run(
            "ws_usage",
            DurableAgentRun(
                agent_run_id="arun_usage",
                turn_id="turn_usage",
                session_id="ses_usage",
                snapshot=snapshot,
                created_at=NOW,
            ),
        )
        definition = SkillDefinition(
            skill_id="usage-skill",
            name="Usage Skill",
            source_kind=SourceKind.GENERATED,
            scope_id="ws_usage",
            effective_trust=TrustLevel.GENERATED,
        )
        version = SkillVersion(
            version_id="skv_usage_12345678",
            skill_id="usage-skill",
            tree_digest="d" * 64,
            file_count=1,
            total_bytes=1,
            source_kind=SourceKind.GENERATED,
            scope_id="ws_usage",
            effective_trust=TrustLevel.GENERATED,
            created_at=NOW,
        )
        journal.transact(
            lambda txn: (
                txn.put_skill_definition(definition, updated_at=NOW),
                txn.put_skill_version(version),
                txn.put_skill_selection(
                    "ws_usage",
                    SkillSelection(
                        selection_id="ssel_usage",
                        agent_run_id="arun_usage",
                        skill_id="usage-skill",
                        version_id=version.version_id,
                        scope="workspace",
                        scope_id="ws_usage",
                        source_kind=SourceKind.GENERATED,
                        activation_reason="explicit",
                        tree_digest=version.tree_digest,
                    ),
                ),
            )
        )
        service = SkillUsageService(journal, workspace_id="ws_usage", clock=lambda: NOW)
        usage = service.record(
            agent_run_id="arun_usage",
            skill_id="usage-skill",
            version_id=version.version_id,
            selection_id="ssel_usage",
            activation_reason="ignored because selection is authoritative",
            status="succeeded",
            duration_ms=20,
            tool_call_count=2,
            usage_id="sug_usage_1",
        )
        assert usage.activation_reason == "explicit"
        assert service.list(skill_id="usage-skill") == (usage,)
        comparison = service.compare(
            skill_id="usage-skill",
            left_version_id=version.version_id,
            right_version_id=version.version_id,
        )
        assert comparison.status.value == "insufficient_data"
        assert "superiority" not in comparison.reason
