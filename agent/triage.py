from temporalio import activity

from common.llm import LLMProvider
from common.models import Intent
from common.names import Activities

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


def parse_intent(reply: str) -> Intent | None:
    word = reply.strip().strip(".").lower()
    return Intent(word) if word in {intent.value for intent in Intent} else None


@activity.defn(name=Activities.CLASSIFY)
async def classify_request(question: str) -> str:
    llm = LLMProvider()
    try:
        reply = await llm.generate(
            [{"role": "system", "content": CLASSIFY_PROMPT}, {"role": "user", "content": question}]
        )
    except Exception as error:
        activity.logger.warning("Classifier unavailable, using rules: %s", error)
        return classify_by_rules(question)
    finally:
        await llm.aclose()
    return parse_intent(reply) or classify_by_rules(question)
