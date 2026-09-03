"""Stage 8 Subplan 3: transport security boundary tests (roadmap §16.3).

Covers the items reachable at this layer: a malicious webpage hitting the
loopback API (Origin/Referer allowlist, JSON-only mutations, no ambient
authority), credential material in payloads, permission-elevation attempts via
command bodies, and bounded error payloads. Browser-side XSS and approval
phishing surfaces belong to the GUI subplans.
"""

from __future__ import annotations

import pytest

from test_stage8_core_api import ServerFixture


@pytest.fixture
def fx(tmp_path):
    fixture = ServerFixture(tmp_path)
    yield fixture
    fixture.close()


async def test_requests_without_a_valid_token_are_rejected(fx):
    missing = await fx.client.get("/v1/meta", token="")
    assert missing.status == 401
    wrong = await fx.client.get("/v1/meta", token="wrong-token")
    assert wrong.status == 401
    posted = await fx.client.post("/v1/sessions", {}, token="wrong-token")
    assert posted.status == 401
    assert missing.json()["error"]["code"] == "unauthorized"


async def test_malicious_webpage_origin_is_rejected(fx):
    evil_get = await fx.client.get("/v1/meta", origin="https://evil.example")
    assert evil_get.status == 403
    evil_post = await fx.client.post("/v1/sessions", {}, origin="https://evil.example")
    assert evil_post.status == 403
    assert evil_post.json()["error"]["code"] == "forbidden"
    # A loopback GUI origin is accepted.
    local = await fx.client.get("/v1/meta", origin="http://127.0.0.1:5173")
    assert local.status == 200
    local_host = await fx.client.get("/v1/meta", origin="http://localhost:8080")
    assert local_host.status == 200


async def test_mutations_require_json_content_type(fx):
    form = await fx.client.post(
        "/v1/sessions", {}, content_type="application/x-www-form-urlencoded"
    )
    assert form.status == 415
    assert form.json()["error"]["code"] == "unsupported_media_type"


async def test_unknown_paths_are_rejected(fx):
    other_version = await fx.client.get("/v2/meta")
    assert other_version.status == 404
    outside = await fx.client.get("/etc/passwd")
    assert outside.status == 404


async def test_permission_elevation_extra_fields_are_rejected(fx):
    """Strict wire models fail closed on smuggled privilege fields."""

    elevated = await fx.client.post(
        "/v1/sessions",
        {"command_id": "cmd_elevate_1", "grant_permissions": ["unconfined_host_process"]},
    )
    assert elevated.status == 400
    start = await fx.client.post(
        "/v1/workflow-runs",
        {
            "workflow_definition_id": "pipeline",
            "workflow_revision_id": "wrev_x",
            "session_id": "ses_x",
            "root_task_run_id": "task_x",
            "expected_root_row_version": 1,
            "objective": "probe",
            "permission_mode": "full-access-manual",
        },
    )
    assert start.status == 400


async def test_oversized_and_malformed_bodies_are_rejected(fx):
    big = await fx.client.post("/v1/sessions", {"padding": "x" * (1024 * 1024 + 16)})
    assert big.status == 400
    # An empty JSON body is a valid empty object; a non-dict JSON body is not.
    empty = await fx.client.request_raw_json("/v1/sessions", "")
    assert empty.status == 200
    response = await fx.client.request_raw_json("/v1/sessions", "[1, 2]")
    assert response.status == 400


async def test_provider_catalog_never_carries_credential_material(fx):
    response = await fx.client.get("/v1/catalog/providers")
    assert response.status == 200
    body = response.body.decode()
    assert "topsecret-value" not in body
    assert "provider:fake-provider:test" not in body
    providers = response.json()["providers"]
    assert providers[0]["credential_configured"] is True
    assert "credential_ref" not in providers[0]
    assert providers[0]["models"]


async def test_error_payloads_are_bounded_and_traceback_free(fx):
    missing = await fx.client.get("/v1/workflow-runs/wrun_missing")
    assert missing.status == 404
    body = missing.body.decode()
    assert "Traceback" not in body
    assert missing.json()["error"]["code"] == "not_found"

    conflict_a = await fx.client.post("/v1/sessions", {"command_id": "cmd_bound_1"})
    assert conflict_a.status == 200
    conflict_b = await fx.client.post(
        "/v1/sessions", {"command_id": "cmd_bound_1", "session_id": "ses_other"}
    )
    assert conflict_b.status == 409
    assert "Traceback" not in conflict_b.body.decode()


async def test_websocket_auth_and_origin(fx):
    no_token = fx.client.websocket(token="")
    await no_token.accept()
    assert no_token.closed_code == 4401
    await no_token.close()

    wrong_token = fx.client.websocket(token="nope")
    await wrong_token.accept()
    assert wrong_token.closed_code == 4401
    await wrong_token.close()

    evil_origin = fx.client.websocket(origin="https://evil.example")
    await evil_origin.accept()
    assert evil_origin.closed_code == 4403
    await evil_origin.close()

    good = fx.client.websocket()
    message = await good.accept()
    assert message["type"] == "websocket.accept"
    hello = await good.receive_json()
    assert hello["type"] == "hello"
    await good.close()
