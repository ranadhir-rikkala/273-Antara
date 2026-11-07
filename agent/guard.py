import re

INJECTION = re.compile(
    r"(system instruction|instruction for the assistant|ignore (all|any|previous|prior|other)\b"
    r"|disregard (all|any|previous|the)\b|you are now|new instructions|assistant:"
    r"|tell (users|them|anyone|everyone|the user) to|(email|send|share) (their|your) (password|credentials))",
    re.IGNORECASE,
)

SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")

UNSAFE = re.compile(
    r"(email|send|share|give|tell|provide|type|enter)\b[^.]{0,40}\b(your|their)\b[^.]{0,20}"
    r"\b(password|passcode|mfa code|one-time code|otp|credentials|pin)\b",
    re.IGNORECASE,
)

SECRETS = (
    re.compile(r"ANTARA-CANARY-[A-Z0-9-]+"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\b(password|passwd|secret|api[_ -]?key|token)\s*[:=]\s*\S+"),
)

REDACTED = "[redacted]"


def sanitize_passage(text: str) -> tuple[str, bool]:
    sentences = SENTENCE.findall(text)
    kept = [sentence for sentence in sentences if not INJECTION.search(sentence)]
    if len(kept) == len(sentences):
        return text, False
    return " ".join(sentence.strip() for sentence in kept if sentence.strip()), True


def unsafe_answer(answer: str) -> str | None:
    match = UNSAFE.search(answer)
    return None if match is None else f"asks for credentials: {match.group(0)!r}"


def redact_secrets(text: str) -> tuple[str, int]:
    count = 0
    for pattern in SECRETS:
        text, replaced = pattern.subn(REDACTED, text)
        count += replaced
    return text, count
