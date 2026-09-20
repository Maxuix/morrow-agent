"""Real Artifact ownership and decoder processes with deterministic cancellation."""

import asyncio
import io
import json

import pytest
from PIL import Image

from morrow.application.attachment_parsing import AttachmentParsePool
from morrow.application.attachments import AttachmentService
from morrow.core.application import ApplicationError
from morrow.core.attachments import AttachmentRef, AttachmentReservation
from morrow.core.domain import DurableConversationRecord, DurableSession
from morrow.testing import FixedIdSource
from test_stage4_artifacts import _service


@pytest.fixture
def attachments(tmp_path):
    handle, journal, _, artifacts = _service(tmp_path)
    service = AttachmentService(
        journal, artifacts, "ws_1", lambda sid: journal.get_session("ws_1", sid), FixedIdSource()
    )
    yield service
    handle.close()


def reservation(content, *, key="file.1", mime="text/plain", sid="ses_1"):
    return AttachmentReservation(
        command_id=key, session_id=sid, name="example", media_type=mime, byte_size=len(content)
    )


async def upload(service, content, **kwargs):
    row = service.reserve(reservation(content, **kwargs))
    row = service.begin_upload(row["attachment_id"], row["revision"], row["request"]["media_type"])
    service.accept(row["attachment_id"], row["revision"], content)
    await service.jobs[row["attachment_id"]]
    return service.get(row["attachment_id"])


async def test_text_retention_immutable_reference_and_replay(attachments):
    s = attachments
    row = await upload(s, b"print('frozen source')")
    assert row["state"] == "ready", row
    ref = AttachmentRef.model_validate(row["reference"])
    representation = s.representation(ref)
    part = representation.parts[0]
    assert s.artifacts.read(part.artifact_id, max_bytes=100).content == b"print('frozen source')"
    assert s.reserve(reservation(b"print('frozen source')")) == row
    assert len(s.artifacts.retention_report().referenced) == 3
    s.validate("ses_1", [ref], image_supported=False)
    s.journal.transact(lambda _: s.submit([ref]))
    with pytest.raises(ApplicationError):
        s.release(row["attachment_id"], s.get(row["attachment_id"])["revision"])
    with pytest.raises(ApplicationError):
        s.validate("ses_other", [ref], image_supported=True)
    with pytest.raises(ApplicationError):
        s.validate(
            "ses_1", [ref.model_copy(update={"content_digest": "0" * 64})], image_supported=True
        )


async def test_release_removes_draft_retention_and_wrong_size_fails(attachments):
    s = attachments
    row = await upload(s, b"draft")
    assert s.artifacts.retention_report().referenced
    s.release(row["attachment_id"], row["revision"])
    assert not s.artifacts.retention_report().referenced
    row = s.reserve(reservation(b"other", key="size"))
    row = s.begin_upload(row["attachment_id"], row["revision"], "text/plain")
    with pytest.raises(ApplicationError):
        s.accept(row["attachment_id"], row["revision"], b"wrong length")
    assert s.get(row["attachment_id"])["state"] == "failed"


