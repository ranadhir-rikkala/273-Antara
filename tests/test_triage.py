from agent.triage import classify_by_rules, needs_model, parse_intent
from common.models import Intent


def test_rules_spot_incidents_actions_and_questions() -> None:
    assert classify_by_rules("The VPN is down again") == Intent.INCIDENT
    assert classify_by_rules("I got a suspicious email with a link") == Intent.INCIDENT
    assert classify_by_rules("I need access to the prod database") == Intent.ACTION
    assert classify_by_rules("Please create a ticket, my VPN is not working") == Intent.ACTION
    assert classify_by_rules("How do contractors get VPN access?") == Intent.QUESTION


def test_model_reply_is_parsed_or_rejected() -> None:
    assert parse_intent(" Incident.") == Intent.INCIDENT
    assert parse_intent("question") == Intent.QUESTION
    assert parse_intent("I think this is an incident") is None


def test_model_is_only_asked_about_ambiguous_requests() -> None:
    assert needs_model("How do contractors get VPN access?", Intent.QUESTION) is False
    assert needs_model("The VPN is down", Intent.INCIDENT) is False
    assert needs_model("Can you set up my laptop encryption?", Intent.QUESTION) is True
