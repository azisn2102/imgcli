"""Google sign-in: authenticate once with Playwright and save the session."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from .config import default_storage_path

GEMINI = "https://gemini.google.com/app"
AUTH_COOKIES = ("__Secure-1PSID", "__Secure-1PSIDTS", "SNlM0q", "__Secure-1PSIDCC")
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)
SKIP_BUTTONS = (
    "Skip", "Lewati", "Not now", "Tidak sekarang", "Later", "Nanti",
    "I'll do this later", "Nanti saja", "Selanjutnya", "Continue", "Lanjutkan",
    "I agree", "Saya setuju", "Agree", "Oke", "OK",
)


def _eprint(*a, **k):
    print(*a, file=sys.stderr, **k)


def _is_authed(ctx) -> bool:
    names = {c["name"] for c in ctx.cookies("https://gemini.google.com")}
    return "__Secure-1PSID" in names and "__Secure-1PSIDTS" in names


def _visible_buttons(page):
    try:
        return page.evaluate(
            """() => Array.from(document.querySelectorAll('button, [role=button], a'))
                 .filter(e => e.offsetParent !== null)
                 .map(e => (e.innerText || e.getAttribute('aria-label') || '').trim())
                 .filter(Boolean).slice(0, 30)"""
        )
    except Exception:
        return []


def _handle_checkpoint(page, diag: bool) -> bool:
    body = page.inner_text("body").lower()
    if any(k in body for k in ("may not be secure", "tidak aman", "not secure")):
        for text in ("Advanced", "Lanjutan", "Try again", "Coba lagi"):
            try:
                loc = page.get_by_text(text, exact=True).first
                if loc.count() and loc.is_visible():
                    loc.click()
                    _eprint(f"  >> clicked '{text}' (not-secure page)")
                    page.wait_for_timeout(2500)
                    return True
            except Exception:
                continue
    if "captcha" in body or "i'm not a robot" in body or "saya bukan robot" in body:
        _eprint("  ! reCAPTCHA detected - solve manually")
        return False
    if any(k in body for k in ("recovery", "pemulihan", "checkpoint", "verify", "verifikasi", "confirm", "konfirmasi")):
        if diag:
            _eprint("  checkpoint page, buttons:")
            for b in _visible_buttons(page):
                _eprint("    -", b)
        for prefix in SKIP_BUTTONS:
            try:
                loc = page.get_by_text(prefix, exact=True).first
                if loc.count() and loc.is_visible():
                    loc.click()
                    _eprint(f"  >> clicked '{prefix}'")
                    page.wait_for_timeout(2500)
                    return True
            except Exception:
                continue
    return False


def _sign_in(page, email: str, password: str | None) -> bool:
    from playwright.sync_api import TimeoutError as PWTimeout

    page.goto(GEMINI, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2500)
    if _is_authed(page.context):
        return True

    try:
        page.get_by_text("Sign in", exact=True).first.click(timeout=8000)
    except Exception:
        page.goto("https://accounts.google.com/ServiceLogin", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2000)

    try:
        email_field = page.locator("input[type=email], input#identifierId").first
        email_field.wait_for(state="visible", timeout=8000)
        email_field.click()
        email_field.fill(email)
        page.keyboard.press("Enter")
    except PWTimeout:
        clicked = False
        for i in range(page.locator("[data-email]").count()):
            if email.lower() in (page.locator("[data-email]").nth(i).get_attribute("data-email") or "").lower():
                page.locator("[data-email]").nth(i).click()
                clicked = True
                break
        if not clicked:
            _eprint("  ! could not find email field or account chooser entry")
            page.screenshot(path="/tmp/google-login-email.png")
            return False
    page.wait_for_timeout(2500)

    try:
        pwd = page.locator("input[type=password]").first
        pwd.wait_for(state="visible", timeout=45000)
        if password:
            pwd.click()
            pwd.fill(password)
            page.keyboard.press("Enter")
    except PWTimeout:
        _eprint("  ! password field did not appear (check flow.google.com / captcha)")
        page.screenshot(path="/tmp/google-login-pwd.png")
        return False
    return True


def _save(ctx, storage: Path) -> None:
    try:
        ctx.storage_state(path=str(storage))
        os.chmod(storage, 0o600)
    except Exception:
        pass


def login(
    email: str,
    password: str | None = None,
    *,
    manual: bool = False,
    display: str | None = None,
    wait: int = 600,
    storage: Path | None = None,
    profile: Path | None = None,
) -> Path:
    """Sign in to Google once. The persistent profile keeps the session alive."""
    from . import browser as _browser
    from . import config

    storage = Path(storage) if storage else default_storage_path()
    storage.parent.mkdir(parents=True, exist_ok=True)
    profile = Path(profile) if profile else config.profile_dir()
    if display:
        os.environ["DISPLAY"] = display

    pw, ctx, _headless = _browser.launch(profile, headless=False, user_agent=_browser.CHROME_UA)
    try:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.set_default_timeout(60000)

        if _is_authed(ctx):
            _save(ctx, storage)
            _eprint(f"already signed in -> saved {storage} (profile: {profile})")
            return storage

        _eprint("opening Google sign-in ...")
        if manual:
            page.goto("https://accounts.google.com/ServiceLogin", wait_until="domcontentloaded", timeout=60000)
            _eprint(f"  >> sign in manually in the browser. Waiting up to {wait}s ...")
        elif not _sign_in(page, email, password):
            _eprint("  sign-in step failed (see /tmp/google-login-*.png)")
            raise SystemExit(1)

        deadline = time.time() + (wait if manual else 330)
        diag = 0
        while time.time() < deadline:
            if _is_authed(ctx):
                break
            if not manual:
                _handle_checkpoint(page, diag < 4)
            diag += 1
            page.wait_for_timeout(4000)
        else:
            _eprint("  timeout waiting for sign-in. Try flow.google.com confirmation or rerun with --manual")
            page.screenshot(path="/tmp/google-login-timeout.png")
            raise SystemExit(1)

        _save(ctx, storage)
        present = {c for c in AUTH_COOKIES if c in {x["name"] for x in ctx.cookies()}}
        _eprint(f"sign-in OK -> profile saved ({profile}); backup {storage} ({', '.join(sorted(present))})")
    finally:
        try:
            ctx.close()
        except Exception:
            pass
        try:
            pw.stop()
        except Exception:
            pass
    return storage
