from schemas.smtp import SMTPAccountResponse


def test_smtp_response_schema_never_serializes_credentials():
    assert "password" not in SMTPAccountResponse.model_fields
    assert "imap_password" not in SMTPAccountResponse.model_fields
    assert "oauth2_client_secret" not in SMTPAccountResponse.model_fields
