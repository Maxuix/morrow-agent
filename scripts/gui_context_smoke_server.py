"""Isolated provider-free Context/Learning/Skill GUI acceptance server.

Start with a fresh directory. All state is test data; no external service is used.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import uvicorn  # noqa: E402

from morrow.application.management_requests import (  # noqa: E402
    PreferenceWriteRequest,
    ProfileWriteRequest,
)
from morrow.core.learning import (  # noqa: E402
    LearningCandidateStatus,
    LearningCandidateType,
    LearningResolutionActor,
)
from morrow.core.learning_payloads import SkillCandidatePayload  # noqa: E402
from morrow.server.app import create_asgi_app  # noqa: E402
from morrow.server.events import EventHub  # noqa: E402
from morrow.server.host import CoreHost, RunSupervisor  # noqa: E402
from test_skill_lifecycle import _source  # noqa: E402
from test_stage8_context_management import management_fixture, seed_mutation  # noqa: E402


def builder(directory):
    def build():
        app, handle, service = management_fixture(directory)
        seed_mutation(service, "learning-decision", "accept")
        sample = service.api.journal.get_learning_candidate("ws_1", "lcn_1")
        payload = SkillCandidatePayload(
            title="Review Notes",
            problem_pattern="Summarize verified evidence",
            observed_steps=("Read the request", "Write verified findings"),
            tool_names=(),
        )
        candidate = sample.model_copy(
            update={
                "candidate_id": "lcn_demo_skill",
                "candidate_type": LearningCandidateType.SKILL_CANDIDATE,
                "semantic_key": "skill.review_notes",
                "proposed_payload": payload,
                "status": LearningCandidateStatus.ACCEPTED,
                "row_version": 2,
                "resolved_at": service.api.clock(),
                "resolved_by": LearningResolutionActor.USER,
                "fingerprint": sample.fingerprint_for(
                    candidate_type=LearningCandidateType.SKILL_CANDIDATE,
                    scope=sample.proposed_scope,
                    semantic_key="skill.review_notes",
                    operation=sample.operation,
                    proposed_payload=payload,
                ),
            }
        )
        service.api.journal.put_learning_candidate("ws_1", candidate)
        service.skills.drafts.create_from_candidate(candidate.candidate_id)
        source = _source(directory)
        service.skills.lifecycle.install(source, scope_id="ws_1", confirmed=True)
        service.execute(
            "preferences",
            PreferenceWriteRequest.model_validate(
                {
                    "command_id": "cmd_demo_preference",
                    "arguments": {
                        "scope": "workspace",
                        "expected_revision": 0,
                        "operations": [
                            {"operation": "add", "statement": "优先使用中文，说明验证结果。"}
                        ],
                    },
                }
            ),
        )
        service.execute(
            "profile",
            ProfileWriteRequest.model_validate(
                {
                    "command_id": "cmd_demo_profile",
                    "expected_revision": 0,
                    "command": {
                        "scope": "workspace",
                        "target": "profile",
                        "operation": "set",
                        "path": "name",
                        "value": "Morrow Demo",
                    },
                }
            ),
        )
        return SimpleNamespace(
            workspace_id="ws_1",
            journal=service.api.journal,
            api=service.api,
            runtime=SimpleNamespace(),
            context_management=service,
            hub=EventHub(),
            supervisor=RunSupervisor(),
            close=handle.close,
        )

    return build


async def main(directory):
    directory.mkdir(parents=True, exist_ok=True)
    host = CoreHost(builder(directory))
    host.start()
    try:
        app = create_asgi_app(
            host, auth_token="context-smoke-token", gui_static_dir=ROOT / "src/morrow/gui_static"
        )
        await uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=8810, log_level="warning")
        ).serve()
    finally:
        host.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main(Path(sys.argv[1])))
    except KeyboardInterrupt:
        pass
