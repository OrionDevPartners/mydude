"""Tests for the Gmail one-time-code (OTP) bridge.

These cover emailed-code retrieval end-to-end *minus* the network: code
extraction from email text, the Gmail message parser (subject/snippet/body),
the governance gate, the integrations honest "not connected" path, and the
subscription manager's SMS-then-Gmail fallback. No Gmail account, OAuth token,
or real mailbox is required — the connector token and broker are faked.

Runnable two ways:
  * ``python tests/test_gmail_otp.py``  (standalone, exits non-zero on failure)
  * ``pytest tests/test_gmail_otp.py``   (test_* functions; no plugins needed)
"""
import asyncio
import base64
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.bridge.gmail_otp import GmailOtpReader, GmailBridgeError
from src.swarm.policy import PolicyEngine
from src.swarm.integrations import Integrations
from src.subscriptions import manager


# -- code extraction ----------------------------------------------------------

def test_extract_codes_basic():
    assert GmailOtpReader.extract_codes("Your code is 482913.") == ["482913"]


def test_extract_codes_grouped():
    assert GmailOtpReader.extract_codes("Use 123-456 to sign in") == ["123456"]


def test_extract_codes_none():
    assert GmailOtpReader.extract_codes("no digits worth keeping") == []


def test_extract_codes_ignores_too_long():
    # 9+ digit runs are not OTPs (phone numbers, ids).
    assert GmailOtpReader.extract_codes("ref 1234567890123") == []


# -- message parsing ----------------------------------------------------------

def _b64(s):
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


def test_message_text_plain_body():
    msg = {
        "snippet": "Here is your code",
        "payload": {
            "headers": [{"name": "Subject", "value": "Verify your login"}],
            "mimeType": "text/plain",
            "body": {"data": _b64("Your verification code is 778899.")},
        },
    }
    text = GmailOtpReader._message_text(msg)
    assert "Verify your login" in text
    assert "778899" in text


def test_message_text_multipart_and_html_fallback():
    msg = {
        "snippet": "",
        "payload": {
            "headers": [{"name": "Subject", "value": "Security code"}],
            "mimeType": "multipart/alternative",
            "parts": [
                {"mimeType": "text/html",
                 "body": {"data": _b64("<p>Your code: <b>551133</b></p>")}},
            ],
        },
    }
    text = GmailOtpReader._message_text(msg)
    assert "551133" in text
    # HTML tags are stripped before extraction works.
    assert GmailOtpReader.extract_codes(text) == ["551133"]


# -- governance gate ----------------------------------------------------------

def test_policy_allows_gmail_by_default():
    os.environ.pop("ENABLE_GMAIL_CAPABILITY", None)
    decision = PolicyEngine().evaluate("gmail_fetch_code", {})
    assert decision.allowed, decision.reason


def test_policy_blocks_gmail_when_disabled():
    os.environ["ENABLE_GMAIL_CAPABILITY"] = "false"
    try:
        decision = PolicyEngine().evaluate("gmail_fetch_code", {})
        assert not decision.allowed
        assert "disabled" in decision.reason.lower()
    finally:
        os.environ.pop("ENABLE_GMAIL_CAPABILITY", None)


# -- integrations honest "not connected" path --------------------------------

def test_integrations_gmail_not_connected_is_honest(monkeypatch=None):
    import src.web.connectors as connectors
    orig = connectors.get_access_token
    connectors.get_access_token = lambda name: None
    try:
        out = asyncio.run(Integrations().gmail_fetch_code({"source": "test"}))
        assert out.lower().startswith("gmail bridge error"), out
    finally:
        connectors.get_access_token = orig


# -- reader honest behaviors --------------------------------------------------

def test_reader_available_false_without_token():
    import src.web.connectors as connectors
    orig = connectors.get_access_token
    connectors.get_access_token = lambda name: None
    try:
        assert GmailOtpReader().available() is False
    finally:
        connectors.get_access_token = orig


def test_reader_raises_without_token():
    reader = GmailOtpReader(access_token=None)
    import src.web.connectors as connectors
    orig = connectors.get_access_token
    connectors.get_access_token = lambda name: None
    try:
        raised = False
        try:
            reader.fetch_recent_code()
        except GmailBridgeError:
            raised = True
        assert raised
    finally:
        connectors.get_access_token = orig


# -- manager SMS-then-Gmail fallback -----------------------------------------

class _FakeDecision:
    def __init__(self, allowed, reason=""):
        self.allowed = allowed
        self.reason = reason


class _FakeResult:
    def __init__(self, allowed, output, reason=""):
        self.decision = _FakeDecision(allowed, reason)
        self.output = output


class _RoutingBroker:
    """Broker that returns a configured result per capability name."""
    def __init__(self, results):
        self._results = results

    async def request(self, capability, params):
        return self._results.get(capability, _FakeResult(True, ""))


def test_otp_prefers_sms_when_available():
    broker = _RoutingBroker({
        "ssh_fetch_code": _FakeResult(True, "Most recent verification code: 111111"),
        "gmail_fetch_code": _FakeResult(True, "Most recent verification code: 222222"),
    })
    assert asyncio.run(manager._maybe_fetch_otp(broker)) == "111111"


def test_otp_falls_back_to_gmail_when_sms_empty():
    broker = _RoutingBroker({
        "ssh_fetch_code": _FakeResult(True, "No recent messages were readable."),
        "gmail_fetch_code": _FakeResult(True, "Most recent verification code: 222222"),
    })
    assert asyncio.run(manager._maybe_fetch_otp(broker)) == "222222"


