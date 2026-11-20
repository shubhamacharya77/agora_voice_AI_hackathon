"""Password hashing with argon2 (argon2-cffi defaults)."""

from functools import cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


@cache
def _dummy_hash() -> str:
    return _hasher.hash("not-a-real-password")


def burn_verification_time(password: str) -> None:
    """Run one verification that always fails.

    Login calls this for unknown emails so they take as long as a wrong password,
    which stops response timing from revealing which emails have accounts.
    """
    verify_password(password, _dummy_hash())
