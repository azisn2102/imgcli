"""Provider: Google Flow (flow.google.com) image generation via Playwright.

Membuat project baru, mengatur setelan gambar (model Nano Banana Pro, rasio,
jumlah x4), mengirim prompt, lalu mengambil gambar hasilnya.
"""

from __future__ import annotations

import base64
import os
import sys
import time
import urllib.request
from pathlib import Path

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)
DEBUG = bool(os.environ.get("IMGCLI_DEBUG"))
MODEL = os.environ.get("FLOW_MODEL", "Nano Banana Pro")


def _eprint(*a, **k):
    print(*a, file=sys.stderr, **k)


def _log(msg: str) -> None:
    if DEBUG:
        _eprint(f"[flow] {msg}")


class FlowSession:
    def __init__(self, storage: Path, headless: bool | None = None, ratio: str = "1:1", n: int = 4, profile: Path | str | None = None):
        self.storage = Path(storage) if storage else None
        self.profile = Path(profile) if profile else None
        self.headless = headless
        self.ratio = ratio
        self.n = n
        self._pw = None
        self._browser = None
        self._ctx = None
        self.page = None
        self._shared = False

    def __enter__(self) -> FlowSession:
        from .. import browser, config

        if browser.cdp_url():
            self._pw, self._browser, self._ctx = browser.attach(
                viewport=(1500, 950), user_agent=browser.CHROME_UA, locale="id-ID"
            )
            self._shared = True
            self.page = self._ctx.new_page()
            self.page.set_default_timeout(60000)
            self.page.goto("https://flow.google.com/", wait_until="domcontentloaded", timeout=60000)
            self.page.wait_for_timeout(6000)
            return self

        profile = self.profile or config.flow_profile_dir()
        self._pw, self._ctx, self.headless = browser.launch(
            profile, headless=self.headless, user_agent=browser.CHROME_UA, viewport=(1500, 950), locale="id-ID"
        )
        if not self._ctx.pages:
            self._ctx.new_page()
        self.page = self._ctx.pages[0]
        self.page.set_default_timeout(60000)
        self.page.goto("https://flow.google.com/", wait_until="domcontentloaded", timeout=60000)
        self.page.wait_for_timeout(6000)
        return self

    def __exit__(self, *exc) -> None:
        if getattr(self, "_shared", False):
            try:
                if self.page:
                    self.page.close()
            except Exception:
                pass
            try:
                if self._pw:
                    self._pw.stop()
            except Exception:
                pass
            return
        try:
            if self._ctx:
                self._ctx.close()
        except Exception:
            pass
        try:
            fd = getattr(self._ctx, "_imgcli_lock_fd", None) if self._ctx else None
            if fd is not None:
                os.close(fd)
                self._ctx._imgcli_lock_fd = None
        except Exception:
            pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass

    def _klik_teks(self, teks: str, exact: bool = False, timeout: float = 8000) -> None:
        skrip = """
        ([n, ex]) => {
            const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
            const els = [...document.querySelectorAll('button,[role=button],[role=radio],[role=menuitem]')];
            const f = els.find(e => {
                const t = norm(e.innerText);
                return e.offsetParent !== null && (ex ? t === n : t.includes(n));
            });
            if (!f) return false;
            f.click();
            return true;
        }
        """
        try:
            if self.page.evaluate(skrip, [teks, bool(exact)]):
                return
        except Exception:
            pass
        loc = self.page.get_by_text(teks, exact=exact).first
        loc.wait_for(state="visible", timeout=timeout)
        loc.click()

    def _klik_ikon(self, nama: str) -> None:
        h = self.page.evaluate_handle(
            "(n)=>{const els=[...document.querySelectorAll('button,[role=button]')];return els.find(e=>(e.innerText||'').trim()===n)||null;}",
            nama,
        )
        el = h.as_element()
        if el is None:
            raise RuntimeError(f"tombol '{nama}' tidak ditemukan di Flow")
        el.click()

    def _setelan(self) -> None:
        self._klik_ikon("tune")
        self.page.wait_for_timeout(2000)
        try:
            self._klik_teks(self.ratio, exact=False, timeout=5000)
        except Exception as exc:
            _log(f"rasio {self.ratio} gagal: {exc}")
        try:
            self._klik_teks("x4", exact=True, timeout=5000)
        except Exception as exc:
            _log(f"jumlah x4 gagal: {exc}")
        try:
            self._klik_teks("Nano Banana", exact=False, timeout=5000)
            self.page.wait_for_timeout(1500)
            try:
                self._klik_teks(MODEL, exact=False, timeout=5000)
            except Exception as exc:
                _log(f"model {MODEL} tidak ketemu: {exc}")
        except Exception as exc:
            _log(f"dropdown model gagal: {exc}")
        try:
            self._klik_teks("Simpan", exact=True, timeout=5000)
        except Exception as exc:
            _log(f"Simpan gagal: {exc}")
        self.page.wait_for_timeout(1500)

    def _kotak_prompt(self):
        for sel in ("div[contenteditable=true]", "textarea"):
            loc = self.page.locator(sel)
            try:
                n = loc.count()
            except Exception:
                n = 0
            for i in range(n):
                try:
                    el = loc.nth(i)
                    if el.is_visible() and "recaptcha" not in (el.get_attribute("class") or ""):
                        return el
                except Exception:
                    continue
        return None

    def _ambil(self, url: str) -> bytes:
        b64 = self.page.evaluate(
            """async (u) => {
                const r = await fetch(u, { credentials: 'include' });
                const b = new Uint8Array(await r.arrayBuffer());
                let s = '';
                for (let i = 0; i < b.length; i += 1024)
                    s += String.fromCharCode.apply(null, b.subarray(i, i + Math.min(1024, b.length - i)));
                return btoa(s);
            }""",
            url,
        )
        return base64.b64decode(b64)

    def _unduh(self, url: str) -> bytes:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=90) as r:
                return r.read()
        except Exception as exc:
            _log(f"unduh langsung gagal: {exc}")
        return self._ambil(url)

    def generate(self, prompt: str, timeout: float = 420) -> list:
        percobaan_maks = int(os.environ.get("FLOW_ATTEMPTS", "3") or "3")
        percobaan_maks = max(1, min(5, percobaan_maks))

        # Satu TRX = SATU project Flow. Project dibuat sekali saja, lalu dipakai
        # ulang untuk semua percobaan. Retry TIDAK PERNAH membuat "Project baru".
        try:
            self._klik_teks("Project baru", exact=True, timeout=20000)
        except Exception as exc:
            self.page.screenshot(path="/tmp/flow-no-image.png")
            raise RuntimeError(f"Flow: gagal membuat project ({exc})")
        self.page.wait_for_timeout(6500)
        _log(f"project: {self.page.url}")
        self._setelan()

        ids = []
        for percobaan in range(percobaan_maks):
            if percobaan > 0:
                _log(f"percobaan {percobaan + 1}/{percobaan_maks}: kirim ulang di project yang sama (target {self.n})")
            box = self._kotak_prompt()
            if box is None:
                _log("kotak perintah tidak ditemukan")
                continue
            box.click()
            self.page.keyboard.type(prompt, delay=6)
            self.page.wait_for_timeout(1200)
            try:
                self._klik_ikon("arrow_forward")
            except Exception as exc:
                _log(f"kirim gagal: {exc}")
                continue
            _log("menunggu gambar Flow...")

            deadline = time.time() + timeout
            kali = []
            while time.time() < deadline:
                self.page.wait_for_timeout(4000)
                kali = self.page.evaluate(
                    """() => [...new Set(Array.from(document.querySelectorAll('img[data-media-id]'))
                         .map(x => x.getAttribute('data-media-id')))]"""
                )
                if len(kali) >= self.n:
                    break
            if len(kali) > len(ids):
                ids = kali
            if len(ids) >= self.n:
                break
            _log(f"percobaan {percobaan + 1}: dapat {len(kali)}/{self.n}")
        if not ids:
            self.page.screenshot(path="/tmp/flow-no-image.png")
            raise RuntimeError("Flow tidak menghasilkan gambar (screenshot: /tmp/flow-no-image.png)")
        _log(f"media muncul: {len(ids)}")

        grid_url = self.page.url
        ids = [i for i in ids if i][: self.n]

        # thumbnail (/asb/) tiap media sebagai fallback
        id_src = self.page.evaluate(
            """() => {
                const out = {};
                for (const x of document.querySelectorAll('img[data-media-id]')) {
                    const id = x.getAttribute('data-media-id');
                    if (id && !out[id] && (x.src || '').includes('/asb/')) out[id] = x.src;
                }
                return out;
            }"""
        )

        # tangkap URL full-res (flow-content.google) dengan membuka tiap media
        urls = {}

        def _cap(resp):
            try:
                u = resp.url
                if "flow-content.google/image/" in u:
                    mid = u.split("/image/")[1].split("?")[0]
                    urls[mid] = u
            except Exception:
                pass

        self.page.on("response", _cap)
        try:
            for k in range(len(ids)):
                if len(urls) >= self.n:
                    break
                try:
                    img = self.page.locator('img[data-media-id="' + ids[k] + '"]').first
                    box = img.bounding_box()
                    if box:
                        self.page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                    self.page.wait_for_timeout(4000)
                except Exception as exc:
                    _log(f"buka media {k} gagal: {exc}")
                try:
                    self.page.goto(grid_url, wait_until="domcontentloaded", timeout=30000)
                    self.page.wait_for_timeout(2500)
                except Exception:
                    pass
        finally:
            try:
                self.page.remove_listener("response", _cap)
            except Exception:
                pass

        hasil = []
        for mid in ids:
            data = None
            if urls.get(mid):
                try:
                    data = self._unduh(urls[mid])
                except Exception as exc:
                    _log(f"full-res {mid[:8]} gagal: {exc}")
            if not data and id_src.get(mid):
                try:
                    data = self._ambil(id_src[mid])
                except Exception:
                    data = None
            if data:
                hasil.append(data)

        if not hasil:
            self.page.screenshot(path="/tmp/flow-no-image.png")
            raise RuntimeError("Flow tidak menghasilkan gambar (screenshot: /tmp/flow-no-image.png)")
        _log(f"dapat {len(hasil)} gambar Flow (full-res: {len(urls)})")
        return hasil


def generate(prompt: str, storage: Path, ratio: str = "1:1", n: int = 4, timeout: float = 420) -> list:
    with FlowSession(storage, ratio=ratio, n=n) as session:
        return session.generate(prompt, timeout)
