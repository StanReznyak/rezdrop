from fastapi.testclient import TestClient

from app.main import app
from app.security import get_csrf_token, is_strong_enough_password, validate_username


def test_security_headers_are_present():
    client = TestClient(app)
    response = client.get("/", headers={"host": "testserver"})
    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_upload_without_csrf_is_rejected():
    client = TestClient(app)
    client.get("/", headers={"host": "testserver"})
    response = client.post(
        "/upload",
        headers={"host": "testserver"},
        data={"expires_in": "3_days"},
        files={"files": ("hello.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 403


def test_username_and_password_policy():
    assert validate_username("user_535") is True
    assert validate_username("bad user") is False
    assert is_strong_enough_password("qwerty123") is False
    assert is_strong_enough_password("goodpass123") is True
