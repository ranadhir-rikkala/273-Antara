from functools import lru_cache
from typing import Any

import jwt
from fastapi import Header, HTTPException
from jwt import PyJWKClient

from common.config import settings
from common.models import UserContext


def groups_from_claims(claims: dict[str, Any]) -> list[str]:
    return sorted({group.strip("/").split("/")[-1] for group in claims.get("groups", []) if group.strip("/")})


@lru_cache(maxsize=1)
def _jwks() -> PyJWKClient:
    url = settings.oidc_jwks_url or f"{settings.oidc_issuer}/protocol/openid-connect/certs"
    return PyJWKClient(url, cache_keys=True)


def verify_token(token: str) -> dict[str, Any]:
    key = _jwks().get_signing_key_from_jwt(token).key
    claims = jwt.decode(token, key, algorithms=["RS256"], issuer=settings.oidc_issuer, options={"verify_aud": False})
    if settings.oidc_client_id and claims.get("azp") != settings.oidc_client_id:
        raise jwt.InvalidTokenError("Token was issued to a different client")
    return claims


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"})


def current_user(
    authorization: str = Header(""),
    x_user_id: str = Header(""),
    x_user_groups: str = Header(""),
) -> UserContext:
    if settings.auth_mode == "headers":
        if not x_user_id:
            raise _unauthorized("Send X-User-Id and X-User-Groups headers")
        return UserContext(user_id=x_user_id, groups=[g.strip() for g in x_user_groups.split(",") if g.strip()])
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise _unauthorized("Missing bearer token")
    try:
        claims = verify_token(token)
    except (jwt.PyJWTError, jwt.PyJWKClientError) as error:
        raise _unauthorized(f"Invalid token: {error}") from error
    return UserContext(user_id=claims.get("preferred_username") or claims["sub"], groups=groups_from_claims(claims))