@pytest.mark.parametrize(
    "format,mime", [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp")]
)
async def test_images_are_decoded_with_real_pixels_and_require_model_support(
    attachments, format, mime
):
    output = io.BytesIO()
    Image.new("RGB", (20, 30), "red").save(output, format=format)
    row = await upload(attachments, output.getvalue(), mime=mime)
    assert row["state"] == "ready", row
    ref = AttachmentRef.model_validate(row["reference"])
    with pytest.raises(ApplicationError, match="cannot read images"):
        attachments.validate("ses_1", [ref], image_supported=False)
    attachments.validate("ses_1", [ref], image_supported=True)
    part = attachments.representation(ref).parts[0]
    assert part.width == 20 and part.height == 30
    raw = attachments.artifacts.read(part.artifact_id, max_bytes=10000).content
    with Image.open(io.BytesIO(raw)) as image:
        assert image.size == (20, 30)
        assert image.getpixel((10, 10))[0] >= 250


async def test_pdf_scans_have_page_images_and_corrupt_formats_fail(attachments):
    output = io.BytesIO()
    Image.new("RGB", (40, 40), "white").save(output, format="PDF")
    row = await upload(attachments, output.getvalue(), mime="application/pdf")
    assert row["state"] == "ready", row
    rep = attachments.representation(AttachmentRef.model_validate(row["reference"]))
    assert rep.reading == "pdf_images" and rep.pages == 1
    assert rep.parts[0].page == 1 and rep.parts[0].media_type == "image/png"
    row = await upload(attachments, b"pretend this is png", key="bad", mime="image/png")
    assert row["state"] == "failed" and row["reference"] is None


async def test_cancel_waits_for_worker_and_never_revives_released_draft(attachments):
    entered, released = asyncio.Event(), asyncio.Event()

    class GatePool:
        active = 0
        concurrency = 2

        async def parse(self, content, mime):
            self.active += 1
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.active -= 1
                released.set()

    attachments.pool = pool = GatePool()
    row = attachments.reserve(reservation(b"pending"))
    row = attachments.begin_upload(row["attachment_id"], row["revision"], "text/plain")
    row = attachments.accept(row["attachment_id"], row["revision"], b"pending")
    await entered.wait()
    attachments.release(row["attachment_id"], row["revision"])
    await attachments.shutdown()
    assert released.is_set() and pool.active == 0
    assert attachments.get(row["attachment_id"])["state"] == "released"
    assert not attachments.artifacts.retention_report().referenced


async def test_parser_timeout_reaps_process_and_releases_capacity():
    pool = AttachmentParsePool(timeout=0)
    with pytest.raises(ApplicationError):
        await pool.parse(b"some text", "text/plain")
    assert pool.active == 0
    pool.timeout = 30
    assert (await pool.parse(b"some text", "text/plain"))["parts"][0][2] == b"some text"


def test_staging_capacity_is_shared_between_workspaces(attachments):
    s = attachments
    s.journal.create_session(DurableSession(session_id="ses_2", workspace_id="ws_2"))
    other = AttachmentService(s.journal, s.artifacts, "ws_2", lambda sid: None, FixedIdSource())
    for i in range(8):
        s.reserve(
            AttachmentReservation(
                command_id=f"large{i}",
                session_id="ses_1",
                name="large",
                media_type="text/plain",
                byte_size=8 * 1024 * 1024,
            )
        )
    with pytest.raises(ApplicationError, match="capacity"):
        other.reserve(reservation(b"x", sid="ses_2"))


def text_pdf(text="Known PDF text 1972"):
    stream = f"BT /F1 12 Tf 20 80 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 100] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(output)
    output.extend(b"xref\n0 6\n0000000000 65535 f \n")
    for offset in offsets:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode())
    return bytes(output)


async def test_text_pdf_has_separate_preview_and_text_input(attachments):
    row = await upload(attachments, text_pdf(), mime="application/pdf")
    assert row["state"] == "ready", row
    rep = attachments.representation(AttachmentRef.model_validate(row["reference"]))
    assert rep.reading == "pdf_text" and len(rep.previews) == 1
    assert all(p.media_type == "text/plain" for p in rep.parts)
    assert (
        b"Known PDF text 1972"
        in attachments.artifacts.read(rep.parts[0].artifact_id, max_bytes=100).content
    )
    attachments.validate(
        "ses_1", [AttachmentRef.model_validate(row["reference"])], image_supported=False
    )


async def test_text_budget_and_benign_security_source_are_not_secret_material(attachments):
    content = ("# authorization module\n" + "a" * 40000).encode()
    row = await upload(attachments, content)
    assert row["state"] == "ready", row
    rep = attachments.representation(AttachmentRef.model_validate(row["reference"]))
    assert rep.parts[0].chars == 32768 and rep.omitted_chars == len(content.decode()) - 32768
    bad = await upload(attachments, b"password = 'actual-test-secret'", key="secret")
    assert bad["state"] == "failed"


