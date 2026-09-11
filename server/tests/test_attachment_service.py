from pathlib import Path

import pytest

from services import attachment_service


def test_uploaded_attachment_metadata_is_server_controlled(tmp_path, monkeypatch):
    monkeypatch.setattr(attachment_service, "UPLOAD_ROOT", tmp_path.resolve())

    stored = attachment_service.store_attachment(b"safe content", "../Quarterly Report.PDF")
    public = attachment_service.public_attachment_metadata([stored])[0]

    assert stored["filename"] == "Quarterly Report.PDF"
    assert stored["stored_name"].endswith(".pdf")
    assert Path(stored["filepath"]).parent == tmp_path.resolve()
    assert public == {
        "filename": "Quarterly Report.PDF",
        "stored_name": stored["stored_name"],
        "content_type": "application/pdf",
        "size": 12,
    }
    assert "filepath" not in public


def test_normalization_ignores_client_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(attachment_service, "UPLOAD_ROOT", tmp_path.resolve())
    stored = attachment_service.store_attachment(b"attachment", "note.txt")

    normalized = attachment_service.normalize_attachment_metadata([{
        **stored,
        "filepath": "/etc/passwd",
        "content_type": "text/html",
        "size": 999_999,
    }])[0]

    assert normalized["filepath"] == stored["filepath"]
    assert normalized["content_type"] == "text/plain"
    assert normalized["size"] == len(b"attachment")


@pytest.mark.parametrize(
    "stored_name",
    ["../../etc/passwd", "/etc/passwd", "not-a-generated-name.txt", "a" * 32 + ".html"],
)
def test_invalid_or_disallowed_stored_names_are_rejected(tmp_path, monkeypatch, stored_name):
    monkeypatch.setattr(attachment_service, "UPLOAD_ROOT", tmp_path.resolve())

    with pytest.raises(ValueError, match="invalid stored name"):
        attachment_service.normalize_attachment_metadata([{
            "filename": "attachment.txt",
            "stored_name": stored_name,
            "filepath": "/etc/passwd",
        }])
