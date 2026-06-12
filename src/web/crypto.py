import os
import logging
from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)

# Whether ENCRYPTION_KEY was supplied via the environment (persists across
# restarts) or auto-generated for this process only. When it is auto-generated,
# every restart produces a fresh key and previously saved vault credentials
# (provider keys, Browserbase keys, SSH bridge config) become undecryptable.
_provided_key = os.environ.get("ENCRYPTION_KEY")
ENCRYPTION_KEY_PERSISTENT = bool(_provided_key)

if _provided_key:
    _key = _provided_key
else:
    _key = Fernet.generate_key().decode()
    os.environ["ENCRYPTION_KEY"] = _key
    logger.warning(
        "ENCRYPTION_KEY is not set — generated an EPHEMERAL encryption key for "
        "this process only. Credentials saved to the vault will become "
        "UNDECRYPTABLE after the next restart. Set ENCRYPTION_KEY as a "
        "persistent deployment secret to keep saved credentials across restarts."
    )

_fernet = Fernet(_key.encode() if isinstance(_key, str) else _key)


def encryption_key_is_persistent() -> bool:
    """True when ENCRYPTION_KEY was supplied via the environment and therefore
    persists across restarts; False when it was auto-generated for this process
    only (saved credentials will be lost on the next restart)."""
    return ENCRYPTION_KEY_PERSISTENT


def encrypt_value(plaintext: str) -> str:
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt_value(ciphertext: str) -> str:
    return _fernet.decrypt(ciphertext.encode()).decode()


def mask_key(value: str) -> str:
    if len(value) <= 4:
        return "••••••••"
    return "••••••••" + value[-4:]
