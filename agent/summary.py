from typing import Any


def summarize_tool_result(tool: str, output: dict[str, Any], approved_by: str | None = None) -> str:
    if tool == "create_ticket":
        return f"I created ticket {output['ticket_id']} for you. Its status is {output['status']}."
    if tool == "get_ticket_status":
        if not output.get("found"):
            return "I couldn't find that ticket among yours. Check the ticket ID and try again."
        return f"Ticket {output['ticket_id']} ({output['title']}) is {output['status']}."
    if tool == "request_access":
        approver = f" after {approved_by} approved it" if approved_by else ""
        return f"Your {output['role']} access to {output['system']} was granted{approver}."
    return f"Done: {tool} finished."
