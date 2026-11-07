import pytest
from fastapi import HTTPException

from api.auth import current_user, groups_from_claims


def test_keycloak_group_paths_become_plain_names() -> None:
    claims = {"groups": ["/employees", "/org/managers", "managers", "/"]}
    assert groups_from_claims(claims) == ["employees", "managers"]


def test_header_mode_reads_user_and_groups() -> None:
    user = current_user(authorization="", x_user_id="alice", x_user_groups="employees, managers")
    assert user.user_id == "alice"
    assert user.groups == ["employees", "managers"]


def test_header_mode_requires_a_user() -> None:
    with pytest.raises(HTTPException) as error:
        current_user(authorization="", x_user_id="", x_user_groups="")
    assert error.value.status_code == 401


def _token(key, claims: dict) -> str:
    import jwt

    return jwt.encode(claims, key, algorithm="RS256")


def test_oidc_mode_accepts_valid_tokens_and_rejects_bad_ones(monkeypatch) -> None:
    from cryptography.hazmat.primitives.asymmetric import rsa

    from api import auth
    from common.config import settings

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    class SigningKey:
        key = private_key.public_key()

    class Keys:
        def get_signing_key_from_jwt(self, token: str) -> SigningKey:
            return SigningKey()

    monkeypatch.setattr(settings, "auth_mode", "oidc")
    monkeypatch.setattr(settings, "oidc_issuer", "http://keycloak/realms/antara")
    monkeypatch.setattr(settings, "oidc_client_id", "antara-web")
    monkeypatch.setattr(auth, "_jwks", lambda: Keys())
    good = _token(
        private_key,
        {"iss": settings.oidc_issuer, "azp": "antara-web", "preferred_username": "carol", "groups": ["/managers"]},
    )
    user = auth.current_user(authorization=f"Bearer {good}", x_user_id="", x_user_groups="")
    assert user.user_id == "carol" and user.groups == ["managers"]
    wrong_client = _token(private_key, {"iss": settings.oidc_issuer, "azp": "other", "sub": "x"})
    for header in ("", "Basic abc", f"Bearer {wrong_client}"):
        with pytest.raises(HTTPException) as error:
            auth.current_user(authorization=header, x_user_id="", x_user_groups="")
        assert error.value.status_code == 401


def test_jwks_url_defaults_to_the_issuer(monkeypatch) -> None:
    from api import auth
    from common.config import settings

    monkeypatch.setattr(settings, "oidc_issuer", "http://keycloak/realms/antara")
    monkeypatch.setattr(settings, "oidc_jwks_url", "")
    auth._jwks.cache_clear()
    assert auth._jwks().uri == "http://keycloak/realms/antara/protocol/openid-connect/certs"
    auth._jwks.cache_clear()
