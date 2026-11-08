import re

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

SECURITY_THREAT = re.compile(
    r"phishing|suspicious (email|link|message|attachment)|\bhacked\b|\bbreach\b|malware|ransomware|\bvirus\b"
)

OUTAGE = re.compile(
    r"\b(down|outage|offline|unreachable)\b"
    r"|not (working|connecting|loading|responding)"
    r"|(can't|cannot|can not|unable to|won't) (connect|log ?in|sign ?in|load|access|reach)"
    r"|keeps? (dropping|crashing|disconnecting|freezing)"
    r"|\bcrash(es|ed|ing)?\b"
    r"|\b(very|really|so) slow\b"
)

QUESTION_START = re.compile(r"^\s*(how|what|who|when|where|why|which|should|does|do)\b")

BROKEN_DEVICE = re.compile(
    r"\bmy (laptop|keyboard|mouse|monitor|screen|phone|headset|webcam|charger)\b"
    r".*\b(broken|cracked|dead|damaged|not working|won't turn on)\b"
)

SYSTEMS = (
    ("vpn", re.compile(r"\bvpn\b")),
    ("email", re.compile(r"\b(e-?mail|outlook|mailbox|gmail)\b")),
    ("wifi", re.compile(r"\b(wi-?fi|wireless)\b")),
    ("sso", re.compile(r"\b(sso|okta|single sign-?on|log ?in|sign ?in)\b")),
    ("printer", re.compile(r"\b(printer|printing)\b")),
    ("internet", re.compile(r"\binternet\b")),
    ("slack", re.compile(r"\bslack\b")),
    ("jira", re.compile(r"\bjira\b")),
    ("database", re.compile(r"\b(database|db)\b")),
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
    if any(phrase in lowered for phrase in ACTION_PHRASES) or BROKEN_DEVICE.search(lowered):
        return Intent.ACTION
    if SECURITY_THREAT.search(lowered) and not QUESTION_START.match(lowered):
        return Intent.INCIDENT
    if OUTAGE.search(lowered) and not QUESTION_START.match(lowered):
        return Intent.INCIDENT
    return Intent.QUESTION


def affected_system(text: str) -> str | None:
    lowered = text.lower()
    for name, pattern in SYSTEMS:
        if pattern.search(lowered):
            return name
    return None


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
