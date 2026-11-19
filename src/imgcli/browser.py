"""Shared Playwright browser launching with a persistent profile.

Using ``launch_persistent_context`` stores the whole browser profile
(cookies that rotate automatically, device-trust signals, localStorage,
IndexedDB), which keeps Google/OpenAI sessions alive far longer than a
one-shot ``storage_state.json``.
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path

CHROME_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_COOKIE_FIELDS = ("name", "value", "domain", "path", "expires", "httpOnly", "secure", "sameSite")
_LOCK_TIMEOUT = 900  # detik menunggu profil tidak dipakai proses lain


def _is_termux() -> bool:
    """Deteksi Termux saja (Playwright Chromium tidak tersedia di sana).

    Sekedar kernel Android bukan alasan (userland Linux biasa tetap bisa
    menjalankan Chromium), jadi hanya cek penanda khas Termux.
    """
    prefix = os.environ.get("PREFIX", "")
    if "com.termux" in prefix:
        return True
    return os.path.isdir("/data/data/com.termux/files/usr")


def pastikan_chromium() -> str:
    """Pastikan chromium milik Playwright terunduh; unduh otomatis bila belum ada.

    Dipanggil sebelum browser dibuka supaya instalasi cukup satu langkah:
    setelah ``pip install imgcli``, pemakaian pertama langsung jalan.
    Mengembalikan path executable chromium.
    """
    if _is_termux():
        raise RuntimeError(
            "mode web (Playwright Chromium) tidak tersedia di Termux/Android — "
            "gunakan API resmi: export GEMINI_API_KEY=... lalu `imgcli --api ...`"
        )
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        exe = p.chromium.executable_path
    if exe and os.path.exists(exe):
        return exe
    print(
        "playwright chromium belum terpasang — mengunduh sekarang "
        "(sekali saja, ±170MB)...",
        file=sys.stderr,
    )
    subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"])
    with sync_playwright() as p:
        exe = p.chromium.executable_path
    if not exe or not os.path.exists(exe):
        raise RuntimeError(
            "unduhan chromium gagal — jalankan manual: playwright install chromium"
        )
    return exe


def _args() -> list[str]:
    return [
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-blink-features=AutomationControlled",
        "--start-maximized",
    ]


def _kunci_profil(profile_dir: Path):
    """flock pada profil supaya generasi & keepalive tak bentrok (Chromium menolak profil dobel)."""
    lock_path = Path(profile_dir) / ".imgcli.lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    t0 = time.time()
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except OSError:
            if time.time() - t0 > _LOCK_TIMEOUT:
                os.close(fd)
                raise RuntimeError("profil Chromium sedang dipakai proses lain")
            time.sleep(2)


def launch(profile_dir, headless=None, viewport=(1280, 900), user_agent=CHROME_UA, locale="en-US"):
    """Start a persistent browser context. Returns (playwright, context, headless_used)."""
    from playwright.sync_api import sync_playwright

    pastikan_chromium()
    profile_dir = Path(profile_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)
    lock_fd = _kunci_profil(profile_dir)

    pw = sync_playwright().start()
    kwargs = {
        "user_data_dir": str(profile_dir),
        "args": _args(),
        "locale": locale,
        "viewport": {"width": viewport[0], "height": viewport[1]},
        "user_agent": user_agent,
    }
    order = [False, True] if headless is None else [headless]
    last = None
    for h in order:
        try:
            ctx = pw.chromium.launch_persistent_context(headless=h, **kwargs)
            try:
                ctx._imgcli_lock_fd = lock_fd
            except Exception:
                pass
            return pw, ctx, h
        except Exception as exc:  # headed may fail without an X server
            last = exc
    os.close(lock_fd)
    pw.stop()
    raise RuntimeError(f"could not launch a browser: {last}")


def load_cookies(ctx, storage_path) -> int:
    """Seed cookies from a storage_state.json into the context (migration)."""
    try:
        data = json.loads(Path(storage_path).read_text())
    except Exception:
        return 0
    cookies = []
    for c in data.get("cookies", []):
        clean = {k: c[k] for k in _COOKIE_FIELDS if k in c}
        if clean.get("sameSite") not in ("Strict", "Lax", "None", None):
            clean["sameSite"] = "Lax"
        if "domain" in clean and "name" in clean:
            cookies.append(clean)
    added = 0
    for c in cookies:
        try:
            ctx.add_cookies([c])
            added += 1
        except Exception:
            continue
    return added


def has_cookies(ctx, names) -> bool:
    current = {c["name"] for c in ctx.cookies()}
    return any(n in current for n in names)


# --- Satu browser / banyak tab (CDP) ---------------------------------------
# Alih-alih membuka satu Chromium per proses (profil dobel -> sesi dicabut
# Google), kita jalankan SATU Chromium persisten lalu tiap proses menempel
# (connect_over_cdp) dan membuka tab-nya sendiri.

def cdp_url() -> str:
    return (os.environ.get("IMGCLI_CDP", "") or "").strip()


def attach(viewport=(1500, 950), user_agent=CHROME_UA, locale="id-ID"):
    """Menempel ke Chromium yang sudah jalan via CDP. Kembalikan (pw, browser, context)."""
    from playwright.sync_api import sync_playwright

    url = cdp_url()
    if not url:
        raise RuntimeError("IMGCLI_CDP tidak diset")
    pw = sync_playwright().start()
    browser = pw.chromium.connect_over_cdp(url)
    ctx = browser.contexts[0] if browser.contexts else browser.new_context(
        viewport={"width": viewport[0], "height": viewport[1]},
        user_agent=user_agent,
        locale=locale,
    )
    try:
        ctx.set_default_timeout(60000)
    except Exception:
        pass
    return pw, browser, ctx


def chromium_exe() -> str:
    from playwright.sync_api import sync_playwright

    pastikan_chromium()
    with sync_playwright() as p:
        return p.chromium.executable_path


def serve(profile_dir, port: int = 9222, display: str | None = None, headless: bool = False):
    """Jalankan SATU Chromium persisten dengan remote debugging (untuk CDP)."""
    import subprocess
    import urllib.request

    exe = chromium_exe()
    profile_dir = Path(profile_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)
    args = [
        exe,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-blink-features=AutomationControlled",
        "--no-first-run",
        "--no-default-browser-check",
        "--start-maximized",
        "--force-color-profile=srgb",
        "--window-size=1500,950",
        "--disable-background-timer-throttling",
        "--disable-renderer-backgrounding",
        "--disable-backgrounding-occluded-windows",
        "--disable-features=CalculateNativeWinOcclusion,Translate,BackForwardCache",
        "--disable-ipc-flooding-protection",
        "--disable-hang-monitor",
        "--mute-audio",
        "--enable-unsafe-swiftshader",
        "--ignore-gpu-blocklist",
        "--use-gl=angle",
        "--use-angle=swiftshader",
    ]
    if headless:
        args.append("--headless=new")
    args.append("about:blank")
    env = dict(os.environ)
    if display:
        env["DISPLAY"] = display
    proc = subprocess.Popen(args, env=env)
    for _ in range(90):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1)
            return proc
        except Exception:
            time.sleep(1)
    return proc
