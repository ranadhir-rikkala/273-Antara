from prometheus_client import Counter, Gauge

REJECTED = Counter("antara_api_rejected_total", "Requests rejected with HTTP 429", ["reason"])
QUEUE_BACKLOG = Gauge("antara_queue_backlog", "Last observed activity backlog per task queue", ["queue"])


class PrometheusAdmissionMetrics:
    def rejected(self, reason: str) -> None:
        REJECTED.labels(reason).inc()

    def backlog(self, queue: str, value: int) -> None:
        QUEUE_BACKLOG.labels(queue).set(value)
