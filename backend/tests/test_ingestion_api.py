from conftest import MINIMAL_PDF
from sqlalchemy import select

from app.db.models import AuditEvent, ProcessingJob


def _upload(client, name="invoice.pdf", content=MINIMAL_PDF):
    return client.post("/api/v1/documents", files={"file": (name, content, "application/pdf")})


def test_upload_pdf_stores_file_queues_job_and_audits(db_client, db_session):
    response = _upload(db_client)

    assert response.status_code == 201
    body = response.json()
    assert body["duplicate"] is False
    document = body["document"]
    assert document["status"] == "pending"
    assert document["mime_type"] == "application/pdf"

    jobs = db_session.scalars(select(ProcessingJob)).all()
    assert [str(job.document_id) for job in jobs] == [document["id"]]
    events = db_session.scalars(select(AuditEvent)).all()
    assert [event.action for event in events] == ["uploaded"]

    downloaded = db_client.get(f"/api/v1/documents/{document['id']}/file")
    assert downloaded.content == MINIMAL_PDF
    assert downloaded.headers["content-type"] == "application/pdf"


def test_same_file_twice_returns_existing_document(db_client, db_session):
    first = _upload(db_client).json()["document"]
    second = _upload(db_client, name="copy.pdf")

    assert second.status_code == 200
    assert second.json() == {"document": first, "duplicate": True}
    assert len(db_session.scalars(select(ProcessingJob)).all()) == 1


def test_rejects_unsupported_and_fake_pdf(db_client):
    assert _upload(db_client, name="photo.png", content=b"\x89PNG").status_code == 415
    assert _upload(db_client, name="fake.pdf", content=b"not a pdf").status_code == 415
    assert _upload(db_client, name="empty.pdf", content=b"").status_code == 415


def test_pasted_email_becomes_text_document(db_client):
    response = db_client.post(
        "/api/v1/documents/text",
        json={
            "subject": "PO 4500012 / Office chairs",
            "sender": "buyer@acme.test",
            "body": "Please supply 10 office chairs at 120.00 USD each. Ref PO-4500012.",
        },
    )

    assert response.status_code == 201
    document = response.json()["document"]
    assert document["filename"] == "PO 4500012  Office chairs.txt"
    assert document["source"] == "paste"
    detail = db_client.get(f"/api/v1/documents/{document['id']}").json()
    assert detail["audit_events"][0]["action"] == "uploaded"


def test_list_filters_and_paginates(db_client):
    _upload(db_client, name="a.pdf", content=MINIMAL_PDF + b"a")
    _upload(db_client, name="b.pdf", content=MINIMAL_PDF + b"b")

    page = db_client.get("/api/v1/documents", params={"page_size": 1}).json()
    assert page["total"] == 2
    assert len(page["items"]) == 1

    assert db_client.get("/api/v1/documents", params={"q": "a.p"}).json()["total"] == 1
    assert db_client.get("/api/v1/documents", params={"status": "approved"}).json()["total"] == 0
    assert db_client.get("/api/v1/documents", params={"status": "bogus"}).status_code == 422


def test_unknown_document_is_404(db_client):
    missing = "00000000-0000-0000-0000-000000000000"
    assert db_client.get(f"/api/v1/documents/{missing}").status_code == 404
    assert db_client.get(f"/api/v1/documents/{missing}/file").status_code == 404
