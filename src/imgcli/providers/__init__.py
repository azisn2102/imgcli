"""Image generation providers."""

from __future__ import annotations

from pathlib import Path

from . import api, web


def run(
    prompt: str,
    *,
    provider: str,
    model: str,
    width: int,
    height: int,
    negative: str | None,
    timeout: int,
    web_timeout: float,
    storage: Path,
    refs: list | None = None,
) -> bytes:
    """Generate a single image using the requested provider."""
    if provider == "web":
        return web.generate(prompt, storage, width, height, negative, web_timeout, refs)
    if provider == "api":
        if refs:
            raise RuntimeError("reference images are only supported in web mode")
        return api.generate(prompt, model, width, height, negative, timeout)
    raise ValueError(f"unknown provider: {provider}")


def resolve_provider(requested: str, storage: Path) -> str:
    if requested != "auto":
        return requested
    return "web" if storage.exists() else "api"
