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
