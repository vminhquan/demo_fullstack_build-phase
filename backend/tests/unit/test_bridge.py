from __future__ import annotations

import pytest

from app.modules.bridge.router import bearer_token
from app.modules.bridge.service import AttemptLimiter, TooManyAttempts, hash_code, hash_device_token, new_code, new_uid


def test_codes_are_six_digits_with_leading_zeros_kept() -> None:
    codes = {new_code() for _ in range(500)}
    assert all(len(code) == 6 and code.isdigit() for code in codes)
    assert len(codes) > 400  # random, not a counter


def test_code_hash_is_keyed_and_stable() -> None:
    assert hash_code("012345") == hash_code("012345")
    assert hash_code("012345") != hash_code("012346")
    # Keyed (HMAC), so it is not the plain sha256 a leaked table could be brute-forced against.
    assert hash_code("012345") != hash_device_token("012345")


def test_uids_carry_prefix() -> None:
    assert new_uid("CONN").startswith("CONN-") and len(new_uid("CONN")) == 15


def test_limiter_blocks_after_limit_and_window_expires() -> None:
    limiter = AttemptLimiter(limit=3, window_seconds=60)
    for moment in (0, 1, 2):
        limiter.check("1.2.3.4", at=moment)
        limiter.fail("1.2.3.4", at=moment)
    with pytest.raises(TooManyAttempts):
        limiter.check("1.2.3.4", at=3)
    limiter.check("5.6.7.8", at=3)  # other clients unaffected
    limiter.check("1.2.3.4", at=100)  # window passed


def test_limiter_reset_after_success() -> None:
    limiter = AttemptLimiter(limit=1, window_seconds=60)
    limiter.fail("ip", at=0)
    limiter.reset("ip")
    limiter.check("ip", at=1)


def test_bearer_token_parsing() -> None:
    assert bearer_token("Bearer abc") == "abc"
    assert bearer_token("bearer  abc ") == "abc"
    assert bearer_token("Basic abc") is None
    assert bearer_token(None) is None


def test_gunzip_limited_accepts_catalogs_and_rejects_bombs_and_garbage() -> None:
    import gzip

    from app.modules.bridge.router import gunzip_limited
    from app.shared.domain.errors import ValidationFailed

    body = b'{"catalog": {"waypoints": [' + b",".join([b'{"x": 1.0, "y": 2.0}'] * 2000) + b"]}}"
    assert gunzip_limited(gzip.compress(body), limit=len(body)) == body
    bomb = gzip.compress(b"0" * 5_000_000)  # ~5 KB on the wire
    with pytest.raises(ValidationFailed, match="exceeds"):
        gunzip_limited(bomb, limit=1_000_000)
    with pytest.raises(ValidationFailed, match="gzip"):
        gunzip_limited(b"not gzip", limit=1000)
