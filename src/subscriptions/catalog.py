"""Curated catalog of known subscription / recurring-billing services.

Discovery matches hostnames seen in the user's browser history against this
catalog to infer likely subscriptions. The catalog is deliberately
*best-effort*: a match means "the user visited this service", not "the user
definitely pays for it" — confirmation is always left to the user.

Each entry carries the URLs the login/manage flow needs:
- ``login_url``: where the sign-in form lives.
- ``account_url``: the account / billing / membership page to reach after login.
- ``cancel_hint``: human guidance for where the cancel flow tends to live.
"""

import re

SUBSCRIPTION_CATALOG = [
    {
        "slug": "netflix",
        "name": "Netflix",
        "domains": ["netflix.com"],
        "login_url": "https://www.netflix.com/login",
        "account_url": "https://www.netflix.com/account",
        "est_cost": "$15.49/mo",
        "cancel_hint": "Account → Membership & Billing → Cancel Membership",
    },
    {
        "slug": "spotify",
        "name": "Spotify",
        "domains": ["spotify.com"],
        "login_url": "https://accounts.spotify.com/en/login",
        "account_url": "https://www.spotify.com/account/subscription/",
        "est_cost": "$11.99/mo",
        "cancel_hint": "Account → Your plan → Change or cancel",
    },
    {
        "slug": "youtube-premium",
        "name": "YouTube Premium",
        "domains": ["youtube.com"],
        "login_url": "https://accounts.google.com/ServiceLogin",
        "account_url": "https://www.youtube.com/paid_memberships",
        "est_cost": "$13.99/mo",
        "cancel_hint": "Paid memberships → Manage membership → Deactivate",
    },
    {
        "slug": "amazon-prime",
        "name": "Amazon Prime",
        "domains": ["amazon.com", "primevideo.com"],
        "login_url": "https://www.amazon.com/ap/signin",
        "account_url": "https://www.amazon.com/gp/primecentral",
        "est_cost": "$14.99/mo",
        "cancel_hint": "Prime membership → Manage → End membership",
    },
    {
        "slug": "disney-plus",
        "name": "Disney+",
        "domains": ["disneyplus.com"],
        "login_url": "https://www.disneyplus.com/login",
        "account_url": "https://www.disneyplus.com/account/subscription",
        "est_cost": "$13.99/mo",
        "cancel_hint": "Account → Subscription → Cancel Subscription",
    },
    {
        "slug": "hulu",
        "name": "Hulu",
        "domains": ["hulu.com"],
        "login_url": "https://auth.hulu.com/web/login",
        "account_url": "https://secure.hulu.com/account",
        "est_cost": "$17.99/mo",
        "cancel_hint": "Account → Cancel Your Subscription",
    },
    {
        "slug": "hbo-max",
        "name": "Max (HBO)",
        "domains": ["max.com", "hbomax.com"],
        "login_url": "https://auth.max.com/login",
        "account_url": "https://www.max.com/account",
        "est_cost": "$16.99/mo",
        "cancel_hint": "Account → Subscription → Manage Subscription",
    },
    {
        "slug": "apple",
        "name": "Apple (iCloud / TV+ / Music)",
        "domains": ["apple.com", "icloud.com"],
        "login_url": "https://account.apple.com/sign-in",
        "account_url": "https://account.apple.com/account/manage/subscriptions",
        "est_cost": None,
        "cancel_hint": "Subscriptions → select service → Cancel Subscription",
    },
    {
        "slug": "adobe",
        "name": "Adobe Creative Cloud",
        "domains": ["adobe.com"],
        "login_url": "https://account.adobe.com/",
        "account_url": "https://account.adobe.com/plans",
        "est_cost": "$59.99/mo",
        "cancel_hint": "Plans → Manage plan → Cancel plan",
    },
    {
        "slug": "microsoft365",
        "name": "Microsoft 365",
        "domains": ["microsoft.com", "office.com"],
        "login_url": "https://login.live.com/",
        "account_url": "https://account.microsoft.com/services",
        "est_cost": "$9.99/mo",
        "cancel_hint": "Services & subscriptions → Manage → Cancel",
    },
    {
        "slug": "google-one",
        "name": "Google One / Workspace",
        "domains": ["one.google.com"],
        "login_url": "https://accounts.google.com/ServiceLogin",
        "account_url": "https://one.google.com/settings",
        "est_cost": "$1.99/mo",
        "cancel_hint": "Settings → Cancel membership",
    },
    {
        "slug": "dropbox",
        "name": "Dropbox",
        "domains": ["dropbox.com"],
        "login_url": "https://www.dropbox.com/login",
        "account_url": "https://www.dropbox.com/account/plan",
        "est_cost": "$11.99/mo",
        "cancel_hint": "Account → Plan → Cancel plan",
    },
    {
        "slug": "notion",
        "name": "Notion",
        "domains": ["notion.so"],
        "login_url": "https://www.notion.so/login",
        "account_url": "https://www.notion.so/my-account",
        "est_cost": "$10/mo",
        "cancel_hint": "Settings → Plans → Change / cancel plan",
    },
    {
        "slug": "github",
        "name": "GitHub",
        "domains": ["github.com"],
        "login_url": "https://github.com/login",
        "account_url": "https://github.com/settings/billing",
        "est_cost": "$4/mo",
        "cancel_hint": "Settings → Billing and plans → downgrade to Free",
    },
    {
        "slug": "linkedin-premium",
        "name": "LinkedIn Premium",
        "domains": ["linkedin.com"],
        "login_url": "https://www.linkedin.com/login",
        "account_url": "https://www.linkedin.com/premium/manage/",
        "est_cost": "$39.99/mo",
        "cancel_hint": "Premium → Manage Premium account → Cancel subscription",
    },
    {
        "slug": "nytimes",
        "name": "The New York Times",
        "domains": ["nytimes.com"],
        "login_url": "https://myaccount.nytimes.com/auth/login",
        "account_url": "https://www.nytimes.com/subscription",
        "est_cost": "$17/mo",
        "cancel_hint": "Account → Manage Subscription → Cancel",
    },
    {
        "slug": "audible",
        "name": "Audible",
        "domains": ["audible.com"],
        "login_url": "https://www.audible.com/sign-in",
        "account_url": "https://www.audible.com/account/membership-details",
        "est_cost": "$14.95/mo",
        "cancel_hint": "Account Details → Cancel membership",
    },
    {
        "slug": "paramount-plus",
        "name": "Paramount+",
        "domains": ["paramountplus.com"],
        "login_url": "https://www.paramountplus.com/account/signin/",
        "account_url": "https://www.paramountplus.com/account/",
        "est_cost": "$11.99/mo",
        "cancel_hint": "Account → Cancel Subscription",
    },
    {
        "slug": "peacock",
        "name": "Peacock",
        "domains": ["peacocktv.com"],
        "login_url": "https://www.peacocktv.com/signin",
        "account_url": "https://www.peacocktv.com/account/plans",
        "est_cost": "$7.99/mo",
        "cancel_hint": "Account → Plans & Payment → Cancel plan",
    },
    {
        "slug": "patreon",
        "name": "Patreon",
        "domains": ["patreon.com"],
        "login_url": "https://www.patreon.com/login",
        "account_url": "https://www.patreon.com/settings/memberships",
        "est_cost": None,
        "cancel_hint": "Settings → Memberships → Edit → Cancel",
    },
]