async def test_publish_link_interruption_retries_without_duplicate_blobs(attachments, monkeypatch):
    service = attachments
    original = service.records.link
    failed = False

    def interrupt(row, identity, role):
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("simulated reference commit interruption")
        return original(row, identity, role)

    monkeypatch.setattr(service.records, "link", interrupt)
    row = await upload(service, b"recover original bytes")
    assert row["state"] == "failed"
    row = service.begin_upload(row["attachment_id"], row["revision"], "text/plain")
    service.accept(row["attachment_id"], row["revision"], b"recover original bytes")
    await service.jobs[row["attachment_id"]]
    assert service.get(row["attachment_id"])["state"] == "ready"
    assert len(service.artifacts.retention_report().referenced) == 3
    assert service.journal._backend.read_one("SELECT COUNT(*) FROM artifacts", ())[0] == 3


def _seed_leaf_run(journal, workspace_id, root_session, leaf_session, run_id):
    def work(_):
        executor = journal._backend.executor()
        executor.execute(
            "INSERT OR IGNORE INTO task_runs (task_run_id, session_id, workspace_id, status,"
            " row_version, attempt, created_at_unix, updated_at_unix)"
            " VALUES (?,?,?,'open',1,1,1000,1000)",
            (f"task_{run_id}", root_session, workspace_id),
        )
        executor.execute(
            "INSERT INTO workflow_revisions (workflow_revision_id, workspace_id,"
            " workflow_definition_id, revision, content_hash, body_json)"
            " VALUES (?,?,?,?,?,'{}')",
            (f"rev_{run_id}", workspace_id, "wf_test", 1, "a" * 64),
        )
        executor.execute(
            "INSERT INTO workflow_runs (workflow_run_id, workspace_id, workflow_revision_id,"
            " root_task_run_id, status, lineage_budget_root_run_id, body_json)"
            " VALUES (?,?,?,?, 'running', ?, '{}')",
            (run_id, workspace_id, f"rev_{run_id}", f"task_{run_id}", run_id),
        )
        executor.execute(
            "INSERT INTO workflow_node_runs (node_run_id, workspace_id, workflow_run_id,"
            " node_id, attempt, status, body_json) VALUES (?,?,?,?,'1','running',?)",
            (
                f"node_{run_id}",
                workspace_id,
                run_id,
                "alpha",
                json.dumps({"conversation_session_id": leaf_session}),
            ),
        )

    journal.transact(work)


async def test_require_visible_authorizes_leaf_record_references_only(attachments):
    s = attachments
    leaf = "ses_leaf1"
    stranger = "ses_stranger1"
    s.journal.transact(
        lambda _: s.journal.create_session(DurableSession(session_id=leaf, workspace_id="ws_1"))
    )
    s.journal.transact(
        lambda _: s.journal.create_session(DurableSession(session_id=stranger, workspace_id="ws_1"))
    )
    row = await upload(s, b"leaf owned bytes", key="leaf.1", sid=leaf)
    identity = row["attachment_id"]
    # Not visible before any durable record references it.
    with pytest.raises(ApplicationError):
        s.require_visible(identity, "ses_1")
    _seed_leaf_run(s.journal, "ws_1", "ses_1", leaf, "wfr_leaf1")
    # The run chain alone still does not grant access; the reference does.
    with pytest.raises(ApplicationError):
        s.require_visible(identity, "ses_1")

    def reference(_):
        s.journal.append_records(
            "ws_1",
            [
                DurableConversationRecord(
                    record_id="rec_leaf1",
                    session_id=leaf,
                    conversation_position=1,
                    kind="message",
                    payload={"role": "user", "content": "x", "attachments": [row["reference"]]},
                )
            ],
        )

    s.journal.transact(reference)
    assert s.require_visible(identity, "ses_1")["attachment_id"] == identity
    # A session outside the run lineage stays unauthorized.
    with pytest.raises(ApplicationError):
        s.require_visible(identity, stranger)


