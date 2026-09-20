"""The one home for password hashing and verification (Argon2id, via argon2-cffi).

**Why this is not passlib.** passlib's argon2 handler reads ``argon2.__version__`` when it
loads its backend (``passlib/handlers/argon2.py``). argon2-cffi 25.x deprecated that
attribute and will remove it, and passlib has had no release since 2020, so nothing upstream
is going to fix it. Today it is only a ``DeprecationWarning`` — hashing still works — which
is exactly why it stayed invisible: the canonical pytest config runs ``filterwarnings =
error``, which turned it into a raised exception, and ``POST /system/users`` returned 500 in
the suite while production carried on fine. The day argon2-cffi drops the attribute, hashing
and login break in production with no change on our side. Found by route-smoke; catching a
deprecation one version before it turns fatal is the whole point of that pairing.

The wire format does not change. argon2-cffi and passlib both emit and accept PHC strings
(``$argon2id$v=19$...``), so every hash already stored still verifies — confirmed against
both live databases.

**Why there is no bcrypt fallback.** The previous ``CryptContext(schemes=["argon2",
"bcrypt"])`` advertised legacy bcrypt support that never existed: ``bcrypt`` was not in
poetry.lock, so passlib had no backend for it and any bcrypt hash would have raised
``MissingBackendError`` rather than verifying. Both live databases were checked and hold no
bcrypt hash — only ``$argon2id$`` and the env-managed placeholder. Keeping the branch would
mean adding a dependency to support a path that has never run and has no data.
"""

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# Argon2id at argon2-cffi's own defaults — the values passlib delegated to anyway.
_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    """Hash a password with Argon2id."""
    return _hasher.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its stored hash.

    Returns False rather than raising on a wrong password OR an unreadable stored hash: the
    caller is an auth path whose only correct answer is "no". A malformed hash must not
    become a 500 on the login route.
    """
    try:
        return _hasher.verify(hashed_password, plain_password)
    except VerifyMismatchError, VerificationError, InvalidHashError:
        return False


def needs_rehash(hashed_password: str) -> bool:
    """Report whether a stored hash should be replaced on the next successful login."""
    try:
        return _hasher.check_needs_rehash(hashed_password)
    except InvalidHashError:
        return True
