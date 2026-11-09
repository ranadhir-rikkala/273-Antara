from api.ops.adapters import _pod_row


def pod(labels: dict[str, str]) -> dict:
    return {
        "metadata": {"name": "p-0", "labels": labels, "ownerReferences": [{"kind": "StatefulSet"}]},
        "spec": {"nodeName": "worker"},
        "status": {"phase": "Running", "containerStatuses": [{"ready": True, "restartCount": 1}]},
    }


def test_a_standby_is_shown_under_the_service_it_replicates() -> None:
    row = _pod_row(pod({"app": "app-db-standby", "antara/service": "app-db", "antara/role": "standby"}))
    assert (row["app"], row["role"], row["node"], row["owner"]) == ("app-db", "standby", "worker", "StatefulSet")


def test_ordinary_pods_keep_their_app_and_have_no_role() -> None:
    row = _pod_row(pod({"app": "agent"}))
    assert (row["app"], row["role"], row["ready"], row["restarts"]) == ("agent", None, True, 1)