_BY_DOMAIN = {}
for _entry in SUBSCRIPTION_CATALOG:
    for _d in _entry["domains"]:
        _BY_DOMAIN[_d.lower()] = _entry


def match_host(host):
    """Return the catalog entry for a hostname, or None.

    Matches the host exactly or as a subdomain of a known billing domain
    (e.g. ``accounts.spotify.com`` -> Spotify).
    """
    host = (host or "").lower().strip()
    if not host:
        return None
    if host.startswith("www."):
        host = host[4:]
    if host in _BY_DOMAIN:
        return _BY_DOMAIN[host]
    for domain, entry in _BY_DOMAIN.items():
        if host == domain or host.endswith("." + domain):
            return entry
    return None


# Generic words inside service names that must NOT, on their own, match a
# merchant — they are too common and would cause false positives in receipts.
_NAME_STOPWORDS = {
    "premium", "plus", "one", "music", "video", "creative", "cloud",
    "workspace", "the", "and", "tv", "hbo", "icloud", "office", "365",
}


def _name_keywords(entry):
    """Distinctive lowercase keywords for an entry's name/slug (>=4 chars)."""
    words = set()
    name = entry["name"].split("(")[0]
    for token in re.split(r"[^a-z0-9]+", name.lower()):
        if len(token) >= 4 and token not in _NAME_STOPWORDS:
            words.add(token)
    for token in entry["slug"].split("-"):
        if len(token) >= 4 and token not in _NAME_STOPWORDS:
            words.add(token)
    return words


_NAME_KEYWORDS = {e["slug"]: _name_keywords(e) for e in SUBSCRIPTION_CATALOG}


def match_merchant(from_addr=None, text=None):
    """Best-effort match of a billing email to a catalog service.

    Tries the strongest signal first — the sender's domain (e.g. a receipt from
    ``billing@netflix.com`` maps to Netflix) — then falls back to a distinctive
    service-name keyword appearing as a whole word in ``text`` (subject/body).

    Returns the catalog entry or None. A None is honest: the email looks like a
    receipt but is not for a service MyDude recognises, so the user can still
    add it by hand.
    """
    # 1) Sender domain.
    addr = (from_addr or "").strip().lower()
    if "@" in addr:
        domain = addr.rsplit("@", 1)[-1].strip()
        entry = match_host(domain)
        if entry:
            return entry

    # 2) Distinctive name keyword as a whole word in the email text.
    blob = (text or "").lower()
    if blob:
        for entry in SUBSCRIPTION_CATALOG:
            for kw in _NAME_KEYWORDS.get(entry["slug"], ()):
                if re.search(r"\b%s\b" % re.escape(kw), blob):
                    return entry
    return None


def all_services():
    """All catalog entries (for manual-add suggestions in the UI)."""
    return list(SUBSCRIPTION_CATALOG)


def get_service(slug):
    for entry in SUBSCRIPTION_CATALOG:
        if entry["slug"] == (slug or "").lower():
            return entry
    return None
