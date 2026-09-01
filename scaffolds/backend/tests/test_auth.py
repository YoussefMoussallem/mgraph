"""Unit tests for Azure AD token validation (app/dependencies.py).

Tokens are minted with the test RSA key from conftest and validated
through the real ``_validate_token`` path — only the JWKS fetch is
stubbed, so signature, audience, issuer, expiry and claim-mapping
logic all run for real.
"""

from __future__ import annotations

import pytest
from app.dependencies import _validate_token, get_current_user
from fastapi import HTTPException

from tests.conftest import TEST_CLIENT_ID, TEST_TENANT_ID, make_token


def _validate(token_header: str | None) -> object:
    return _validate_token(token_header, TEST_CLIENT_ID, TEST_TENANT_ID)


class TestValidateToken:
    def test_valid_token_maps_claims(self, stub_jwks) -> None:
        user = _validate(f"Bearer {make_token()}")
        assert user.user_id == "oid-1234"
        assert user.azure_oid == "oid-1234"
        assert user.email == "user@example.com"
        assert user.display_name == "Test User"

    def test_bearer_scheme_is_case_insensitive(self, stub_jwks) -> None:
        user = _validate(f"bearer {make_token()}")
        assert user.user_id == "oid-1234"

    def test_falls_back_to_sub_when_no_oid(self, stub_jwks) -> None:
        token = make_token(claims={"oid": None})
        user = _validate(f"Bearer {token}")
        assert user.user_id == "sub-1234"

    def test_missing_header_401(self, stub_jwks) -> None:
        with pytest.raises(HTTPException) as exc_info:
            _validate(None)
        assert exc_info.value.status_code == 401

    def test_wrong_scheme_401(self, stub_jwks) -> None:
        with pytest.raises(HTTPException) as exc_info:
            _validate(f"Basic {make_token()}")
        assert exc_info.value.status_code == 401

    def test_expired_token_401_with_distinct_detail(self, stub_jwks) -> None:
        # -3600 puts exp an hour in the past — beyond the 60s leeway.
        token = make_token(expires_in=-3600)
        with pytest.raises(HTTPException) as exc_info:
            _validate(f"Bearer {token}")
        assert exc_info.value.status_code == 401
        # The expired case keeps its own message so the frontend can
        # distinguish "silently refresh" from "force re-login".
        assert exc_info.value.detail == "Token has expired"

    def test_wrong_audience_401(self, stub_jwks) -> None:
        token = make_token(audience="some-other-app")
        with pytest.raises(HTTPException) as exc_info:
            _validate(f"Bearer {token}")
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Invalid token"

    def test_wrong_issuer_401(self, stub_jwks) -> None:
        token = make_token(tenant_id="another-tenant")
        with pytest.raises(HTTPException) as exc_info:
            _validate(f"Bearer {token}")
        assert exc_info.value.status_code == 401

    def test_garbage_token_401(self, stub_jwks) -> None:
        with pytest.raises(HTTPException) as exc_info:
            _validate("Bearer not-a-jwt")
        assert exc_info.value.status_code == 401


class TestUserDependency:
    def test_valid_token_through_dependency(self, stub_jwks) -> None:
        user = get_current_user(authorization=f"Bearer {make_token()}")
        assert user.user_id == "oid-1234"

    def test_foreign_audience_rejected(self, stub_jwks) -> None:
        # Audience separation: a token minted for a different app
        # registration must not authenticate here.
        token = make_token(audience="some-other-app-registration")
        with pytest.raises(HTTPException) as exc_info:
            get_current_user(authorization=f"Bearer {token}")
        assert exc_info.value.status_code == 401

    def test_dev_fallback_when_unconfigured(
        self, monkeypatch: pytest.MonkeyPatch, reset_settings
    ) -> None:
        monkeypatch.setenv("AZURE_CLIENT_ID", "")
        user = get_current_user(authorization=None)
        assert user.user_id == "anonymous"
        assert user.display_name == "Anonymous (dev)"


class TestTokenShapes:
    """Both Entra issuer formats, both audience forms, and access-token mode."""

    def test_v1_issuer_accepted(self, stub_jwks) -> None:
        # Portal-created registrations default to accessTokenAcceptedVersion=null,
        # which is v1 -- a v2-only check would reject every one of their tokens.
        token = make_token(claims={"iss": f"https://sts.windows.net/{TEST_TENANT_ID}/"})
        assert _validate(f"Bearer {token}").user_id == "oid-1234"

    def test_app_id_uri_audience_accepted(self, stub_jwks) -> None:
        token = make_token(audience=f"api://{TEST_CLIENT_ID}")
        assert _validate(f"Bearer {token}").user_id == "oid-1234"

    def test_foreign_tenant_v1_issuer_rejected(self, stub_jwks) -> None:
        token = make_token(claims={"iss": "https://sts.windows.net/another-tenant/"})
        with pytest.raises(HTTPException) as exc_info:
            _validate(f"Bearer {token}")
        assert exc_info.value.status_code == 401

    def test_id_token_mode_keeps_no_assertion(self, stub_jwks) -> None:
        user = _validate(f"Bearer {make_token()}")
        assert user.assertion is None
        assert user.scopes == ()

    def test_scope_mode_rejects_id_token_with_a_usable_message(self, stub_jwks) -> None:
        with pytest.raises(HTTPException) as exc_info:
            _validate_token(
                f"Bearer {make_token()}", TEST_CLIENT_ID, TEST_TENANT_ID, "access_as_user"
            )
        assert exc_info.value.status_code == 401
        assert "scp" in exc_info.value.detail
        assert f"api://{TEST_CLIENT_ID}/access_as_user" in exc_info.value.detail

    def test_scope_mode_rejects_wrong_scope_with_403(self, stub_jwks) -> None:
        token = make_token(claims={"scp": "some.other.scope"})
        with pytest.raises(HTTPException) as exc_info:
            _validate_token(f"Bearer {token}", TEST_CLIENT_ID, TEST_TENANT_ID, "access_as_user")
        assert exc_info.value.status_code == 403
        assert "access_as_user" in exc_info.value.detail

    def test_scope_mode_keeps_the_assertion_for_on_behalf_of(self, stub_jwks) -> None:
        token = make_token(claims={"scp": "access_as_user User.Read"})
        user = _validate_token(f"Bearer {token}", TEST_CLIENT_ID, TEST_TENANT_ID, "access_as_user")
        assert user.assertion == token
        assert user.scopes == ("access_as_user", "User.Read")

    def test_dependency_reads_required_scope_from_settings(
        self, stub_jwks, monkeypatch: pytest.MonkeyPatch, reset_settings
    ) -> None:
        monkeypatch.setenv("API_REQUIRED_SCOPE", "access_as_user")
        with pytest.raises(HTTPException) as exc_info:
            get_current_user(authorization=f"Bearer {make_token()}")
        assert exc_info.value.status_code == 401
        token = make_token(claims={"scp": "access_as_user"})
        assert get_current_user(authorization=f"Bearer {token}").assertion == token
