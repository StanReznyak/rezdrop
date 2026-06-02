from app.security import hash_password, verify_password


def test_password_hash_and_verify():
    stored = hash_password("sample-password-123")
    assert stored.startswith("scrypt$")
    assert verify_password("sample-password-123", stored) is True
    assert verify_password("wrong", stored) is False
