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

    # Tamper the FIRST character of the signature, not the last. A 32-byte HMAC-SHA256
    # signature is 256 bits and base64url packs 6 bits per character, so the FINAL character
    # carries only 256 - 42*6 = 4 significant bits — its low 2 bits are padding. Rewriting
    # that last character to "A" therefore decodes to the SAME signature bytes whenever the
    # original was B, C or D (3/64 of tokens ~ 4.7%), the token stays valid, and this test
    # fails intermittently for reasons that look like flake. The first character carries a
    # full 6 significant bits, so changing it always changes the signature.
    header, payload, signature = token.split(".")
    flipped = ("A" if signature[0] != "A" else "B") + signature[1:]
    assert flipped != signature
    tampered = f"{header}.{payload}.{flipped}"

    assert AuthService.verify_token(tampered) is None


def test_forged_signature_rejected() -> None:
    # Signed with a different secret (>=32 bytes, per PyJWT's HMAC minimum) ->
    # the signature check must still fail against the real key.
    forged = jwt.encode(
        {"sub": "mallory"},
        "a-different-secret-of-sufficient-length-32b",
        algorithm=ALGORITHM,
    )
    assert AuthService.verify_token(forged) is None


def test_garbage_token_rejected() -> None:
    assert AuthService.verify_token("not.a.jwt") is None
    assert AuthService.verify_token("") is None


def test_missing_sub_returns_none() -> None:
    # A validly-signed token without a subject is not a valid identity.
    token = AuthService.create_access_token({"role": "admin"})
    assert AuthService.verify_token(token) is None
