import subprocess
import os
import logging

logger = logging.getLogger(__name__)

OP_BIN = "/tmp/op_bin/op"

SECRET_MAP = {
    "OPENAI_API_KEY": "op://Security/pkdfq4txhw4a7opjqgq4bk4wza/password",
    "ANTHROPIC_API_KEY": "op://Security/yoqqzcgul5sdwn24si2vlzuwzm/password",
    "GEMINI_API_KEY": "op://Security/p2ekopsycdr4edaynx3rqkor3m/password",
    "GROK_API_KEY": "op://Security/5qtm7ttf7o4yycptuttbadri2e/credential",
    "ASANA_PAT": "op://Security/jhpvfnugdmsrz7clygqblqhqqm/credential",
}


def _ensure_op_cli():
    if os.path.exists(OP_BIN):
        return True

    logger.info("Downloading 1Password CLI...")
    try:
        subprocess.run(
            ["mkdir", "-p", "/tmp/op_bin"],
            check=True, capture_output=True
        )
        subprocess.run(
            ["curl", "-sS",
             "https://cache.agilebits.com/dist/1P/op2/pkg/v2.30.3/op_linux_amd64_v2.30.3.zip",
             "-o", "/tmp/op.zip"],
            check=True, capture_output=True, timeout=30
        )
        subprocess.run(
            ["unzip", "-o", "/tmp/op.zip", "-d", "/tmp/op_bin"],
            check=True, capture_output=True
        )
        subprocess.run(
            ["chmod", "+x", OP_BIN],
            check=True, capture_output=True
        )
        logger.info("1Password CLI installed.")
        return True
    except Exception as e:
        logger.warning(f"Failed to install 1Password CLI: {e}")
        return False


def _op_read(reference: str) -> str:
    token = os.getenv("OP_SERVICE_ACCOUNT_TOKEN")
    if not token:
        return ""

    env = os.environ.copy()
    env["OP_SERVICE_ACCOUNT_TOKEN"] = token

    try:
        result = subprocess.run(
            [OP_BIN, "read", reference],
            capture_output=True, text=True, timeout=15, env=env
        )
        if result.returncode == 0:
            return result.stdout.strip()
        else:
            logger.warning(f"op read failed for {reference}: {result.stderr.strip()}")
            return ""
    except Exception as e:
        logger.warning(f"op read error for {reference}: {e}")
        return ""


def load_secrets_from_1password():
    token = os.getenv("OP_SERVICE_ACCOUNT_TOKEN")
    if not token:
        logger.info("No OP_SERVICE_ACCOUNT_TOKEN set, skipping 1Password secret loading.")
        return {}

    if not _ensure_op_cli():
        logger.warning("1Password CLI not available, skipping secret loading.")
        return {}

    loaded = {}
    for env_var, reference in SECRET_MAP.items():
        if os.getenv(env_var):
            logger.info(f"{env_var} already set, skipping 1Password lookup.")
            continue

        value = _op_read(reference)
        if value:
            os.environ[env_var] = value
            loaded[env_var] = True
            logger.info(f"Loaded {env_var} from 1Password.")
        else:
            logger.warning(f"Could not load {env_var} from 1Password.")

    return loaded
