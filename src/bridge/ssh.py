"""SSH bridge to the user's machine (e.g. their Mac).

Runs whitelisted commands and reads local artifacts (browser history, recent
verification codes) over SSH. Connection details come from the credential vault
(synced to env vars); paramiko is imported lazily so the app boots with this
capability disabled and the vault empty.

This module performs the *transport*. The allow-list / destructive-command
policy is enforced upstream in src/swarm/policy.py before any command reaches
``run_command`` — internal helpers (history/code) build their own trusted,
read-only commands.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from src.providers.secrets import get_secret, get_env

# Most one-time verification codes are 4-8 digits, optionally grouped.
_CODE_RE = re.compile(r"\b(\d{3}[\s-]?\d{3}|\d{4,8})\b")

OUTPUT_LIMIT = 8000


class SSHBridgeError(RuntimeError):
    """Raised for connection/auth/config problems talking to the bridge host."""


@dataclass
class SSHConfig:
    host: Optional[str]
    port: int
    user: Optional[str]
    password: Optional[str]
    private_key: Optional[str]
    key_passphrase: Optional[str]

    @property
    def configured(self) -> bool:
        return bool(self.host and self.user and (self.password or self.private_key))


def load_ssh_config() -> SSHConfig:
    """Read SSH connection details from the environment (vault-synced)."""
    port_raw = get_env("SSH_PORT", "22") or "22"
    try:
        port = int(port_raw)
    except ValueError:
        port = 22
    return SSHConfig(
        host=get_secret("SSH_HOST") or get_env("SSH_HOST"),
        port=port,
        user=get_secret("SSH_USER") or get_env("SSH_USER"),
        password=get_secret("SSH_PASSWORD"),
        private_key=get_secret("SSH_PRIVATE_KEY"),
        key_passphrase=get_secret("SSH_KEY_PASSPHRASE"),
    )


def _load_pkey(private_key: str, passphrase: Optional[str]):
    import paramiko

    last_err = None
    for loader in (
        getattr(paramiko, "Ed25519Key", None),
        getattr(paramiko, "RSAKey", None),
        getattr(paramiko, "ECDSAKey", None),
    ):
        if loader is None:
            continue
        try:
            return loader.from_private_key(io.StringIO(private_key), password=passphrase or None)
        except Exception as e:  # try the next key type
            last_err = e
    raise SSHBridgeError("Could not parse SSH_PRIVATE_KEY: %s" % (last_err or "unknown format"))


class SSHBridge:
    def __init__(self, config: Optional[SSHConfig] = None):
        self.config = config or load_ssh_config()

    def available(self) -> bool:
        return self.config.configured

    def _connect(self):
        import paramiko

        cfg = self.config
        if not cfg.configured:
            raise SSHBridgeError(
                "SSH bridge is not configured. Add SSH_HOST, SSH_USER and either "
                "SSH_PRIVATE_KEY or SSH_PASSWORD in the vault."
            )
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kwargs = {
            "hostname": cfg.host,
            "port": cfg.port,
            "username": cfg.user,
            "timeout": 15,
            "allow_agent": False,
            "look_for_keys": False,
        }
        if cfg.private_key:
            kwargs["pkey"] = _load_pkey(cfg.private_key, cfg.key_passphrase)
        else:
            kwargs["password"] = cfg.password
        try:
            client.connect(**kwargs)
        except Exception as e:
            raise SSHBridgeError("SSH connection failed: %s: %s" % (type(e).__name__, e))
        return client

    def _exec(self, command: str, timeout: int = 30) -> Tuple[int, str]:
        client = self._connect()
        try:
            stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
            out = stdout.read().decode("utf-8", "replace")
            err = stderr.read().decode("utf-8", "replace")
            rc = stdout.channel.recv_exit_status()
            combined = (out + ("\n" + err if err else "")).strip()
            return rc, combined[:OUTPUT_LIMIT]
        finally:
            try:
                client.close()
            except Exception:
                pass

    # -- public capability surface -------------------------------------------
    def run_command(self, command: str, timeout: int = 30) -> str:
        """Run an already policy-approved command and return its output."""
        rc, out = self._exec(command, timeout=timeout)
        if rc != 0 and not out:
            return "(command exited with status %d, no output)" % rc
        return out or "(no output)"

    def read_browser_history(self, limit: int = 20, browser: str = "chrome") -> str:
        """Read recent browser history over SSH.

        Copies the (possibly locked) history DB to a temp file and queries it
        with the macOS-bundled ``sqlite3``. Read-only.
        """
        limit = max(1, min(int(limit or 20), 200))
        if browser == "safari":
            src = "$HOME/Library/Safari/History.db"
            query = (
                "SELECT i.url, COALESCE(v.title,''), "
                "datetime(v.visit_time+978307200,'unixepoch','localtime') "
                "FROM history_visits v JOIN history_items i ON i.id=v.history_item "
                "ORDER BY v.visit_time DESC LIMIT %d" % limit
            )
        else:
            src = "$HOME/Library/Application Support/Google/Chrome/Default/History"
            query = (
                "SELECT url, title, "
                "datetime(last_visit_time/1000000-11644473600,'unixepoch','localtime') "
                "FROM urls ORDER BY last_visit_time DESC LIMIT %d" % limit
            )
        tmp = "/tmp/mydude_hist_%d.db" % limit
        command = (
            'cp "%s" "%s" 2>/dev/null && sqlite3 -separator " | " "%s" "%s"; rm -f "%s"'
            % (src, tmp, tmp, query, tmp)
        )
        rc, out = self._exec(command, timeout=30)
        if not out:
            return (
                "No history returned. The %s history database may not exist on the "
                "bridge host, or sqlite3 is unavailable." % browser
            )
        return out

    def fetch_recent_code(self, within_minutes: int = 10) -> str:
        """Read recent iMessage/SMS texts and extract a likely verification code.

        Reads the macOS Messages database (read-only) and scans the most recent
        messages for a one-time code.
        """
        within_minutes = max(1, min(int(within_minutes or 10), 120))
        db = "$HOME/Library/Messages/chat.db"
        tmp = "/tmp/mydude_msgs.db"
        # Apple stores message.date as ns since 2001-01-01. Pull the latest few
        # incoming messages; code extraction happens here, not on the host.
        query = (
            "SELECT text FROM message WHERE is_from_me=0 AND text IS NOT NULL "
            "ORDER BY date DESC LIMIT 15"
        )
        command = (
            'cp "%s" "%s" 2>/dev/null && sqlite3 "%s" "%s"; rm -f "%s"'
            % (db, tmp, tmp, query, tmp)
        )
        rc, out = self._exec(command, timeout=30)
        if not out:
            return (
                "No recent messages were readable. Ensure the bridge host grants "
                "Full Disk Access to the SSH session so Messages can be read."
            )
        codes = self.extract_codes(out)
        if not codes:
            return "Read %d recent message(s) but found no verification code." % len(
                [l for l in out.splitlines() if l.strip()]
            )
        return "Most recent verification code: %s\n(candidates: %s)" % (
            codes[0], ", ".join(codes[:5])
        )

    @staticmethod
    def extract_codes(text: str) -> List[str]:
        """Pull plausible verification codes from message text, newest first."""
        found: List[str] = []
        for line in text.splitlines():
            for m in _CODE_RE.findall(line):
                normalized = re.sub(r"[\s-]", "", m)
                if 4 <= len(normalized) <= 8 and normalized not in found:
                    found.append(normalized)
        return found
