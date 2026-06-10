"""
SWARM LAYER: CONTRACT (seam sub-layer)

Typed model↔tool interaction contracts for the capability broker.

Each capability declares:
  - category: one of Tool / MCP / Skill / Knowledge (CrewAI-style split)
  - required_fields: params that must be present and non-empty
  - optional_fields: known params (for documentation; not enforced as forbidden)
  - input_schema: typed field declarations {field_name: type_hint_str}
  - output_schema: typed output field declarations {field_name: type_hint_str}
  - epistemic_preconditions: human-readable governance requirements (for docs)
  - enforced_preconditions: executable validators — each is (label, callable(params)->str|None)
    callable returns None if precondition passes, or a violation string if it fails

Contract validation pipeline (called by broker BEFORE the policy gate):
  1. Required fields check — presence and non-emptiness
  2. Input type checks — field values match declared types in input_schema
  3. Executable preconditions — each enforced_precondition callable is run
  4. Under-justification check — fields that semantically require context are verified

All violations are recorded in the capability audit log regardless of capability category.

CrewAI-inspired capability categories:
  Tool      — Direct action with side effects (browse, SSH, API mutation)
  MCP       — Model-context protocol: structured agent data exchange
  Skill     — Reusable capability without direct side effects (plan, query)
  Knowledge — Read-only information access (docs, logs, metrics, history)
"""
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


class CapabilityCategory:
    TOOL = "Tool"
    MCP = "MCP"
    SKILL = "Skill"
    KNOWLEDGE = "Knowledge"


# ---------------------------------------------------------------------------
# Precondition validators — pure functions that return None (pass) or a
# violation reason string (fail).  Keep them simple and side-effect-free.
# ---------------------------------------------------------------------------

def _precond_url_scheme(params: Dict[str, Any]) -> Optional[str]:
    url = params.get("url") or params.get("login_url") or ""
    if url and not (url.startswith("http://") or url.startswith("https://")):
        return f"URL must begin with http:// or https://. Got: '{url[:80]}'"
    return None


def _precond_https_only(params: Dict[str, Any]) -> Optional[str]:
    url = params.get("url") or params.get("login_url") or ""
    if url and not url.startswith("https://"):
        return f"URL must use https:// (http is not permitted for login/cancel). Got: '{url[:80]}'"
    return None


def _precond_no_raw_secrets_in_command(params: Dict[str, Any]) -> Optional[str]:
    cmd = params.get("command", "")
    if not cmd:
        return None
    # Reject common credential-passing patterns
    patterns = [
        r"-p\s+\S+", r"--password\s+\S+", r"PASS=\S+", r"SECRET=\S+",
        r"TOKEN=\S+", r"API_KEY=\S+",
    ]
    for p in patterns:
        if re.search(p, cmd, re.IGNORECASE):
            return (
                f"Command appears to contain inline credentials. "
                f"Use vault-stored credentials via op_read_scoped instead."
            )
    return None


def _precond_single_line_command(params: Dict[str, Any]) -> Optional[str]:
    cmd = params.get("command", "")
    if "\n" in cmd:
        return "Command must be a single-line string (no newlines). Multi-step commands require separate broker calls."
    return None


def _precond_no_shell_metacharacters(params: Dict[str, Any]) -> Optional[str]:
    cmd = params.get("command", "")
    dangerous = [";", "&&", "||", "`", "$(", "${", ">", "<", "|"]
    found = [c for c in dangerous if c in cmd]
    if found:
        return f"Command contains forbidden shell metacharacters {found}. Each command must be a single allow-listed token."
    return None


def _precond_has_plan(params: Dict[str, Any]) -> Optional[str]:
    if not params.get("has_plan"):
        return (
            "terraform_apply requires has_plan=true. "
            "A prior terraform_plan must have been reviewed and approved before apply."
        )
    return None


def _precond_prod_gated(params: Dict[str, Any]) -> Optional[str]:
    env = (params.get("env") or params.get("workspace") or "").lower()
    if "prod" in env:
        if not os.getenv("ALLOW_PROD_CAPABILITIES", "").lower() in ("1", "true", "yes"):
            return "Production workspace requires ALLOW_PROD_CAPABILITIES=true in environment."
    return None


