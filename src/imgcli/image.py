"""Image helpers: naming, format detection, saving, metadata."""

from __future__ import annotations

import json
import os
import random
import re
import time
from pathlib import Path


def slugify(text: str, maxlen: int = 48) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    return (slug[:maxlen].rstrip("-")) or "image"


def detect_ext(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ".img"


def jpeg_size(data: bytes) -> tuple[int, int] | None:
    """Return (width, height) for a JPEG, or None."""
    i = 2
    n = len(data)
    while i + 9 <= n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3):
            h = int.from_bytes(data[i + 5:i + 7], "big")
            w = int.from_bytes(data[i + 7:i + 9], "big")
            return w, h
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seg = int.from_bytes(data[i + 2:i + 4], "big")
        if seg <= 0:
            break
        i += 2 + seg
    return None


def output_path(out: str | None, prompt: str, index: int, ext: str, outdir: str = "output") -> Path:
    """Resolve the destination path for a single image.

    ``out`` is treated as a *file* only when it carries an extension;
    otherwise it is treated as a directory.
    """
    if out and os.path.splitext(out)[1]:
        return Path(out)

    base = Path(out or outdir)
    base.mkdir(parents=True, exist_ok=True)
    token = f"{random.randint(0, 0xFFFFFF):06x}"
    return base / f"{slugify(prompt)}-{index + 1:02d}-{token}{ext}"


def save_image(path: Path, data: bytes, meta: dict | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if meta is not None:
        sidecar = path.with_suffix(path.suffix + ".json")
        meta = dict(meta)
        meta.setdefault("file", path.name)
        meta.setdefault("bytes", len(data))
        meta.setdefault("created", time.strftime("%Y-%m-%dT%H:%M:%S"))
        dims = jpeg_size(data)
        if dims:
            meta.setdefault("width", dims[0])
            meta.setdefault("height", dims[1])
        sidecar.write_text(json.dumps(meta, indent=2, ensure_ascii=False))
