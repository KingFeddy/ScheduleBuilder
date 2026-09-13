"""The public parse/generate round trip preserves requirement provenance."""
import base64
import hashlib

from tests.deployment.test_api_contracts import api  # noqa: F401
from tests.plan.test_requirement_identity import mock_catalog


def test_pdf_to_plan_round_trip_keeps_quantity_source_and_identity(api, monkeypatch):
    import pdfplumber

    text = ("Synthetic audit notes.\n" * 30 + "Majors Computer Science\n"
            "Credits required: 120 Credits applied: 114\n"
            "Synthetic electives\nStill needed: 2 Classes in CS 435 or CS 480\n"
            "Unknown elective\nStill needed: ? Credits in PHYS 3@")

    class Page:
        def extract_text(self, layout=True):
            return text

    class PDF:
        pages = [Page()]
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    monkeypatch.setattr(pdfplumber, "open", lambda _: PDF())
    mock_catalog(monkeypatch)
    client, _ = api
    pdf = b"%PDF-1.4\n" + b"Synthetic bytes.\n" * 400
    request = {"pdf_base64": base64.b64encode(pdf).decode(), "client_pdf_hash": ""}
    parsed = client.post("/api/plan/parse", json=request)
    assert parsed.status_code == 200
    body = parsed.json()
    requirements = body["parsed"]["still_needed"]
    assert requirements[0]["remaining_quantity"] == 2
    assert requirements[0]["quantity_unit"] == "classes"
    assert requirements[0]["quantity_status"] == "known"
    assert requirements[1]["remaining_quantity"] is None
    assert requirements[1]["quantity_status"] == "unresolved"
    assert all(r["source"]["document_id"] == hashlib.sha256(pdf).hexdigest() == body["server_hash"] for r in requirements)
    assert client.post("/api/plan/parse", json=request).json() == body
    result = client.post("/api/plan/generate", json={"parsed_degree": body["parsed"], "preferences": {}})
    assert result.status_code == 200
    rows = [c for s in result.json()["semesters"] for c in s["courses"]]
    assert {c["requirement"]["requirement_id"]: c["requirement"] for c in rows} == {r["requirement_id"]: r for r in requirements}
    assert len({c["slot_id"] for c in rows}) == len(rows)
