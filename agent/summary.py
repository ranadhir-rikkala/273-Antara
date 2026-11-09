from typing import Any

from common.models import IncidentResult


def summarize_tool_result(tool: str, output: dict[str, Any], approved_by: str | None = None) -> str:
    if tool == "create_ticket":
        return f"I created ticket {output['ticket_id']} for you. Its status is {output['status']}."
    if tool == "get_ticket_status":
        if not output.get("found"):
            return "I couldn't find that ticket among yours. Check the ticket ID and try again."
        return f"Ticket {output['ticket_id']} ({output['title']}) is {output['status']}."
    if tool == "request_access":
        approver = f" after {approved_by} approved it" if approved_by else ""
        granted = f"Your {output['role']} access to {output['system']} was granted{approver}."
        if "lease_seconds" not in output:
            return granted
        return f"{granted} It ends automatically in {describe_duration(output['lease_seconds'])}."
    return f"Done: {tool} finished."


def describe_duration(seconds: int) -> str:
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds >= size:
            count = seconds // size
            return f"{count} {unit}{'' if count == 1 else 's'}"
    return f"{seconds} seconds"


def incident_message(incident: IncidentResult) -> str:
    reference = f"INC-{incident.incident_id[:8]}"
    if incident.created:
        return f"I opened incident {reference} for this. The on-call team will follow up."
    others = incident.reporter_count - 1
    if others < 1:
        return f"You already reported this in incident {reference} ({incident.title}). Updates will follow there."
    people = "1 other person has" if others == 1 else f"{others} other people have"
    return f"This matches incident {reference} ({incident.title}), which {people} reported. Updates will follow there."