async def test_require_visible_enforces_fork_cut_prefix(attachments):
    s = attachments
    child = "ses_child1"
    before = await upload(s, b"before cut", key="cut.before")
    after = await upload(s, b"after cut", key="cut.after")

    def submit_all(_):
        s.submit(
            [
                AttachmentRef.model_validate(before["reference"]),
                AttachmentRef.model_validate(after["reference"]),
            ]
        )

    s.journal.transact(submit_all)

    def before_record(_):
        s.journal.append_records(
            "ws_1",
            [
                DurableConversationRecord(
                    record_id="rec_before",
                    session_id="ses_1",
                    conversation_position=1,
                    kind="message",
                    payload={
                        "role": "user",
                        "content": "a",
                        "attachments": [before["reference"]],
                    },
                ),
                DurableConversationRecord(
                    record_id="rec_close",
                    session_id="ses_1",
                    conversation_position=2,
                    kind="terminal",
                    payload={"finish_reason": "stop"},
                ),
            ],
        )

    s.journal.transact(before_record)
    # The fork child is cut at the closed turn after position 2.
    s.journal.transact(
        lambda _: s.journal.create_session(
            DurableSession(
                session_id=child,
                workspace_id="ws_1",
                parent_session_id="ses_1",
                parent_cut_record_id="rec_close",
                parent_cut_position=2,
                fork_reason="continue",
                conversation_position=2,
            )
        )
    )

    def after_record(_):
        s.journal.append_records(
            "ws_1",
            [
                DurableConversationRecord(
                    record_id="rec_after",
                    session_id="ses_1",
                    conversation_position=3,
                    kind="message",
                    payload={
                        "role": "user",
                        "content": "b",
                        "attachments": [after["reference"]],
                    },
                )
            ],
        )

    s.journal.transact(after_record)
    assert (
        s.require_visible(before["attachment_id"], child)["attachment_id"]
        == before["attachment_id"]
    )
    with pytest.raises(ApplicationError):
        s.require_visible(after["attachment_id"], child)


async def test_mixed_pdf_page_coverage_limits_and_pin_survive_release(attachments):
    import pypdfium2 as pdfium

    scan = io.BytesIO()
    Image.new("RGB", (40, 40), "white").save(scan, format="PDF")
    with pdfium.PdfDocument.new() as mixed:
        with pdfium.PdfDocument(text_pdf()) as text:
            mixed.import_pages(text)
        with pdfium.PdfDocument(scan.getvalue()) as image:
            mixed.import_pages(image)
        data = io.BytesIO()
        mixed.save(data)
    row = await upload(attachments, data.getvalue(), mime="application/pdf")
    assert row["state"] == "ready", row
    rep = attachments.representation(AttachmentRef.model_validate(row["reference"]))
    assert rep.reading == "pdf_mixed" and rep.pages == 2
    assert [(p.page, p.media_type) for p in rep.parts] == [(1, "text/plain"), (2, "image/png")]
    attachments.artifacts.pin(rep.parts[0].artifact_id)
    attachments.release(row["attachment_id"], row["revision"])
    assert attachments.artifacts.read(rep.parts[0].artifact_id, max_bytes=100).content
    with pdfium.PdfDocument.new() as large:
        with pdfium.PdfDocument(text_pdf()) as text:
            for _ in range(21):
                large.import_pages(text)
        data = io.BytesIO()
        large.save(data)
    assert (await upload(attachments, data.getvalue(), mime="application/pdf", key="many-pages"))[
        "state"
    ] == "failed"
    data = io.BytesIO()
    Image.new("RGB", (4001, 4000), "white").save(data, format="PNG")
    assert (await upload(attachments, data.getvalue(), mime="image/png", key="pixels"))[
        "state"
    ] == "failed"
    assert (await upload(attachments, scan.getvalue(), mime="image/png", key="wrong-mime"))[
        "state"
    ] == "failed"
