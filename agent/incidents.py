from psycopg.types.json import Jsonb
from temporalio import activity

from common.config import settings
from common.db import pool, vector_literal
from common.llm import LLMProvider
from common.models import IncidentReport, IncidentResult
from common.names import Activities

DEDUP_LOCK = "antara-incident-dedup"
TITLE_LENGTH = 120

_MATCH_SQL = """
SELECT incident_id::text, title
FROM incidents
WHERE status = 'OPEN'
  AND updated_at > now() - make_interval(mins => %(window)s)
  AND 1 - (embedding <=> %(embedding)s::vector) >= %(threshold)s
ORDER BY embedding <=> %(embedding)s::vector
LIMIT 1
"""


@activity.defn(name=Activities.REPORT_INCIDENT)
async def report_incident(report: IncidentReport) -> IncidentResult:
    llm = LLMProvider()
    try:
        [embedding] = await llm.embed([report.text])
    finally:
        await llm.aclose()
    vector = vector_literal(embedding)
    db = await pool()
    async with db.connection() as conn, conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (DEDUP_LOCK,))
        existing = await (
            await conn.execute(
                "SELECT i.incident_id::text, i.title, i.reporter_count FROM incident_reports r "
                "JOIN incidents i ON i.incident_id = r.incident_id WHERE r.report_id = %s",
                (report.report_id,),
            )
        ).fetchone()
        if existing:
            return IncidentResult(existing[0], existing[1], created=False, reporter_count=existing[2])
        match = await (
            await conn.execute(
                _MATCH_SQL,
                {
                    "window": settings.incident_window_minutes,
                    "embedding": vector,
                    "threshold": settings.incident_similarity,
                },
            )
        ).fetchone()
        created = match is None
        if created:
            title = report.text.strip()[:TITLE_LENGTH]
            incident_id = (
                await (
                    await conn.execute(
                        "INSERT INTO incidents (title, embedding) VALUES (%s, %s::vector) RETURNING incident_id::text",
                        (title, vector),
                    )
                ).fetchone()
            )[0]
        else:
            incident_id, title = match
        await conn.execute(
            "INSERT INTO incident_reports (report_id, incident_id, reporter_id, text) VALUES (%s, %s, %s, %s)",
            (report.report_id, incident_id, report.reporter.user_id, report.text),
        )
        count = (
            await (
                await conn.execute(
                    "UPDATE incidents SET reporter_count = "
                    "(SELECT count(*) FROM incident_reports WHERE incident_id = %s), updated_at = now() "
                    "WHERE incident_id = %s RETURNING reporter_count",
                    (incident_id, incident_id),
                )
            ).fetchone()
        )[0]
        await conn.execute(
            "INSERT INTO audit_log (actor_id, action, detail) VALUES (%s, %s, %s)",
            (
                report.reporter.user_id,
                "incident.opened" if created else "incident.linked",
                Jsonb({"incident_id": incident_id, "report_id": report.report_id}),
            ),
        )
    return IncidentResult(incident_id=incident_id, title=title, created=created, reporter_count=count)
