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


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "")
    if not raw.strip():
        return default
    return raw.lower() in _TRUTHY


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

        if capability == "gmail_fetch_code":
            return self._evaluate_gmail(params)

        if capability == "bot_spawn":
            return self._evaluate_bot_spawn(params)

        if capability == "fleet_provision_plan":
            return self._evaluate_fleet_provision(params)

        if capability == "fleet_provision_approve":
            return self._evaluate_fleet_provision_approve(params)

        if capability == "calendly_book":
            return self._evaluate_calendly_book(params)

        if capability in ("telephony_place_call", "telephony_receive_call",
                          "telephony_turn", "voice_synthesize"):
            return self._evaluate_telephony(capability, params)

        if capability in ("azure_cosmos_read", "azure_pg_select",
                          "azure_deploy_status", "azure_aoai_complete",
                          "azure_deploy_plan", "azure_deploy_apply"):
            return self._evaluate_azure(capability, params)

        if capability == "memory_recall":
            return self._evaluate_memory(capability, params)

        return PolicyDecision(True, "Allowed by policy.")

    def _evaluate_telephony(self, capability: str, params: Dict[str, Any]) -> PolicyDecision:
        """Gate for the voice + telephony capabilities (Task #66).

        Enabled by default so voice/telephony is usable out of the box once a
        provider is connected; operators hard-disable ALL voice + call actions
        via ENABLE_TELEPHONY_CAPABILITY=false. Provider credential presence
        (Twilio / ElevenLabs connected) is enforced downstream and fails loud —
        never mocked. Outbound calls additionally require explicit production
        opt-in: a caller targeting production (env="prod") is gated by the
        ALLOW_PROD_CAPABILITIES check above (calls cost money / reach real PSTN).
        """
        if not _env_flag("ENABLE_TELEPHONY_CAPABILITY", default=True):
            return PolicyDecision(
                False,
                "Telephony capability is disabled. Set "
                "ENABLE_TELEPHONY_CAPABILITY=true to enable voice + calls.",
            )
        return PolicyDecision(True, "Allowed by policy.")

    def _evaluate_azure(self, capability: str, params: Dict[str, Any]) -> PolicyDecision:
        """Gate for the Azure MCP dev-accelerator capabilities (Task #186).

        The read / governed-completion / plan surface is enabled by default
        (usable once the MCP server is deployed inside the VNet) and is
        hard-disabled with ENABLE_AZURE_MCP=false. The billable APPLY phase is
        default-DENY: it additionally requires an explicit ALLOW_AZURE_DEPLOY=true
        opt-in. The cryptographic two-phase binding (plan token + exact plan-hash
        + confirm phrase) is verified downstream in the integration handler —
        that needs the signing secret — so here we enforce only the master
        enable flag and the destructive opt-in. Provider/credential presence is
        enforced at the data plane and fails loud (never mocked).
        """
        if not _env_flag("ENABLE_AZURE_MCP", default=True):
            return PolicyDecision(
                False,
                "Azure MCP capability is disabled. Set ENABLE_AZURE_MCP=true to enable it.",
            )
        if capability == "azure_deploy_apply" and not _env_flag("ALLOW_AZURE_DEPLOY"):
            return PolicyDecision(
                False,
                "Azure deploy apply is blocked by policy (billable, destructive, "
                "default-deny). Set ALLOW_AZURE_DEPLOY=true to enable the apply phase.",
            )
        return PolicyDecision(True, "Allowed by policy.")

    def _evaluate_memory(self, capability: str, params: Dict[str, Any]) -> PolicyDecision:
        """Gate for long-term memory recall exposed over MCP (Task #219).

        Read-only semantic recall is enabled by default (it never mutates memory
        and the integration layer filters out private digital-twin entries); the
        operator can hard-disable the MCP memory surface with ENABLE_MEMORY_MCP=
        false. Note this gates ONLY the explicit ``memory_recall`` capability —
        the additive write-back siphon that distills other interactions into
        memory lives in the MCP server and is governed there (governed output +
        sanitization), never as a brokered capability.
        """
        if not _env_flag("ENABLE_MEMORY_MCP", default=True):
            return PolicyDecision(
                False,
                "Memory MCP capability is disabled. Set ENABLE_MEMORY_MCP=true to enable it.",
            )
        return PolicyDecision(True, "Allowed by policy.")

    def _evaluate_calendly_book(self, params: Dict[str, Any]) -> PolicyDecision:
        """Gate for booking a meeting via Calendly from a sales conversation.

        Enabled by default so sales mode is usable out of the box; operators can
        hard-disable all outbound sales actions via ENABLE_SALES_CAPABILITY=false.
        Credential presence (Calendly connected) is enforced downstream and fails
        loud — never mocked.
        """
        if not _env_flag("ENABLE_SALES_CAPABILITY", default=True):
            return PolicyDecision(
                False,
                "Sales capability is disabled. Set ENABLE_SALES_CAPABILITY=true to enable it.",
            )
        return PolicyDecision(True, "Allowed by policy.")

    def _evaluate_bot_spawn(self, params: Dict[str, Any]) -> PolicyDecision:
        """Gate for bot spawning.

        Enabled by default so the fleet is usable out of the box; operators can
        hard-disable autonomous spawning via ENABLE_BOT_SPAWN=false.
        A non-zero spawn_cap is enforced separately in the spawner.
        """
        if not _env_flag("ENABLE_BOT_SPAWN", default=True):
            return PolicyDecision(
                False,
                "Bot spawning is disabled. Set ENABLE_BOT_SPAWN=true to enable it.",
            )
        return PolicyDecision(True, "Allowed by policy.")

    def _evaluate_fleet_provision(self, params: Dict[str, Any]) -> PolicyDecision:
        """Gate for the fleet_provision_plan capability (dry-run plan phase).

        Off by default for VM/ML — cloud resource creation has real cost/blast-radius.
        Operators opt in with ALLOW_FLEET_PROVISIONING=true.
        git_repo is allowed without the flag (no cloud cost, no persistent compute).
        The resource_type is recorded in the decision reason for auditability.
        """
        rtype = params.get("resource_type", "unknown")
        gated = {"vm", "ml_service"}
        if rtype in gated and not _env_flag("ALLOW_FLEET_PROVISIONING"):
            return PolicyDecision(
                False,
                f"Provisioning plan for '{rtype}' resources is blocked by policy. "
                "Set ALLOW_FLEET_PROVISIONING=true to enable real cloud provisioning.",
            )
        return PolicyDecision(True, f"Provisioning plan for '{rtype}' allowed by policy.")

    def _evaluate_fleet_provision_approve(self, params: Dict[str, Any]) -> PolicyDecision:
        """Gate for the fleet_provision_approve capability (actual resource creation).

        The approve phase only receives a job_id; we load the associated resource
        from the DB to determine the resource type, then apply the same gate as
        the plan phase.  This ensures the policy is re-enforced at apply time even
        if ALLOW_FLEET_PROVISIONING was toggled off after the plan was created.
        """
        job_id = params.get("job_id")
        if not job_id:
            return PolicyDecision(False, "fleet_provision_approve requires a job_id.")

        rtype = "unknown"
        try:
            from src.database import SessionLocal
            from src.models import ProvisioningJob, ProvisionedResource
            db = SessionLocal()
            try:
                job = db.query(ProvisioningJob).filter(ProvisioningJob.id == job_id).first()
                if job and job.resource_id:
                    res = db.query(ProvisionedResource).filter(
                        ProvisionedResource.id == job.resource_id
                    ).first()
                    if res:
                        rtype = res.resource_type
            finally:
                db.close()
        except Exception:
            pass

        gated = {"vm", "ml_service"}
        if rtype in gated and not _env_flag("ALLOW_FLEET_PROVISIONING"):
            return PolicyDecision(
                False,
                f"Approving provisioning of '{rtype}' resources is blocked by policy. "
                "Set ALLOW_FLEET_PROVISIONING=true to enable real cloud provisioning.",
            )
        return PolicyDecision(True, f"Provisioning approve for '{rtype}' (job {job_id}) allowed by policy.")

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

    def _evaluate_gmail(self, params: Dict[str, Any]) -> PolicyDecision:
        """Gate for reading an emailed one-time code via the Gmail connector.

        Unlike the IMAP receipt scan (which uses stored vault credentials and is
        off by default), Gmail access is granted through Replit's OAuth flow —
        an explicit, revocable user consent that *is* the gate. So this is
        allowed by default once connected; the connection presence is enforced
        downstream and every use is audited. An operator can still hard-disable
        it by setting ENABLE_GMAIL_CAPABILITY=false.
        """
        if not _env_flag("ENABLE_GMAIL_CAPABILITY", default=True):
            return PolicyDecision(
                False,
                "Gmail capability is disabled. Set ENABLE_GMAIL_CAPABILITY=true to enable it.",
            )
        return PolicyDecision(True, "Allowed by policy.")
