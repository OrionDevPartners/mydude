"""Local AI Models console — guided setup + live status for the local sovereign
inference providers (Ollama, Apple MLX).

These providers run on the operator's own machine, not in this container, so the
app cannot literally install them for you. What it can do — and what this screen
provides — is the one-click-grade glue around them:

  * live status of each local server (reachable? which models are loaded?) using
    the very same TCP probe (``_server_listening``) the adapters gate
    availability on, plus the OpenAI-compatible model listing each server
    exposes;
  * copy-ready install / start / pull commands for each provider, with the
    default model names sourced from env_1 (config/providers.toml) so the
    guidance can never drift from what the swarm actually resolves;
  * a view of the local model registry (~/.mydude/local/model_registry.yaml)
    with a refresh that re-reads it from disk.
"""
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from src.providers.adapters import _LocalOpenAICompatAdapter, _server_listening
from src.providers.config import llm_provider_specs
from src.providers.local_registry import (
    load_local_models,
    local_models_for_provider,
    registry_path,
)
from src.providers.registry import build_adapter
from src.web.auth import require_auth
from src.web.templating import templates

logger = logging.getLogger(__name__)
router = APIRouter()

# Friendly display names + install guidance keyed by the provider key in env_1.
# Commands intentionally avoid hardcoding model names — those are injected from
# each provider's ``default_model`` so this stays a single source of truth.
_PROVIDER_META = {
    "ollama": {
        "label": "Ollama",
        "blurb": "Cross-platform local inference (macOS, Linux, Windows). "
                 "Runs GGUF models behind an OpenAI-compatible API.",
        "install_url": "https://ollama.com/download",
        "models_url": "https://ollama.com/library",
        "install_cmd": "curl -fsSL https://ollama.com/install.sh | sh",
        "serve_cmd": "ollama serve",
        "pull_tmpl": "ollama pull {model}",
        "install_note": "On macOS/Windows you can also install the desktop app "
                        "from the download page; it starts the server for you.",
    },
    "mlx": {
        "label": "Apple MLX",
        "blurb": "Apple-silicon (M-series) local inference via mlx_lm's "
                 "OpenAI-compatible server. macOS only.",
        "install_url": "https://github.com/ml-explore/mlx-lm",
        "models_url": "https://huggingface.co/mlx-community",
        "install_cmd": "pip install mlx-lm",
        "serve_cmd": "mlx_lm.server --port {port}",
        "pull_tmpl": "mlx_lm.server --model {model} --port {port}",
        "install_note": "Requires an Apple-silicon Mac. The model downloads "
                        "automatically the first time the server serves it.",
    },
}


def _port_from_url(base_url: str) -> str:
    from urllib.parse import urlparse

    try:
        parsed = urlparse(base_url)
        return str(parsed.port or "")
    except Exception:
        return ""


async def _provider_status(spec) -> dict:
    """Build the live status + guidance block for one local provider spec."""
    adapter = build_adapter(spec)
    base_url = adapter._base_url()
    reachable = _server_listening(base_url)

    try:
        default_model = await adapter.resolve_model()
    except Exception:
        default_model = spec.default_model

    loaded_models = None
    list_error = None
    if reachable:
        try:
            loaded_models = await adapter.list_models()
        except Exception as e:  # server up but listing failed — surface it
            list_error = str(e)
            logger.info("list_models failed for %s: %s", spec.key, e)

    meta = _PROVIDER_META.get(spec.key, {})
    port = _port_from_url(base_url)
    pull_model = spec.default_model or default_model

    guidance = None
    if meta:
        guidance = {
            "install_url": meta.get("install_url"),
            "models_url": meta.get("models_url"),
            "install_note": meta.get("install_note"),
            "install_cmd": meta.get("install_cmd", ""),
            "serve_cmd": (meta.get("serve_cmd", "") or "").format(port=port),
            "pull_cmd": (meta.get("pull_tmpl", "") or "").format(
                model=pull_model, port=port
            ),
        }

    return {
        "key": spec.key,
        "label": meta.get("label", spec.key.title()),
        "blurb": meta.get("blurb", spec.role_hint or ""),
        "base_url": base_url,
        "reachable": reachable,
        "default_model": default_model,
        "loaded_models": loaded_models,
        "list_error": list_error,
        "registry_models": local_models_for_provider(spec.key),
        "guidance": guidance,
        "model_env": spec.model_env,
        "concurrency": spec.default_concurrency,
    }


def _is_local(spec) -> bool:
    try:
        return isinstance(build_adapter(spec), _LocalOpenAICompatAdapter)
    except Exception:
        return False


@router.get("/local-models", response_class=HTMLResponse)
async def local_models_page(request: Request, _=Depends(require_auth)):
    specs = [s for s in llm_provider_specs() if _is_local(s)]
    providers = [await _provider_status(s) for s in specs]

    reachable_count = sum(1 for p in providers if p["reachable"])

    registry = load_local_models()
    p = registry_path()

    return templates.TemplateResponse("local_models.html", {
        "request": request,
        "providers": providers,
        "reachable_count": reachable_count,
        "total_count": len(providers),
        "registry": registry,
        "registry_path": str(p),
        "registry_exists": p.exists(),
        "msg": request.query_params.get("msg"),
        "err": request.query_params.get("err"),
    })
