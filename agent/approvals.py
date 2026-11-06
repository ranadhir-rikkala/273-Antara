from psycopg.types.json import Jsonb
from temporalio import activity

from common.db import pool
from common.models import ApprovalOpenRequest
from common.names import Activities


@activity.defn(name=Activities.OPEN_APPROVAL)
async def open_approval(request: ApprovalOpenRequest) -> str:
    args = request.call.arguments
    db = await pool()
    async with db.connection() as conn, conn.transaction():
        row = await (
            await conn.execute(
                "INSERT INTO approvals (approval_key, run_id, requester_id, system, role, reason, status) "
                "VALUES (%s, %s, %s, %s, %s, %s, 'PENDING') "
                "ON CONFLICT (approval_key) DO UPDATE SET approval_key = EXCLUDED.approval_key "
                "RETURNING approval_id::text",
                (
                    request.approval_key,
                    request.run_id,
                    request.user.user_id,
                    args.get("system", ""),
                    args.get("role", ""),
                    args.get("reason", ""),
                ),
            )
        ).fetchone()
        await conn.execute(
            "INSERT INTO audit_log (actor_id, action, detail) VALUES (%s, 'approval.opened', %s)",
            (request.user.user_id, Jsonb({"approval_id": row[0], "run_id": request.run_id, "tool": request.call.name})),
        )
    return row[0]


@activity.defn(name=Activities.ESCALATE_APPROVAL)
async def escalate_approval(approval_id: str) -> None:
    db = await pool()
    async with db.connection() as conn, conn.transaction():
        await conn.execute(
            "UPDATE approvals SET escalated = true WHERE approval_id = %s AND status = 'PENDING'", (approval_id,)
        )
        await conn.execute(
            "INSERT INTO audit_log (actor_id, action, detail) VALUES ('system', 'approval.escalated', %s)",
            (Jsonb({"approval_id": approval_id}),),
        )
