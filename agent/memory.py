import re

from common.models import ConversationTurn

MAX_HISTORY_TURNS = 3
MAX_TURN_TEXT = 500
FOLLOW_UP_WORDS = 6
CONNECTIVE_START = re.compile(r"^\s*(and|also|what about|how about|same for|then|but|or)\b", re.IGNORECASE)


def recent(history: list[ConversationTurn]) -> list[ConversationTurn]:
    return [
        ConversationTurn(turn.question[:MAX_TURN_TEXT], turn.answer[:MAX_TURN_TEXT])
        for turn in history[-MAX_HISTORY_TURNS:]
    ]


def is_follow_up(question: str) -> bool:
    return bool(CONNECTIVE_START.match(question)) or len(question.split()) <= FOLLOW_UP_WORDS - 1


def contextual_query(question: str, history: list[ConversationTurn]) -> str:
    if not history or not is_follow_up(question):
        return question
    return f"{history[-1].question} {question}"
