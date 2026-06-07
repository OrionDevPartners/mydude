import os
import shlex
from dataclasses import dataclass
from typing import Any, Dict, List
from urllib.parse import urlparse

_TRUTHY = ("1", "true", "yes", "on")

# Default-deny SSH allow-list: only these (read-only / diagnostic) commands may
# run on the bridge host unless the operator widens SSH_ALLOWED_COMMANDS.
DEFAULT_SSH_ALLOWED = [
    "echo", "whoami", "hostname", "uname", "sw_vers", "uptime", "date",
    "pwd", "ls", "cat", "head", "tail", "sqlite3", "defaults", "system_profiler",
]

# Hard-blocked destructive substrings — rejected regardless of the allow-list.
DESTRUCTIVE_PATTERNS = [
    "rm ", "rm-", "rmdir", "mkfs", "dd ", "shutdown", "reboot", "halt",
    "sudo", "su ", "chmod", "chown", "kill", "pkill", "killall", "launchctl",
    "diskutil", "format", "> /", ">/", ":(){", "fork", "curl", "wget",
    "nc ", "ncat", "scp", "ssh ", "passwd", "dscl", "crontab",
]


@dataclass
class PolicyDecision:
    allowed: bool
    reason: str


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").lower() in _TRUTHY


def _env_list(name: str, default: List[str]) -> List[str]:
    raw = os.environ.get(name, "")
    if not raw.strip():
        return list(default)
    return [x.strip().lower() for x in raw.split(",") if x.strip()]


def _prod_explicitly_allowed() -> bool:
    """Production-affecting capabilities are blocked by default.

    They are only permitted when an operator sets ALLOW_PROD_CAPABILITIES to a
    truthy value. This is an explicit, deliberate opt-in: it is NEVER inferred
    from REPLIT_DEPLOYMENT, so simply publishing the app does not unlock
    production actions.
    """
    return _env_flag("ALLOW_PROD_CAPABILITIES")