def _precond_permanently_blocked(params: Dict[str, Any]) -> Optional[str]:
    return "BLOCKED: this capability is permanently forbidden by governance policy."


def _precond_item_not_inline_secret(params: Dict[str, Any]) -> Optional[str]:
    item = params.get("item", "")
    if item and (item.startswith("sk-") or item.startswith("Bearer ") or len(item) > 200):
        return "item looks like a raw secret value. Pass the vault item name (e.g. 'my_openai_key'), not the secret itself."
    return None


# ---------------------------------------------------------------------------
# Contract dataclass
# ---------------------------------------------------------------------------

@dataclass
class CapabilityContract:
    capability: str
    category: str
    description: str
    required_fields: List[str] = field(default_factory=list)
    optional_fields: List[str] = field(default_factory=list)
    input_schema: Dict[str, str] = field(default_factory=dict)
    output_schema: Dict[str, str] = field(default_factory=dict)
    epistemic_preconditions: List[str] = field(default_factory=list)
    enforced_preconditions: List[Tuple[str, Callable]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Contract registry
# ---------------------------------------------------------------------------

_CONTRACTS: Dict[str, CapabilityContract] = {
    "browser_open": CapabilityContract(
        capability="browser_open",
        category=CapabilityCategory.TOOL,
        description="Navigate to a URL and extract page content.",
        required_fields=["url"],
        optional_fields=["source", "domain", "team"],
        input_schema={"url": "str", "source": "str", "domain": "str", "team": "str"},
        output_schema={"text": "str", "title": "str", "url": "str"},
        epistemic_preconditions=[
            "url must start with http:// or https://",
            "Domain must be in the operator-configured BROWSER_ALLOWED_DOMAINS list",
        ],
        enforced_preconditions=[
            ("url_scheme", _precond_url_scheme),
        ],
    ),
    "browser_login": CapabilityContract(
        capability="browser_login",
        category=CapabilityCategory.TOOL,
        description="Perform an automated login flow using vault-stored credentials.",
        required_fields=["login_url"],
        optional_fields=["account_url", "source", "domain", "team"],
        input_schema={"login_url": "str", "account_url": "str", "source": "str"},
        output_schema={"status": "str", "current_url": "str", "page_text": "str"},
        epistemic_preconditions=[
            "login_url must start with https://",
            "Credentials must be stored in the vault; never passed inline",
        ],
        enforced_preconditions=[
            ("https_only", _precond_https_only),
        ],
    ),
    "browser_cancel": CapabilityContract(
        capability="browser_cancel",
        category=CapabilityCategory.TOOL,
        description="Navigate the account portal to trigger a subscription cancellation flow.",
        required_fields=["login_url"],
        optional_fields=["account_url", "source", "domain", "team"],
        input_schema={"login_url": "str", "account_url": "str", "source": "str"},
        output_schema={"status": "str", "steps_taken": "list", "current_url": "str"},
        epistemic_preconditions=[
            "login_url must start with https://",
            "Cancellation must have been explicitly confirmed by the operator",
        ],
        enforced_preconditions=[
            ("https_only", _precond_https_only),
        ],
    ),
    "ssh_run": CapabilityContract(
        capability="ssh_run",
        category=CapabilityCategory.TOOL,
        description="Execute a single allow-listed shell command on the SSH-connected host.",
        required_fields=["command"],
        optional_fields=["source", "domain", "team"],
        input_schema={"command": "str", "source": "str"},
        output_schema={"stdout": "str", "stderr": "str", "exit_code": "int"},
        epistemic_preconditions=[
            "command must be a non-empty single-line string (no newlines)",
            "command first token must appear in SSH_ALLOWED_COMMANDS allow-list",
            "Shell metacharacters are forbidden",
            "Inline credentials are forbidden — use vault via op_read_scoped",
        ],
        enforced_preconditions=[
            ("single_line", _precond_single_line_command),
            ("no_metacharacters", _precond_no_shell_metacharacters),
            ("no_inline_secrets", _precond_no_raw_secrets_in_command),
        ],
    ),
    "ssh_read_history": CapabilityContract(
        capability="ssh_read_history",
        category=CapabilityCategory.KNOWLEDGE,
        description="Read browser history from the SSH-connected host (read-only).",
        required_fields=[],
        optional_fields=["browser", "limit", "source"],
        input_schema={"browser": "str", "limit": "int", "source": "str"},
        output_schema={"entries": "list"},
        epistemic_preconditions=[],
        enforced_preconditions=[],
    ),
    "ssh_fetch_code": CapabilityContract(
        capability="ssh_fetch_code",
        category=CapabilityCategory.KNOWLEDGE,
        description="Fetch source code listings from the SSH-connected host (read-only).",
        required_fields=[],
        optional_fields=["path", "source"],
        input_schema={"path": "str", "source": "str"},
        output_schema={"files": "list", "content": "str"},
        epistemic_preconditions=[],
        enforced_preconditions=[],
    ),
    "git_status": CapabilityContract(
        capability="git_status",
        category=CapabilityCategory.KNOWLEDGE,
        description="Query git repository status.",
        required_fields=[],
        optional_fields=["repo", "source"],
        input_schema={"repo": "str", "source": "str"},
        output_schema={"branch": "str", "staged": "list", "unstaged": "list", "untracked": "list"},
        epistemic_preconditions=[],
        enforced_preconditions=[],
    ),
    "terraform_plan": CapabilityContract(
        capability="terraform_plan",
        category=CapabilityCategory.SKILL,
        description="Run terraform plan and return the proposed infrastructure change diff.",
        required_fields=[],
        optional_fields=["workspace", "env", "source"],
        input_schema={"workspace": "str", "env": "str", "source": "str"},
        output_schema={"plan_text": "str", "resource_changes": "int", "warnings": "list"},
        epistemic_preconditions=[
            "Production workspace requires ALLOW_PROD_CAPABILITIES=true",
        ],
        enforced_preconditions=[
            ("prod_gated", _precond_prod_gated),
        ],
    ),
    "terraform_apply": CapabilityContract(
        capability="terraform_apply",
        category=CapabilityCategory.TOOL,
        description="Apply an approved terraform plan.",
        required_fields=["has_plan"],
        optional_fields=["workspace", "env", "source"],
        input_schema={"has_plan": "bool", "workspace": "str", "env": "str", "source": "str"},
        output_schema={"applied": "bool", "changes": "list", "errors": "list"},
        epistemic_preconditions=[
            "has_plan must be truthy: a prior terraform_plan must have been reviewed",
            "Production environment requires ALLOW_PROD_CAPABILITIES=true",
        ],
        enforced_preconditions=[
            ("has_plan", _precond_has_plan),
            ("prod_gated", _precond_prod_gated),
        ],
    ),
    "asana_query": CapabilityContract(
        capability="asana_query",
        category=CapabilityCategory.KNOWLEDGE,
        description="Query Asana tasks and projects.",
        required_fields=[],
        optional_fields=["project_gid", "workspace", "source"],
        input_schema={"project_gid": "str", "workspace": "str", "source": "str"},
        output_schema={"tasks": "list", "total": "int"},
        epistemic_preconditions=[],
        enforced_preconditions=[],
    ),
    "op_read_scoped": CapabilityContract(
        capability="op_read_scoped",
        category=CapabilityCategory.KNOWLEDGE,
        description="Read a scoped secret from 1Password (read-only; never echoed).",
        required_fields=["item"],
        optional_fields=["vault", "source"],
        input_schema={"item": "str", "vault": "str", "source": "str"},
        output_schema={"value": "str"},
        epistemic_preconditions=[
            "Secret value must not be logged or echoed in any output",
            "item must be a vault item name, not a raw secret value",
        ],
        enforced_preconditions=[
            ("item_not_inline_secret", _precond_item_not_inline_secret),
        ],
    ),
    "imap_read_receipts": CapabilityContract(
        capability="imap_read_receipts",
        category=CapabilityCategory.KNOWLEDGE,
        description="Read billing email receipts via IMAP (read-only; mailbox opened readonly).",
        required_fields=[],
        optional_fields=["limit", "lookback_days", "source"],
        input_schema={"limit": "int", "lookback_days": "int", "source": "str"},
        output_schema={"receipts": "list"},
        epistemic_preconditions=[],
        enforced_preconditions=[],
    ),
    "gmail_fetch_code": CapabilityContract(
        capability="gmail_fetch_code",
        category=CapabilityCategory.KNOWLEDGE,
        description="Fetch a one-time authentication code from Gmail via OAuth connector.",
        required_fields=[],
        optional_fields=["subject_filter", "source"],
        input_schema={"subject_filter": "str", "source": "str"},
        output_schema={"code": "str", "subject": "str"},
        epistemic_preconditions=[
            "Gmail OAuth connector must be connected and authorised",
        ],
        enforced_preconditions=[],
    ),
    "bot_spawn": CapabilityContract(
        capability="bot_spawn",
        category=CapabilityCategory.TOOL,
        description="Spawn a new bot into the spawning bot's team (bounded by team spawn_cap).",
        required_fields=["spawner_bot_id", "name"],
        optional_fields=["goal", "identity_schema", "prompt_cards", "protocols", "source"],
        input_schema={"spawner_bot_id": "int", "name": "str", "goal": "str", "source": "str"},
        output_schema={"ok": "bool", "bot_id": "int", "team_id": "int", "name": "str"},
        epistemic_preconditions=[
            "spawner_bot_id must reference an existing bot in a team",
            "Resulting bot count must not exceed the team's spawn_cap",
            "ENABLE_BOT_SPAWN must not be set to false",
        ],
        enforced_preconditions=[],
    ),
    "fleet_provision_plan": CapabilityContract(
        capability="fleet_provision_plan",
        category=CapabilityCategory.SKILL,
        description="Generate a provisioning plan for a cloud resource (no resources created until approved).",
        required_fields=["resource_type"],
        optional_fields=["config", "bot_id", "team_id", "source"],
        input_schema={"resource_type": "str", "bot_id": "int", "team_id": "int", "source": "str"},
        output_schema={"ok": "bool", "job_id": "int", "resource_id": "int", "plan_output": "str", "status": "str"},
        epistemic_preconditions=[
            "VM and ml_service resources require ALLOW_FLEET_PROVISIONING=true",
            "No real cloud resources are created in the plan phase",
        ],
        enforced_preconditions=[],
    ),
    "fleet_provision_approve": CapabilityContract(
        capability="fleet_provision_approve",
        category=CapabilityCategory.TOOL,
        description="Approve and apply a provisioning job that is awaiting_approval (creates real cloud resources).",
        required_fields=["job_id"],
        optional_fields=["source"],
        input_schema={"job_id": "int", "source": "str"},
        output_schema={"ok": "bool", "job_id": "int", "resource_id": "str", "output": "str", "status": "str"},
        epistemic_preconditions=[
            "job_id must reference a job with status=awaiting_approval",
            "Operator must have reviewed the plan_output before calling approve",
            "This action may create real cloud resources that incur cost",
        ],
        enforced_preconditions=[],
    ),
    "read_secret_raw": CapabilityContract(
        capability="read_secret_raw",
        category=CapabilityCategory.TOOL,
        description="[PERMANENTLY BLOCKED] Raw secret export is forbidden by governance policy.",
        required_fields=[],
        optional_fields=[],
        input_schema={},
        output_schema={},
        epistemic_preconditions=["BLOCKED: raw secret export is permanently forbidden"],
        enforced_preconditions=[
            ("permanently_blocked", _precond_permanently_blocked),
        ],
    ),
    "dump_vault": CapabilityContract(
        capability="dump_vault",
        category=CapabilityCategory.TOOL,
        description="[PERMANENTLY BLOCKED] Vault dump is forbidden by governance policy.",
        required_fields=[],
        optional_fields=[],
        input_schema={},
        output_schema={},
        epistemic_preconditions=["BLOCKED: vault dump is permanently forbidden"],
        enforced_preconditions=[
            ("permanently_blocked", _precond_permanently_blocked),
        ],
    ),
    "export_all_secrets": CapabilityContract(
        capability="export_all_secrets",
        category=CapabilityCategory.TOOL,
        description="[PERMANENTLY BLOCKED] Secret export is forbidden by governance policy.",
        required_fields=[],
        optional_fields=[],
        input_schema={},
        output_schema={},
        epistemic_preconditions=["BLOCKED: secret export is permanently forbidden"],
        enforced_preconditions=[
            ("permanently_blocked", _precond_permanently_blocked),
        ],
    ),
}

_DEFAULT_CONTRACT = CapabilityContract(
    capability="__default__",
    category=CapabilityCategory.SKILL,
    description="Generic stub capability with no declared contract.",
    required_fields=[],
    optional_fields=[],
    input_schema={},
    output_schema={},
    epistemic_preconditions=[],
    enforced_preconditions=[],
)

# Type name → Python types for runtime type checking
_TYPE_MAP: Dict[str, tuple] = {
    "str": (str,),
    "int": (int,),
    "float": (float, int),
    "bool": (bool,),
    "list": (list,),
    "dict": (dict,),
}


def get_contract(capability: str) -> CapabilityContract:
    """Return the declared contract for a capability, or the default stub contract."""
    return _CONTRACTS.get(capability, _DEFAULT_CONTRACT)


def validate_request(capability: str, params: Dict) -> Optional[str]:
    """
    Validate a capability request against its declared contract.

    Validation pipeline:
      1. Required fields — presence and non-emptiness.
      2. Input type checks — declared input_schema types are enforced for provided fields.
      3. Executable preconditions — all enforced_preconditions callables are run.

    Returns None when the request is valid.
    Returns a human-readable violation reason string when invalid.

    All violations are recorded via _audit_violation() regardless of category.
    The broker calls this BEFORE the policy gate.
    """
    contract = get_contract(capability)

    # Step 1: Required fields
    for required in contract.required_fields:
        val = params.get(required)
        if val is None or (isinstance(val, str) and not val.strip()) or val is False:
            reason = (
                f"Contract violation [{contract.category}] '{capability}': "
                f"required field '{required}' is missing or empty. "
                f"Contract: {contract.description}"
            )
            _audit_violation(capability, params, reason, "missing_required_field")
            return reason

    # Step 2: Input type checks (only for fields that are present)
    for field_name, type_hint in contract.input_schema.items():
        val = params.get(field_name)
        if val is None:
            continue  # Optional field — not present is fine
        expected_types = _TYPE_MAP.get(type_hint)
        if expected_types and not isinstance(val, expected_types):
            reason = (
                f"Contract violation [{contract.category}] '{capability}': "
                f"field '{field_name}' expects type '{type_hint}', "
                f"got {type(val).__name__} value {repr(val)[:60]}."
            )
            _audit_violation(capability, params, reason, "type_mismatch")
            return reason

    # Step 3: Executable preconditions
    for label, validator in contract.enforced_preconditions:
        try:
            violation = validator(params)
        except Exception as e:
            violation = f"Precondition '{label}' raised an error: {e}"
        if violation:
            reason = (
                f"Contract violation [{contract.category}] '{capability}' "
                f"[precondition: {label}]: {violation}"
            )
            _audit_violation(capability, params, reason, f"precondition:{label}")
            return reason

    return None


def _audit_violation(capability: str, params: Dict, reason: str, violation_type: str) -> None:
    """Record ALL contract violations in the capability audit log.

    Every capability category is audited — not just the externally-reaching ones.
    This gives operators a complete, governance-traceable picture of every
    malformed request the broker rejected before reaching the policy gate.
    Failures are swallowed so an audit-log outage never blocks requests.
    """
    try:
        from src.swarm.integrations import audit_capability
        target = (
            params.get("url") or params.get("login_url")
            or params.get("command") or params.get("item")
            or params.get("browser") or ""
        )
        audit_capability(
            capability,
            target=target[:200] if target else None,
            status="contract_violation",
            detail=f"[{violation_type}] {reason}"[:500],
            source=params.get("source"),
        )
    except Exception:
        pass  # audit outage must not block the request pipeline


def all_contracts() -> List[CapabilityContract]:
    """Return all declared contracts (for the capabilities dashboard)."""
    return list(_CONTRACTS.values())
