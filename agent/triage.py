from common.models import Intent

ACTION_PHRASES = (
    "need access",
    "request access",
    "grant me",
    "give me access",
    "create a ticket",
    "open a ticket",
    "file a ticket",
    "ticket status",
    "status of ticket",
    "status of my ticket",
)

INCIDENT_PHRASES = (
    " is down",
    " are down",
    "outage",
    "not working",
    "can't connect",
    "cannot connect",
    "unable to connect",
    "phishing",
    "suspicious email",
    "suspicious link",
    "hacked",
    "breach",
    "malware",
    "ransomware",
)

REQUEST_MARKERS = (
    "i need",
    "i want",
    "please",
    "can you",
    "could you",
    "give me",
    "grant",
    "create",
    "open a",
    "reset my",
    "set up",
)

TOOL_HINTS = (
    (("access",), "request_access"),
    (("ticket status", "status of ticket", "status of my ticket", "check my ticket"), "get_ticket_status"),
    (("ticket", "broken", "not working", "doesn't work", "does not"), "create_ticket"),
)

CLASSIFY_PROMPT = (
    "Classify the employee's message for an IT and security helpdesk. "
    "Reply with exactly one word: question (asks how something works or what a policy says), "
    "action (asks you to do something such as grant access or create or check a ticket), "
    "or incident (reports something broken, an outage, or a security threat)."
)


def classify_by_rules(text: str) -> Intent:
    lowered = f" {text.lower()} "
    if any(phrase in lowered for phrase in ACTION_PHRASES):
        return Intent.ACTION
    if any(phrase in lowered for phrase in INCIDENT_PHRASES):
        return Intent.INCIDENT
    return Intent.QUESTION


def needs_model(text: str, by_rules: Intent) -> bool:
    lowered = f" {text.lower()} "
    return by_rules == Intent.QUESTION and any(marker in lowered for marker in REQUEST_MARKERS)


def tools_for(text: str) -> list[str] | None:
    lowered = text.lower()
    for phrases, tool in TOOL_HINTS:
        if any(phrase in lowered for phrase in phrases):
            return [tool]
    return None


def parse_intent(reply: str) -> Intent | None:
    word = reply.strip().strip(".").lower()
    return Intent(word) if word in {intent.value for intent in Intent} else None