class PolicyEngine:
    def __init__(self):
        self.allow_prod = _prod_explicitly_allowed()

    # -- capability gates -----------------------------------------------------
    def is_host_allowed(self, host: str) -> bool:
        """Single source of truth for the browse allow-list. Used both before
        navigation (input URL) and after (final/redirected URL)."""
        host = (host or "").lower()
        if not host:
            return False
        allowed = _env_list("BROWSER_ALLOWED_DOMAINS", ["example.com"])
        return any(host == d or host.endswith("." + d) for d in allowed)

    def _evaluate_browser(self, params: Dict[str, Any]) -> PolicyDecision:
        if not _env_flag("ENABLE_BROWSER_CAPABILITY"):
            return PolicyDecision(
                False,
                "Browser capability is disabled. Set ENABLE_BROWSER_CAPABILITY=true to enable it.",
            )
        url = (params.get("url") or "").strip()
        if not url:
            return PolicyDecision(False, "A URL is required.")
        host = (urlparse(url).hostname or "").lower()
        if not host:
            return PolicyDecision(False, "Could not parse a host from the URL.")
        if not self.is_host_allowed(host):
            return PolicyDecision(
                False,
                "Domain '%s' is not in the browse allow-list. Add it to "
                "BROWSER_ALLOWED_DOMAINS to permit it." % host,
            )
        return PolicyDecision(True, "Allowed by policy.")

    def _evaluate_browser_interactive(self, params: Dict[str, Any]) -> PolicyDecision:
        """Gate for the login/cancel flows.

        Same enable flag and allow-list as browse, but applied to *every* URL the
        flow will touch (login + account). The runtime per-hop allow_host check in
        the browser layer is the enforcement backstop; this is the up-front gate.
        """
        if not _env_flag("ENABLE_BROWSER_CAPABILITY"):
            return PolicyDecision(
                False,
                "Browser capability is disabled. Set ENABLE_BROWSER_CAPABILITY=true to enable it.",
            )
        urls = [u for u in (params.get("login_url"), params.get("account_url")) if (u or "").strip()]
        if not urls:
            return PolicyDecision(False, "A login URL is required.")
        for u in urls:
            host = (urlparse(u).hostname or "").lower()
            if not host:
                return PolicyDecision(False, "Could not parse a host from '%s'." % u)
            if not self.is_host_allowed(host):
                return PolicyDecision(
                    False,
                    "Domain '%s' is not in the browse allow-list. Add it to "
                    "BROWSER_ALLOWED_DOMAINS to permit it." % host,
                )
        return PolicyDecision(True, "Allowed by policy.")

    def _evaluate_ssh(self, capability: str, params: Dict[str, Any]) -> PolicyDecision:
        if not _env_flag("ENABLE_SSH_CAPABILITY"):
            return PolicyDecision(
                False,
                "SSH capability is disabled. Set ENABLE_SSH_CAPABILITY=true to enable it.",
            )
        # History/code reads run internally-built read-only commands.
        if capability in ("ssh_read_history", "ssh_fetch_code"):
            return PolicyDecision(True, "Allowed by policy.")

        command = (params.get("command") or "").strip()
        if not command:
            return PolicyDecision(False, "A command is required.")
        # Reject any newline/carriage-return outright: a single command line
        # only. This closes the `whoami\nrm -rf /` multi-line bypass.
        if "\n" in command or "\r" in command:
            return PolicyDecision(False, "Multi-line commands are not permitted.")
        low = command.lower()
        for pat in DESTRUCTIVE_PATTERNS:
            if pat in low:
                return PolicyDecision(
                    False, "Command blocked: contains destructive pattern '%s'." % pat.strip()
                )
        # Block every shell control / chaining / redirection / substitution /
        # backgrounding / globbing metacharacter. A whitelisted command runs on
        # its own, with literal arguments only — no shell tricks.
        FORBIDDEN_METACHARS = (
            "&&", "||", ";", "|", "&", "`", "$(", "${", "$",
            ">", "<", "*", "?", "~", "{", "}", "[", "]", "(", ")", "!", "#", "\\",
            "\t", "\x00",
        )
        for c in FORBIDDEN_METACHARS:
            if c in command:
                return PolicyDecision(
                    False,
                    "Shell metacharacter '%s' is not permitted (no chaining, "
                    "redirection, substitution, backgrounding, or globbing)." % c,
                )
        # Tokenize the way a shell would so the executable cannot be smuggled
        # past the allow-list (e.g. quoting). Reject anything unparseable.
        try:
            tokens = shlex.split(command)
        except ValueError:
            return PolicyDecision(False, "Command could not be parsed safely.")
        if not tokens:
            return PolicyDecision(False, "A command is required.")
        first = tokens[0].rsplit("/", 1)[-1].lower()
        allowed = _env_list("SSH_ALLOWED_COMMANDS", DEFAULT_SSH_ALLOWED)
        if first not in allowed:
            return PolicyDecision(
                False,
                "Command '%s' is not in the SSH allow-list. Add it to "
                "SSH_ALLOWED_COMMANDS to permit it." % first,
            )
        # Defense in depth for powerful allow-listed binaries: their arguments
        # must not coerce them into executing or writing. sqlite3/defaults can
        # otherwise run shell or mutate state even with read-only intent.
        arg_tokens = tokens[1:]
        joined_args = " ".join(arg_tokens).lower()
        RISKY_ARG_TOKENS = (
            "exec", "system", "shell", "write", "delete", "rename",
            "attach", "vacuum", "load_extension", "insert", "update",
            "drop", "create", "alter", "pragma",
        )
        if first == "sqlite3":
            # sqlite3 dot-commands (.shell, .import, .output, …) are tokens that
            # start with a dot; reject all of them. Filenames like places.sqlite
            # are not separate "." tokens, so they remain allowed.
            if any(t.startswith(".") for t in arg_tokens):
                return PolicyDecision(
                    False, "sqlite3 dot-commands are not permitted (read-only queries only)."
                )
            if any(f.startswith("-") and f.lower() in ("-cmd", "-init", "-a") for f in arg_tokens):
                return PolicyDecision(False, "That sqlite3 flag is not permitted.")
        if first in ("sqlite3", "defaults"):
            for bad in RISKY_ARG_TOKENS:
                if bad in joined_args:
                    return PolicyDecision(
                        False,
                        "Argument '%s' is not permitted for '%s' (read-only only)." % (bad, first),
                    )
        return PolicyDecision(True, "Allowed by policy.")

    def evaluate(self, capability: str, params: Dict[str, Any]) -> PolicyDecision:
        if capability in ("read_secret_raw", "dump_vault", "export_all_secrets"):
            return PolicyDecision(False, "Raw secret export is forbidden.")

        env = params.get("env")
        if env == "prod" and not self.allow_prod:
            return PolicyDecision(False, "Production actions are blocked by policy.")

        if capability == "terraform_apply" and not params.get("has_plan"):
            return PolicyDecision(False, "terraform_apply requires a prior terraform_plan.")

        if capability == "browser_open":
            return self._evaluate_browser(params)

        if capability in ("browser_login", "browser_cancel"):
            return self._evaluate_browser_interactive(params)

        if capability in ("ssh_run", "ssh_read_history", "ssh_fetch_code"):
            return self._evaluate_ssh(capability, params)

        if capability == "imap_read_receipts":
            return self._evaluate_email(params)

        return PolicyDecision(True, "Allowed by policy.")

    def _evaluate_email(self, params: Dict[str, Any]) -> PolicyDecision:
        """Gate for the read-only email-receipt scan.

        Disabled by default; the operator opts in with ENABLE_EMAIL_CAPABILITY.
        The IMAP read is internally-built and read-only (the mailbox is opened
        ``readonly=True``), so there is no command/URL allow-list to apply — the
        enable flag is the gate, mirroring ssh_read_history.
        """
        if not _env_flag("ENABLE_EMAIL_CAPABILITY"):
            return PolicyDecision(
                False,
                "Email capability is disabled. Set ENABLE_EMAIL_CAPABILITY=true to enable it.",
            )
        return PolicyDecision(True, "Allowed by policy.")
