import os
from cryptography.fernet import Fernet

_key = os.environ.get("ENCRYPTION_KEY")
if not _key:
    _key = Fernet.generate_key().decode()
    os.environ["ENCRYPTION_KEY"] = _key

_fernet = Fernet(_key.encode() if isinstance(_key, str) else _key)


def encrypt_value(plaintext: str) -> str:
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt_value(ciphertext: str) -> str:
    return _fernet.decrypt(ciphertext.encode()).decode()


def mask_key(value: str) -> str:
    if len(value) <= 4:
        return "••••••••"
    return "••••••••" + value[-4:]
