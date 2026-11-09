from agent.triage import affected_system, classify_by_rules, needs_model, parse_intent, tools_for
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


def test_requests_are_narrowed_to_the_matching_tool() -> None:
    assert tools_for("I need read access to the production database") == ["request_access"]
    assert tools_for("What is the status of my ticket 42?") == ["get_ticket_status"]
    assert tools_for("Please create a ticket, my screen does not lock") == ["create_ticket"]
    assert tools_for("Can you help me with something?") is None


def test_every_report_from_the_live_storm_is_an_incident() -> None:
    for report in (
        "The VPN is down",
        "vpn not connecting",
        "VPN down for me too",
        "cannot connect to VPN",
        "Is the VPN down?",
        "VPN outage again",
        "Email is down",
        "wifi keeps dropping",
    ):
        assert classify_by_rules(report) == Intent.INCIDENT, report


def test_questions_about_outages_and_downloads_stay_questions() -> None:
    assert classify_by_rules("How do I download the VPN client?") == Intent.QUESTION
    assert classify_by_rules("What should I do if the VPN is down?") == Intent.QUESTION


def test_a_broken_personal_device_is_a_ticket_not_an_incident() -> None:
    assert classify_by_rules("My laptop screen is broken") == Intent.ACTION
    assert tools_for("My laptop screen is broken") == ["create_ticket"]


def test_affected_system_is_named_consistently() -> None:
    assert affected_system("The VPN is down") == "vpn"
    assert affected_system("cannot connect to vpn") == "vpn"
    assert affected_system("Outlook keeps crashing") == "email"
    assert affected_system("wi-fi keeps dropping") == "wifi"
    assert affected_system("something is weird") is None


def test_questions_about_security_topics_stay_questions_but_reports_are_incidents() -> None:
    for question in (
        "How do I report a phishing email?",
        "What should I do about a suspicious email?",
        "Does antivirus catch ransomware?",
        "how do I know if I've been hacked?",
    ):
        assert classify_by_rules(question) == Intent.QUESTION, question
    for report in (
        "I clicked a phishing link",
        "I think my account was hacked",
        "There is ransomware on the file server",
        "Got a suspicious email asking for my login",
    ):
        assert classify_by_rules(report) == Intent.INCIDENT, report


def test_a_report_that_also_asks_what_to_do_wants_guidance() -> None:
    from agent.triage import asks_for_help

    for text in (
        "The VPN is down for me since this morning, what should I do?",
        "VPN down again. How do I work in the meantime",
        "email is not working, can I use webmail",
        "Is the VPN down?",
        "vpn down what to do",
        "VPN down, need help",
        "email down any workaround",
        "wifi not working how to fix",
    ):
        assert asks_for_help(text) is True, text
    for text in (
        "The VPN is down",
        "vpn not working since 9am",
        "Email is down for everyone on my team",
        "Tell the helpdesk the VPN is down",
    ):
        assert asks_for_help(text) is False, text


def test_guidance_asks_a_focused_question_about_the_affected_system() -> None:
    from agent.triage import guidance_question

    assert guidance_question("VPN down, need help") == "What should I do if the VPN is down?"
    assert guidance_question("wifi not working how to fix") == "What should I do if the Wi-Fi is down?"
    assert guidance_question("my thing is broken, help") == "my thing is broken, help"


def test_only_outage_reports_get_a_rephrased_guidance_question() -> None:
    from agent.triage import guidance_question

    phishing = "I got a suspicious email asking for my password, what should I do?"
    assert guidance_question(phishing) == phishing


def test_a_message_phrased_as_a_question_is_not_sent_to_the_model_as_a_possible_request() -> None:
    assert needs_model("Do I need approval to export 5,000 customer records?", Intent.QUESTION) is False
    assert needs_model("I need read access to the billing database", Intent.QUESTION) is True


def test_security_reports_are_grouped_apart_from_outages_of_the_same_system() -> None:
    from agent.triage import incident_system

    assert incident_system("I got a suspicious email asking for my password") == "security"
    assert incident_system("I clicked a phishing link") == "security"
    assert incident_system("Email is down for everyone") == "email"
    assert incident_system("something is weird") is None
