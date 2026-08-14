"""JWT create/verify tests — lock the auth token behavior after the
python-jose -> PyJWT migration.

A JWT-library swap must preserve the security-critical contract: signature
verification, expiry enforcement, and rejection of tampered/forged tokens.
The happy path is already exercised by the auth_client login flow; these tests
pin the *rejection* paths (the `except jwt.PyJWTError` branch) that a silent
library regression would otherwise slip past.
"""

from datetime import timedelta

import jwt

from app.web.auth import ALGORITHM, AuthService


def test_token_roundtrip() -> None:
    token = AuthService.create_access_token({"sub": "alice"})
    assert AuthService.verify_token(token) == "alice"


def test_expired_token_rejected() -> None:
    token = AuthService.create_access_token(
        {"sub": "bob"}, expires_delta=timedelta(seconds=-1)
    )
    assert AuthService.verify_token(token) is None


def test_tampered_token_rejected() -> None:
    token = AuthService.create_access_token({"sub": "carol"})
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    assert AuthService.verify_token(tampered) is None


def test_forged_signature_rejected() -> None:
    # Signed with a different secret (>=32 bytes, per PyJWT's HMAC minimum) ->
    # the signature check must still fail against the real key.
    forged = jwt.encode(
        {"sub": "mallory"}, "a-different-secret-of-sufficient-length-32b", algorithm=ALGORITHM
    )
    assert AuthService.verify_token(forged) is None


def test_garbage_token_rejected() -> None:
    assert AuthService.verify_token("not.a.jwt") is None
    assert AuthService.verify_token("") is None


def test_missing_sub_returns_none() -> None:
    # A validly-signed token without a subject is not a valid identity.
    token = AuthService.create_access_token({"role": "admin"})
    assert AuthService.verify_token(token) is None
