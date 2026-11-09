import re
from pathlib import Path
from urllib.parse import urlparse

from api.services import inside_cluster

OVERLAY = Path(__file__).resolve().parent.parent / "deploy" / "local" / "overlay" / "kustomization.yaml"


def test_every_model_host_the_gateway_calls_counts_as_inside_the_cluster() -> None:
    urls = [url for value in re.findall(r'value: "(http[^"]+)"', OVERLAY.read_text()) for url in value.split(",")]
    hosts = {urlparse(url).hostname for url in urls}
    assert hosts
    assert {host for host in hosts if not inside_cluster(host)} == set()