def test_otp_none_when_both_fail():
    broker = _RoutingBroker({
        "ssh_fetch_code": _FakeResult(True, "SSH bridge error: down"),
        "gmail_fetch_code": _FakeResult(True, "Gmail bridge error: not connected."),
    })
    assert asyncio.run(manager._maybe_fetch_otp(broker)) is None


def test_otp_gmail_blocked_is_honest_none():
    broker = _RoutingBroker({
        "ssh_fetch_code": _FakeResult(True, "No recent messages were readable."),
        "gmail_fetch_code": _FakeResult(False, None, reason="Gmail capability is disabled."),
    })
    assert asyncio.run(manager._maybe_fetch_otp(broker)) is None


# -- recorded Gmail REST round-trip ------------------------------------------
# These exercise the real fetch_recent_code logic (search-query building, the
# messages.list -> messages.get walk, and the "prefer an OTP-term-anchored
# code, else newest code" selection) by stubbing only the HTTP layer
# (_api_get) with recorded Gmail JSON payloads. No network or OAuth needed.

def _plain_msg(subject, body):
    return {
        "payload": {
            "headers": [{"name": "Subject", "value": subject}],
            "mimeType": "text/plain",
            "body": {"data": _b64(body)},
        },
    }


def _make_reader_with_api(list_payload, get_payloads, list_error=None):
    """Reader whose HTTP layer replays recorded Gmail JSON.

    ``list_payload`` is the messages.list response; ``get_payloads`` maps a
    message id to its messages.get response. ``list_error`` (if set) is raised
    instead of returning the listing, to simulate a transport failure. Every
    call is recorded on ``reader.captured`` for query-construction asserts.
    """
    reader = GmailOtpReader(access_token="fake-token")
    reader.captured = []

    def fake_api_get(token, path, params=None):
        assert token == "fake-token"
        reader.captured.append({"path": path, "params": params})
        if path == "/messages":
            if list_error is not None:
                raise list_error
            return list_payload
        mid = path.rsplit("/", 1)[-1]
        return get_payloads[mid]

    reader._api_get = fake_api_get
    return reader


def test_fetch_recent_code_typical():
    reader = _make_reader_with_api(
        {"messages": [{"id": "m1"}]},
        {"m1": _plain_msg("Sign in", "Your code is 482913. It expires soon.")},
    )
    out = reader.fetch_recent_code()
    assert out.startswith("Most recent verification code: 482913"), out


def test_fetch_recent_code_html_only():
    msg = {
        "payload": {
            "headers": [{"name": "Subject", "value": "Security code"}],
            "mimeType": "multipart/alternative",
            "parts": [
                {"mimeType": "text/html",
                 "body": {"data": _b64("<p>Your verification code: <b>551133</b></p>")}},
            ],
        },
    }
    reader = _make_reader_with_api({"messages": [{"id": "m1"}]}, {"m1": msg})
    out = reader.fetch_recent_code()
    assert out.startswith("Most recent verification code: 551133"), out


def test_fetch_recent_code_no_otp_mail():
    reader = _make_reader_with_api(
        {"messages": [{"id": "m1"}, {"id": "m2"}]},
        {
            "m1": _plain_msg("Weekly newsletter", "Thanks for reading our updates today."),
            "m2": _plain_msg("Welcome aboard", "We are glad to have you with us."),
        },
    )
    out = reader.fetch_recent_code()
    assert out == "No verification code was found in recent email.", out


def test_fetch_recent_code_empty_listing():
    reader = _make_reader_with_api({"messages": []}, {})
    out = reader.fetch_recent_code(within_minutes=10)
    assert out.startswith("No recent verification email"), out


def test_fetch_recent_code_http_error_surfaces():
    reader = _make_reader_with_api(
        {"messages": []}, {},
        list_error=GmailBridgeError("Gmail API returned HTTP 401."),
    )
    raised = False
    try:
        reader.fetch_recent_code()
    except GmailBridgeError:
        raised = True
    assert raised


def test_fetch_recent_code_prefers_otp_anchored():
    # Newest message has a (non-OTP) number; an OTP-anchored one is preferred.
    reader = _make_reader_with_api(
        {"messages": [{"id": "m1"}, {"id": "m2"}]},
        {
            "m1": _plain_msg("Receipt", "Order 998877 confirmed."),
            "m2": _plain_msg("Login", "Your login code is 445566."),
        },
    )
    out = reader.fetch_recent_code()
    assert out.startswith("Most recent verification code: 445566"), out


def test_fetch_recent_code_fallback_when_no_otp_term():
    # Terse "123456 to continue" with no OTP term still yields the code.
    reader = _make_reader_with_api(
        {"messages": [{"id": "m1"}]},
        {"m1": _plain_msg("Hi", "Use 123456 to continue.")},
    )
    out = reader.fetch_recent_code()
    assert out.startswith("Most recent verification code: 123456"), out


def test_fetch_recent_code_builds_query():
    reader = _make_reader_with_api(
        {"messages": [{"id": "m1"}]},
        {"m1": _plain_msg("Sign in", "Your code is 482913.")},
    )
    reader.fetch_recent_code(within_minutes=10)
    list_call = next(c for c in reader.captured if c["path"] == "/messages")
    q = list_call["params"]["q"]
    assert q.startswith("after:"), q
    assert "verification" in q, q
    assert list_call["params"]["maxResults"] == 10, list_call["params"]


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print("ok   %s" % fn.__name__)
        except Exception as e:
            failed += 1
            print("FAIL %s: %s: %s" % (fn.__name__, type(e).__name__, e))
    print("\n%d/%d passed" % (len(fns) - failed, len(fns)))
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
