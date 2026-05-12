"""Tests for OAuth utility functions — PKCE S256."""
from __future__ import annotations

from oauth import _pkce_s256


class TestPkceS256:
    def test_rfc7636_appendix_b_vector(self) -> None:
        """RFC 7636 Appendix B test vector."""
        verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
        expected = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
        assert _pkce_s256(verifier) == expected

    def test_no_padding(self) -> None:
        """Result must not contain base64 padding characters."""
        result = _pkce_s256("test-verifier-string")
        assert "=" not in result

    def test_url_safe_characters(self) -> None:
        """Result must only contain URL-safe base64 characters."""
        result = _pkce_s256("test-verifier-string")
        assert "+" not in result
        assert "/" not in result
