from urllib.parse import parse_qs, urlsplit

from services.template_engine import TemplateEngine
from services.tracking_signature import (
    sign_tracking_request,
    verify_tracking_signature,
)


def test_tracking_signature_rejects_parameter_tampering(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-tracking-secret")
    signature = sign_tracking_request(12, 34, "https://example.com/report")

    assert verify_tracking_signature(
        signature,
        12,
        34,
        "https://example.com/report",
    )
    assert not verify_tracking_signature(
        signature,
        12,
        34,
        "https://attacker.example/report",
    )


def test_generated_tracking_links_include_valid_signatures(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-tracking-secret")
    engine = TemplateEngine(base_url="https://sender.example")

    pixel_url = urlsplit(
        engine._generate_tracking_pixel(7, 9).split('src="', 1)[1].split('"', 1)[0]
    )
    pixel_params = parse_qs(pixel_url.query)
    assert verify_tracking_signature(
        pixel_params["s"][0],
        int(pixel_params["c"][0]),
        int(pixel_params["r"][0]),
    )

    wrapped = engine.wrap_clicks(
        '<a href="https://destination.example/path?q=one">Open</a>',
        7,
        9,
    )
    click_url = urlsplit(wrapped.split('href="', 1)[1].split('"', 1)[0])
    click_params = parse_qs(click_url.query)
    assert verify_tracking_signature(
        click_params["s"][0],
        int(click_params["c"][0]),
        int(click_params["r"][0]),
        click_params["url"][0],
    )


def test_tracking_fails_closed_without_secret(monkeypatch):
    monkeypatch.delenv("TRACKING_SECRET", raising=False)
    monkeypatch.delenv("SECRET_KEY", raising=False)

    assert sign_tracking_request(1) == ""
    assert not verify_tracking_signature("anything", 1)
