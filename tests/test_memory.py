from agent.memory import MAX_HISTORY_TURNS, contextual_query, is_follow_up, recent
from common.models import ConversationTurn

PREVIOUS = [ConversationTurn("Who approves read access to production databases?", "The manager [1]")]


def test_short_or_connective_messages_are_follow_ups() -> None:
    assert is_follow_up("and what about write access?")
    assert is_follow_up("what about contractors?")
    assert is_follow_up("And for 7 days?")
    assert not is_follow_up("How often do standard account passwords have to be rotated?")


def test_follow_ups_are_searched_together_with_the_previous_question() -> None:
    assert contextual_query("and what about write access?", PREVIOUS) == (
        "Who approves read access to production databases? and what about write access?"
    )
    assert contextual_query("and what about write access?", []) == "and what about write access?"
    standalone = "How often do standard account passwords have to be rotated?"
    assert contextual_query(standalone, PREVIOUS) == standalone


def test_only_recent_turns_are_kept_and_long_text_is_trimmed() -> None:
    turns = [ConversationTurn(f"q{i}", "a" * 2000) for i in range(6)]
    kept = recent(turns)
    assert [turn.question for turn in kept] == ["q3", "q4", "q5"] and len(kept) == MAX_HISTORY_TURNS
    assert len(kept[0].answer) == 500
