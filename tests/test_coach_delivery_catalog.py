"""Tests that the delivery channels are DISCOVERABLE + GUIDED (Task #77).

Two hermetic guarantees (no network, no DB, no real providers):

  1. The Service Directory catalog (``src/web/service_catalog.py``) surfaces the
     EXACT env vars the delivery layer (``src/coach/delivery.py``) reads for each
     channel, with guided signup (a key_url + setup steps) so a user can actually
     connect a provider. Google Calendar keeps its preferred OAuth connector path.
  2. ``delivery_status()`` flips a channel from "not configured" to "configured"
     exactly when those env vars are present — so the per-channel status the Coach
     Approvals tab renders is driven by the SAME env vars the catalog advertises.

Runnable two ways:
  * ``python tests/test_coach_delivery_catalog.py``  (standalone, non-zero on fail)
  * ``pytest tests/test_coach_delivery_catalog.py``   (test_* functions; no plugins)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.web import service_catalog as cat
from src.coach import delivery as delivery_mod


# The canonical env vars delivery.py reads, surfaced as first-class input rows.
_REQUIRED = {
    "resend": {"RESEND_API_KEY", "RESEND_FROM"},
    "twilio": {"TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM"},
    "google-calendar": {"GOOGLE_CALENDAR_ACCESS_TOKEN"},
}

# Accepted fallback aliases delivery.py ALSO reads (documented in labels/steps so
# a user who already has these set knows they work — not promoted as extra rows).
_FALLBACK_ALIASES = {
    "resend": {"EMAIL_FROM"},
    "twilio": {"TWILIO_FROM_NUMBER"},
    "google-calendar": set(),
}


# -- (1) catalog surfaces the exact delivery env vars + guided signup ---------

def _catalog_vars(slug):
    svc = cat.get_service(slug)
    return {e["var"] for e in (svc.get("env_vars") or [])} if svc else set()


def _entry_text(slug):
    """All user-visible text for an entry (env var names, labels, steps, desc)."""
    svc = cat.get_service(slug) or {}
    parts = [svc.get("env_var") or "", svc.get("description") or ""]
    parts += list(svc.get("steps") or [])
    for e in (svc.get("env_vars") or []):
        parts += [e.get("var") or "", e.get("label") or ""]
    return "\n".join(parts)


def test_catalog_lists_exact_delivery_env_vars():
    for slug, required in _REQUIRED.items():
        listed = _catalog_vars(slug)
        missing = required - listed
        assert not missing, \
            "%s catalog entry is missing delivery env vars: %s" % (slug, missing)


def test_catalog_documents_every_var_delivery_reads():
    # Guards against drift: EVERY env var delivery.py accepts (canonical AND
    # fallback aliases) must be discoverable somewhere in the catalog entry.
    for slug in _REQUIRED:
        text = _entry_text(slug)
        for var in _REQUIRED[slug] | _FALLBACK_ALIASES[slug]:
            assert var in text, \
                "%s entry never mentions env var delivery.py reads: %s" % (slug, var)


def test_delivery_providers_are_guided_in_directory():
    manual = {s["slug"] for s in cat.manual_services()}
    for slug in _REQUIRED:
        svc = cat.get_service(slug)
        assert svc is not None, "missing catalog entry: %s" % slug
        assert slug in manual, \
            "%s must appear in the Service Directory (needs key_url)" % slug
        assert svc.get("steps"), "%s needs guided setup steps" % slug
        assert svc.get("key_url"), "%s needs a signup/get-key link" % slug


def test_google_calendar_keeps_connector_path():
    # The manual fallback must NOT drop the preferred OAuth connector path.
    connectors = {s["slug"] for s in cat.connector_services()}
    assert "google-calendar" in connectors, \
        "google-calendar must remain a connector (preferred OAuth path)"


# -- (2) delivery_status() flips per channel on those exact env vars ----------

def _patch_env(env):
    """Patch the delivery seams so creds come ONLY from a fake env dict."""
    saved = (delivery_mod.get_connection_settings, delivery_mod.get_access_token,
             delivery_mod.get_secret, delivery_mod.get_env)
    delivery_mod.get_connection_settings = lambda *a, **k: None
    delivery_mod.get_access_token = lambda *a, **k: None
    delivery_mod.get_secret = lambda name, *a, **k: env.get(name)
    delivery_mod.get_env = lambda name, default=None, *a, **k: env.get(name, default)

    def restore():
        (delivery_mod.get_connection_settings, delivery_mod.get_access_token,
         delivery_mod.get_secret, delivery_mod.get_env) = saved
    return restore


def test_delivery_status_unconfigured_by_default():
    restore = _patch_env({})
    try:
        st = delivery_mod.delivery_status()
        assert st["email"]["configured"] is False, st["email"]
        assert st["sms"]["configured"] is False, st["sms"]
        assert st["calendar"]["configured"] is False, st["calendar"]
    finally:
        restore()


def test_email_configures_only_with_both_vars():
    # API key alone is not enough — Resend needs a verified sender too.
    restore = _patch_env({"RESEND_API_KEY": "re_x"})
    try:
        assert delivery_mod.email_status()["configured"] is False
    finally:
        restore()
    restore = _patch_env({"RESEND_API_KEY": "re_x", "RESEND_FROM": "me@x.com"})
    try:
        s = delivery_mod.email_status()
        assert s["configured"] is True and s["provider"] == "resend", s
    finally:
        restore()


def test_sms_configures_only_with_all_three_vars():
    restore = _patch_env({"TWILIO_ACCOUNT_SID": "AC", "TWILIO_AUTH_TOKEN": "tok"})
    try:
        assert delivery_mod.sms_status()["configured"] is False  # no FROM
    finally:
        restore()
    restore = _patch_env({"TWILIO_ACCOUNT_SID": "AC", "TWILIO_AUTH_TOKEN": "tok",
                          "TWILIO_FROM": "+15551234567"})
    try:
        s = delivery_mod.sms_status()
        assert s["configured"] is True and s["provider"] == "twilio", s
    finally:
        restore()


def test_calendar_configures_with_token():
    restore = _patch_env({})
    try:
        assert delivery_mod.calendar_status()["configured"] is False
    finally:
        restore()
    restore = _patch_env({"GOOGLE_CALENDAR_ACCESS_TOKEN": "ya29.x"})
    try:
        s = delivery_mod.calendar_status()
        assert s["configured"] is True and s["provider"] == "google-calendar", s
    finally:
        restore()


def test_channel_configured_matches_status():
    restore = _patch_env({"RESEND_API_KEY": "re_x", "RESEND_FROM": "me@x.com"})
    try:
        assert delivery_mod.channel_configured("email") is True
        assert delivery_mod.channel_configured("sms") is False
        assert delivery_mod.channel_configured("calendar") is False
    finally:
        restore()


def _run_all():
    tests = [
        test_catalog_lists_exact_delivery_env_vars,
        test_catalog_documents_every_var_delivery_reads,
        test_delivery_providers_are_guided_in_directory,
        test_google_calendar_keeps_connector_path,
        test_delivery_status_unconfigured_by_default,
        test_email_configures_only_with_both_vars,
        test_sms_configures_only_with_all_three_vars,
        test_calendar_configures_with_token,
        test_channel_configured_matches_status,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print("PASS", t.__name__)
        except AssertionError as e:
            failed += 1
            print("FAIL", t.__name__, "->", e)
        except Exception as e:  # noqa: BLE001
            failed += 1
            print("ERROR", t.__name__, "->", type(e).__name__, e)
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
