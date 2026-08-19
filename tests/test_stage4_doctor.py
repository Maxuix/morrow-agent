"""Read-only operational diagnosis coverage."""

from __future__ import annotations

import random

from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import BusyRetryPolicy, OperationalStore
from morrow.application.api import OperationalApplicationService
from morrow.application.doctor import OperationalDoctor
from morrow.core.domain import DurableSession
from morrow.testing import FixedClock, FixedIdSource


def test_doctor_is_read_only_and_reports_application_event_counts(tmp_path):
    store = OperationalStore(
        tmp_path / "state",
        clock=FixedClock(),
        retry_policy=BusyRetryPolicy(
            busy_timeout_ms=0, sleep=lambda _delay: None, rng=random.Random(0)
        ),
        maintenance_timeout=0,
    )
    handle = store.initialize()
    journal = SqliteOperationalJournal(handle)
    journal.create_session(DurableSession(session_id="ses_1", workspace_id="ws_1"))
    api = OperationalApplicationService(
        journal=journal, workspace_id="ws_1", id_source=FixedIdSource()
    )
    api.create_session(session_id="ses_2", command_id="cmd_1")
    before = store.layout.database.stat().st_mtime_ns
    report = OperationalDoctor(store).inspect("ws_1")
    after = store.layout.database.stat().st_mtime_ns
    handle.close()
    assert report.health.value == "ok"
    assert report.counts["sessions"] == 2
    assert report.counts["application_events"] == 1
    assert before == after
    assert b"/" not in report.json_bytes()
