import hashlib

from services.auth_service import hash_password, verify_password


def test_new_password_hashes_use_scrypt_and_verify():
    stored_hash, salt = hash_password("CorrectHorseBatteryStaple1")

    assert stored_hash.startswith("scrypt$")
    assert salt in stored_hash
    assert verify_password("CorrectHorseBatteryStaple1", stored_hash)
    assert not verify_password("wrong-password", stored_hash)


def test_legacy_sha256_password_hashes_still_verify():
    password = "LegacyPassword1"
    salt = "ab" * 32
    digest = hashlib.sha256(f"{salt}{password}".encode()).hexdigest()
    stored_hash = f"{salt}${digest}"

    assert verify_password(password, stored_hash)
    assert not verify_password("wrong-password", stored_hash)


def test_malformed_password_hash_is_rejected():
    assert not verify_password("password", "")
    assert not verify_password("password", "scrypt$bad")