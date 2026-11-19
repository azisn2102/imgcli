"""Provider: Google Gemini image generation via the signed-in web session.

Drives gemini.google.com with Playwright (reuses one browser across images)
and captures the full-resolution file through the image's download button.
"""

from __future__ import annotations

import base64
import os
import re
import sys
import time
from pathlib import Path

USER_AGENT = "imgcli/2.0"
DEBUG = bool(os.environ.get("IMGCLI_DEBUG"))

DOWNLOAD_SELECTORS = (
    'button[aria-label*="ukuran penuh" i]',
    'button[aria-label*="full size" i]',
    'button[aria-label*="Download gambar" i]',
    'button[aria-label*="Unduh gambar" i]',
)
SEND_LABELS = ("Kirim pesan", "Send message", "Send", "Kirim", "Submit")
NEW_CHAT_LABELS = ("Percakapan baru", "New chat", "Chat baru")
PENOLAKAN = (
    "tidak dapat membuat gambar",
    "tidak bisa membuat gambar",
    "tidak dapat menghasilkan gambar",
    "tidak bisa menghasilkan gambar",
    "gambar yang tidak aman",
    "tidak aman",
    "unsafe",
    "can't create",
    "cannot create",
    "can't generate",
    "unable to generate",
    "not able to generate",
    "tidak dapat memproses permintaan",
    "tidak dapat memenuhi permintaan",
    "tidak dapat membantu",
)


def _eprint(*a, **k):
    print(*a, file=sys.stderr, **k)


def _log(msg: str) -> None:
    if DEBUG:
        _eprint(f"[dbg] {msg}")


def _mengetik(teks: str) -> bool:
    t = (teks or "").lower()
    if len(t.strip()) > 120:
        return False
    return any(k in t for k in ("mengetik", "typing", "sedang berpikir", "thinking")) or t.rstrip().endswith(("…", "..."))


def _penolakan_teks(teks: str) -> bool:
    t = (teks or "").strip().lower()
    if len(t) < 120:
        return True
    for k in (
        "tidak diprogram",
        "tidak dapat membantu",
        "tidak bisa membantu",
        "tidak dapat melakukan",
        "maaf, saya tidak",
        "i can't",
        "i cannot",
        "cannot help",
    ):
        if k in t:
            return True
    return False


def _teks_status(teks: str) -> bool:
    t = (teks or "").strip().lower()
    if len(t) < 25:
        return True
    # Jawaban panjang (mis. prompt gambar) bukan status, walau memuat frasa di bawah.
    if len(t) > 200:
        return False
    for k in ("membuat gambar", "membuat gambar anda", "creating your image", "generating image",
              "menghasilkan gambar", "sedang membuat", "sedang menyiapkan", "loading"):
        if k in t:
            return True
    return False


def _bersihkan(teks: str) -> str:
    baris = []
    for b in (teks or "").splitlines():
        s = b.strip()
        if s in ("JPG", "PNG", "+ 1", "+1", "", "```"):
            continue
        baris.append(b.rstrip())
    hasil = "\n".join(baris)
    while "\n\n\n" in hasil:
        hasil = hasil.replace("\n\n\n", "\n\n")
    return hasil.strip()


def _pisah_gambar(teks: str, maks: int) -> list:
    hasil = {}
    idx = None
    buf = []
    for baris in (teks or "").splitlines():
        m = re.match(r"(?i)^\s*[>*#\-\s]*(?:evaluasi|penilaian)?\s*:?\s*gambar\s*(?:ke-?\s*)?(\d+)\b", baris)
        if not m:
            m = re.match(r"(?i)^\s*[>*#\-\s]*[^\n:]{0,18}?\bgambar\s*(?:ke-?\s*)?(\d+)\s*[:(]", baris)
        if m:
            i = int(m.group(1))
            if 1 <= i <= maks:
                if idx is not None and buf:
                    hasil.setdefault(idx, "\n".join(buf).strip())
                idx = i
                buf = [baris]
                continue
        if idx is not None:
            buf.append(baris)
    if idx is not None and buf:
        hasil.setdefault(idx, "\n".join(buf).strip())
    return [{"i": k, "teks": hasil[k]} for k in sorted(hasil)]


def _garis_saran(teks: str, maks: int) -> list:
    hasil = {}
    pola = re.compile(r"(?im)^[\s>*#-]*gambar\s*(?:ke-?\s*)?(\d+)\s*[:\-]\s*(.+)$")
    for m in pola.finditer(teks or ""):
        i = int(m.group(1))
        if 1 <= i <= maks and i not in hasil:
            isi = _bersihkan(m.group(2)).strip()
            if isi:
                hasil[i] = isi
    return [{"i": k, "teks": hasil[k]} for k in sorted(hasil)]


def _saran_per_gambar(teks: str, maks: int) -> list:
    keluar = []
    for b in _pisah_gambar(teks, maks):
        blok = _bersihkan(b["teks"])
        m = re.search(r"saran[^:\n]*:\s*(.+)$", blok, re.IGNORECASE | re.DOTALL)
        isi = _bersihkan(m.group(1)) if m else blok
        isi = re.split(r"(?im)^\s*rekomendasi\s*:", isi)[0]
        isi = re.sub(r"(?is)^\s*gambar\s*(?:ke-?\s*)?\d+[^\n]*\n?", "", isi).strip()
        if isi:
            keluar.append({"i": b["i"], "teks": isi})
    return keluar


def _ekstrak_nomor(teks: str) -> int | None:
    t = teks or ""
    m = re.search(r"REKOMENDASI\s*[:\-]?\s*(?:gambar\s*)?(?:ke-?\s*)?(\d+)", t, re.IGNORECASE)
    if m:
        return int(m.group(1))
    m = re.search(r"gambar\s*ke-?\s*(\d+)", t, re.IGNORECASE)
    if m:
        return int(m.group(1))
    m = re.search(r"gambar\s*(\d+)\s*(?:merupakan|adalah|jadi|sebagai)?\s*(?:yang\s*)?(?:terbaik|paling unggul|pemenang|unggul)", t, re.IGNORECASE)
    if m:
        return int(m.group(1))
    m = re.search(r"(?:terbaik|pemenang|paling unggul|unggul)[^\d]{0,25}gambar\s*(?:ke-?\s*)?(\d+)", t, re.IGNORECASE)
    if m:
        return int(m.group(1))
    skor = {}
    for mm in re.finditer(r"gambar\s*(?:ke-?\s*)?(\d+)[^\n]{0,140}?(\d+)\s*(?:/\s*5|/\s*10|poin|skor)?", t, re.IGNORECASE):
        idx = int(mm.group(1))
        val = int(mm.group(2))
        if 1 <= idx <= 50 and val <= 100:
            skor[idx] = max(skor.get(idx, 0), val)
    if skor:
        return max(skor, key=lambda k: skor[k])
    return None


class WebSession:
    """A reusable browser session authenticated with a persistent Google profile."""

    def __init__(self, storage: Path | str | None = None, headless: bool | None = None, profile: Path | str | None = None):
        self.storage = Path(storage) if storage else None
        self.headless = headless
        self.profile = Path(profile) if profile else None
        self._pw = None
        self._browser = None
        self._ctx = None
        self.page = None
        self._model_set = False
        self._shared = False
        # True bila tab Gemini dipakai ulang lewat CDP (tidak perlu ditutup di __exit__)
        self._reused = False

    def __enter__(self) -> WebSession:
        from .. import browser, config

        if browser.cdp_url():
            self._pw, self._browser, self._ctx = browser.attach(
                viewport=(1280, 900), user_agent=browser.CHROME_UA, locale="id-ID"
            )
            self._shared = True
            # Bersihkan tab bocor (RotateCookies / Gemini ganda) dari sesi sebelumnya
            # sebelum membuka tab baru. Tab-tab ini selalu sampah, aman ditutup.
            try:
                for _pg in list(self._ctx.pages):
                    try:
                        _t = (_pg.title() or "").lower()
                    except Exception:
                        _t = ""
                    if "rotatecookie" in _t or "rotate cookies" in _t:
                        try:
                            _pg.close()
                        except Exception:
                            pass
            except Exception:
                pass
            # Pakai ulang tab Gemini yang sudah ada (hemat goto+reload ±5 dtk).
            # Tab dipertahankan agar CDP tetap menempel di tab yang sama.
            _reuse = None
            try:
                for _pg in self._ctx.pages:
                    try:
                        if "gemini.google.com/app" in (_pg.url or ""):
                            if _reuse is None:
                                _reuse = _pg
                    except Exception:
                        continue
            except Exception:
                _reuse = None
            if _reuse is not None:
                self.page = _reuse
                self._reused = True
            else:
                self.page = self._ctx.new_page()
            self.page.set_default_timeout(60000)
            if not self._reused:
                self.page.goto("https://gemini.google.com/app", wait_until="domcontentloaded", timeout=60000)
            self._tunggu_komposer()
            return self

        use_storage = str(os.environ.get("IMGCLI_WEB_STORAGE", "")).lower() in ("1", "true", "yes")
        storage = self.storage or config.default_storage_path()
        if use_storage and storage and Path(storage).exists():
            from playwright.sync_api import sync_playwright

            self._pw = sync_playwright().start()
            args = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"]
            self._browser = self._pw.chromium.launch(
                headless=(self.headless if self.headless is not None else True), args=args
            )
            self._ctx = self._browser.new_context(
                storage_state=str(storage), locale="en-US", user_agent=browser.CHROME_UA,
                viewport={"width": 1280, "height": 900},
            )
            self.page = self._ctx.new_page()
            self.page.set_default_timeout(60000)
            self.page.goto("https://gemini.google.com/app", wait_until="domcontentloaded", timeout=60000)
            self._tunggu_komposer()
            return self

        profile = self.profile or config.profile_dir()
        new_profile = not profile.exists()

        self._pw, self._ctx, self.headless = browser.launch(
            profile, headless=self.headless, user_agent=browser.CHROME_UA
        )

        # migrate an existing storage_state.json into the fresh profile once
        if new_profile and self.storage and self.storage.exists():
            added = browser.load_cookies(self._ctx, self.storage)
            _log(f"migrated {added} cookies from {self.storage} into the profile")

        if not self._ctx.pages:
            self._ctx.new_page()
        self.page = self._ctx.pages[0]
        self.page.set_default_timeout(60000)
        self.page.goto("https://gemini.google.com/app", wait_until="domcontentloaded", timeout=60000)
        self._tunggu_komposer()
        return self

    def __exit__(self, *exc) -> None:
        if getattr(self, "_shared", False):
            # CDP: biarkan tab Gemini tetap terbuka (selalu dipakai ulang run
            # berikutnya). Duplikat & tab sampah tetap dibersihkan di bawah,
            # dengan menyisakan satu tab Gemini terbaru.
            try:
                _gemini = [pg for pg in list(self._ctx.pages)
                           if "gemini.google.com" in (pg.url or "")]
                for pg in _gemini[:-1] if len(_gemini) > 1 else []:
                    try:
                        pg.close()
                    except Exception:
                        pass
                for pg in list(self._ctx.pages):
                    try:
                        if "rotatecookie" in (pg.title() or "").lower() or "rotate cookies" in (pg.title() or "").lower():
                            pg.close()
                    except Exception:
                        pass
            except Exception:
                pass
            try:
                if self._pw:
                    self._pw.stop()
            except Exception:
                pass
            return
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass
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

    # -- internals ---------------------------------------------------------

    def _tunggu_komposer(self, timeout_ms: int = 15000) -> None:
        """Tunggu kotak input siap, fallback diam sebentar bila tak ketemu."""
        try:
            self.page.wait_for_selector('div[contenteditable="true"]', state="attached", timeout=timeout_ms)
        except Exception:
            self.page.wait_for_timeout(1500)

    def is_authenticated(self) -> bool:
        """True when the auth cookies are present (sama seperti cek saat login)."""
        try:
            names = {c["name"] for c in self._ctx.cookies("https://gemini.google.com")}
            return "__Secure-1PSID" in names and "__Secure-1PSIDTS" in names
        except Exception:
            return self._composer() is not None

    def keepalive(self, wait: float = 6.0, storage: Path | str | None = None) -> bool:
        """Touch the app so Google refreshes the rolling session cookies.

        Also exports the refreshed cookies to ``storage`` (used by the Flow
        provider, which runs in a separate context).
        """
        self.page.wait_for_timeout(int(wait * 1000))
        try:
            self.page.reload(wait_until="domcontentloaded")
            self.page.wait_for_timeout(4000)
        except Exception:
            pass
        ok = self.is_authenticated()
        if ok and storage:
            try:
                self._ctx.storage_state(path=str(storage))
                os.chmod(storage, 0o600)
            except Exception:
                pass
        return ok

    def _pilih_model(self, nama: str, level: str = None) -> None:
        """Pilih mode/model Gemini web + tingkat penalaran (Rendah/Sedang/Tinggi)."""
        if not nama:
            return
        try:
            self.page.wait_for_selector('div[contenteditable="true"]', state="attached", timeout=30000)
        except Exception:
            pass
        ada = False
        for _cobaan in range(2):
            for _ in range(25):
                try:
                    if self.page.evaluate("() => !!document.querySelector('.model-picker-container')"):
                        ada = True
                        break
                except Exception:
                    pass
                self.page.wait_for_timeout(600)
            if ada:
                break
            try:
                self.page.reload(wait_until="domcontentloaded")
                self.page.wait_for_timeout(5000)
            except Exception:
                pass
        h = self.page.evaluate_handle(
            """() => {
                const c = document.querySelector('.model-picker-container');
                if (c) return c;
                const els = [...document.querySelectorAll('*')];
                return els.find((e) => /^(Flash|Pro|Flash-Lite)$/.test((e.innerText || '').replace(/\\s+/g, ' ').trim())) || null;
            }"""
        )
        btn = h.as_element() if h is not None else None
        if btn is None:
            _log("tombol pemilih model tidak ditemukan")
            return
        try:
            try:
                btn.click(timeout=5000)
            except Exception:
                btn.click(force=True, timeout=5000)
        except Exception as exc:
            _log(f"buka pemilih model gagal: {exc}")
            return
        self.page.wait_for_timeout(700)

        # Tingkat penalaran didahulukan (opsinya hanya ada saat panel terbuka)
        lvl = level or os.environ.get("IMGCLI_REASONING") or ""
        if lvl:
            self.page.wait_for_timeout(700)
            ok_lvl = False
            try:
                for _coba in range(4):
                    b = self.page.get_by_text("Tingkat penalaran", exact=False)
                    sudah = False
                    for i in range(b.count()):
                        try:
                            e = b.nth(i)
                            if e.is_visible():
                                e.click(timeout=3000)
                                sudah = True
                                break
                        except Exception:
                            continue
                    if not sudah:
                        try:
                            btn.click(timeout=4000)
                        except Exception:
                            pass
                        self.page.wait_for_timeout(600)
                    self.page.wait_for_timeout(500)
                    map_ = {"rendah": "Rendah", "sedang": "Sedang", "tinggi": "Tinggi", "low": "Rendah", "medium": "Sedang", "high": "Tinggi"}
                    want = map_.get(str(lvl).lower(), str(lvl))
                    lv = self.page.get_by_text(re.compile(r"^\s*" + re.escape(want) + r"\s*$"))
                    for i in range(lv.count()):
                        try:
                            e = lv.nth(i)
                            if e.is_visible():
                                e.click(timeout=3000)
                                ok_lvl = True
                                break
                        except Exception:
                            continue
                    if ok_lvl:
                        break
            except Exception as exc:
                _log(f"set penalaran gagal: {exc}")
            _log(f"tingkat penalaran -> {lvl}" + ("" if ok_lvl else " (opsi tak ketemu)"))
            self.page.wait_for_timeout(400)

        namaL = str(nama).lower()
        try:
            hasil = self.page.evaluate(
                """(namaL) => {
                    const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                    const rows = [...document.querySelectorAll('[role=menuitem],[role=option],[role=radio],[role=menuitemradio],li,button')]
                        .filter((e) => e.offsetParent !== null && norm(e.innerText).length <= 50);
                    let f = null;
                    if (namaL.includes('lite')) f = rows.find((e) => /flash\\s*-?\\s*lite/.test(norm(e.innerText)));
                    else if (namaL === 'flash') f = rows.find((e) => /\\bflash\\b/.test(norm(e.innerText)) && !/lite/.test(norm(e.innerText)));
                    else if (namaL === 'pro') f = rows.find((e) => /\\bpro\\b/.test(norm(e.innerText)));
                    else f = rows.find((e) => norm(e.innerText).includes(namaL));
                    if (f) { f.click(); return norm(f.innerText).slice(0, 40); }
                    return null;
                }""",
                namaL,
            )
            _log(f"model Gemini -> {nama}" + (f" ({hasil})" if hasil else " (opsi tak ketemu)"))
        except Exception as exc:
            _log(f"pilih model gagal: {exc}")
        try:
            self.page.keyboard.press("Escape")
        except Exception:
            pass

    def _pastikan_model(self, nama=None, level=None) -> None:
        """Pilih model Gemini sekali per sesi."""
        if self._model_set:
            return
        nama = nama or os.environ.get("IMGCLI_MODEL") or "Pro"
        self._pilih_model(nama, level)
        self._model_set = True

    def _new_chat(self) -> None:
        for lbl in NEW_CHAT_LABELS:
            try:
                loc = self.page.locator(f'[aria-label="{lbl}"]').first
                if loc.count() and loc.is_visible():
                    loc.click()
                    self.page.wait_for_timeout(800)
                    return
            except Exception:
                continue

    def _composer(self):
        for sel in (
            'rich-textarea div[contenteditable="true"]',
            'div[contenteditable="true"][role="textbox"]',
            'div.text-input-field[contenteditable="true"]',
            'div[contenteditable="true"]',
            "textarea",
        ):
            loc = self.page.locator(sel)
            try:
                n = loc.count()
            except Exception:
                n = 0
            for i in range(n):
                if loc.nth(i).is_visible():
                    return loc.nth(i)
        return None

    def _file_input(self):
        for sel in ("input[type=file]", "input[type='file']"):
            loc = self.page.locator(sel)
            try:
                n = loc.count()
            except Exception:
                n = 0
            for i in range(n):
                try:
                    if loc.nth(i).is_visible():
                        return loc.nth(i)
                except Exception:
                    continue
        return None

    def _selesaikanKonsen(self) -> None:
        # Goog mewajibkan modal penyangkalan saat mengunggah gambar pertama kali.
        for _ in range(8):
            try:
                ada = self.page.evaluate(
                    """() => {
                        const o = document.querySelector('.cdk-overlay-container');
                        return o && /Membuat konten dari gambar|hak yang diperlukan|penyangkalan/i.test(o.innerText || '');
                    }"""
                )
            except Exception:
                ada = False
            if not ada:
                return
            try:
                btn = self.page.locator('button[aria-label*="Setuju"], button:has-text("Setuju")').first
                if btn.count() > 0:
                    btn.click(timeout=3000)
                    self.page.wait_for_timeout(2500)
                    _log("konsen unggah gambar disetujui")
                else:
                    return
            except Exception:
                return

    def _cekTerpasang(self) -> int:
        try:
            return int(
                self.page.evaluate(
                    "() => document.querySelectorAll('rich-textarea img, img[src^=\\\"blob:\\\"]').length"
                )
            )
        except Exception:
            return 0

    def _drop_file(self, b64: str, nama: str) -> None:
        self.page.evaluate(
            """
            async ({ b64, nama }) => {
                const bin = atob(b64);
                const u8 = new Uint8Array(bin.length);
                for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
                const f = new File([u8], nama, { type: "image/jpeg" });
                const dt = new DataTransfer();
                dt.items.add(f);
                const komposer = document.querySelector('div[contenteditable="true"], textarea');
                if (!komposer) throw new Error("komposer tidak ditemukan");
                komposer.dispatchEvent(new DragEvent("drop", { bubbles: true, cancelable: true, dataTransfer: dt }));
            }""",
            {"b64": b64, "nama": nama},
        )

    def _attach(self, refs) -> None:
        if not refs:
            return
        files = [str(Path(r)) for r in refs]

        # 1) input[type=file] eksplisit
        try:
            fin = self.page.locator("input[type=file]").first
            if fin.count() > 0:
                fin.set_input_files(files)
                self.page.wait_for_timeout(2500)
                self._selesaikanKonsen()
                self.page.wait_for_timeout(1500)
                if self._cekTerpasang():
                    _log(f"attached {len(refs)} via input")
                    return
        except Exception as exc:
            _log(f"input attach failed: {exc}")

        # 2) tombol "Upload & tools"/"Upload & alat" -> chooser langsung atau menu
        import re as _re

        labels = [
            r"Upload & tools", r"Upload & alat", r"Tools", r"Alat",
            r"Add files?", r"Tambah file", r"Lampirkan",
        ]
        btn = None
        for _ in range(12):
            try:
                for lbl in labels:
                    loc = self.page.get_by_role("button", name=_re.compile(lbl, _re.IGNORECASE)).first
                    if loc.count() > 0 and loc.is_visible():
                        btn = loc
                        break
                if btn is None:
                    h = self.page.evaluate_handle(
                        """() => {
                            const els = [...document.querySelectorAll('button,[role=button]')];
                            return els.find(e => {
                                const a = (((e.getAttribute('aria-label')||'') + ' ' + (e.innerText||'')).toLowerCase());
                                return a.includes('upload') || a.includes('unggah') || a.includes('& tools') || a.includes('& alat');
                            }) || null;
                        }"""
                    )
                    btn = h.as_element()
            except Exception:
                btn = None
            if btn is not None and btn.is_visible():
                break
            btn = None
            self.page.wait_for_timeout(700)

        if btn is not None:
            chooser = None
            try:
                with self.page.expect_file_chooser(timeout=6000) as fc:
                    btn.click(timeout=6000)
                chooser = fc.value
            except Exception:
                chooser = None
                item = None
                for _ in range(8):
                    for sel in (
                        '[role="menuitem"]:has-text("Upload file")',
                        '[role="menuitem"]:has-text("Upload from device")',
                        '[role="menuitem"]:has-text("Files")',
                        '[role="menuitem"]:has-text("Unggah file")',
                        '[role="menuitem"]:has-text("Upload")',
                        '[role="menuitem"]:has-text("Unggah")',
                    ):
                        loc = self.page.locator(sel).first
                        try:
                            if loc.count() > 0 and loc.is_visible():
                                item = loc
                                break
                        except Exception:
                            continue
                    if item is not None:
                        break
                    self.page.wait_for_timeout(600)
                if item is not None:
                    try:
                        with self.page.expect_file_chooser(timeout=8000) as fc2:
                            item.click(timeout=3000)
                        chooser = fc2.value
                    except Exception:
                        chooser = None
            if chooser is not None:
                try:
                    chooser.set_files(files)
                    self.page.wait_for_timeout(2500)
                    self._selesaikanKonsen()
                    self.page.wait_for_timeout(2500)
                    if self._cekTerpasang():
                        _log(f"attached {len(refs)} via tombol upload")
                        return
                except Exception as exc:
                    _log(f"set files gagal: {exc}")
            try:
                fin2 = self.page.locator("input[type=file]").first
                if fin2.count() > 0:
                    fin2.set_input_files(files)
                    self.page.wait_for_timeout(2500)
                    self._selesaikanKonsen()
                    self.page.wait_for_timeout(1500)
                    if self._cekTerpasang():
                        _log(f"attached {len(refs)} via input (setelah tombol)")
                        return
            except Exception as exc:
                _log(f"input2 attach failed: {exc}")

        # 3) drop event (fallback) + selesaikan modal penyangkalan
        import base64 as _b64

        try:
            payload = [(_b64.b64encode(Path(r).read_bytes()).decode(), Path(r).name) for r in refs]
            for _ in range(2):
                self._drop_file(*payload[0])
                self.page.wait_for_timeout(1500)
                self._selesaikanKonsen()
                self.page.wait_for_timeout(2000)
                for b64, nama in payload[1:]:
                    self._drop_file(b64, nama)
                self.page.wait_for_timeout(2000)
                if self._cekTerpasang():
                    _log(f"attached {len(refs)} via drop")
                    return
        except Exception as exc:
            _log(f"drop attach failed: {exc}")

        self.page.screenshot(path="/tmp/gemini-no-file-input.png")
        raise RuntimeError("gagal melampirkan foto referensi (screenshot: /tmp/gemini-no-file-input.png)")

    def _send(self, text: str, refs: list | None = None) -> None:
        composer = self._composer()
        if composer is None:
            self.page.screenshot(path="/tmp/gemini-no-composer.png")
            raise RuntimeError("composer not found - session may have expired (screenshot: /tmp/gemini-no-composer.png)")

        self._attach(refs)
        self._selesaikanKonsen()
        try:
            composer.click(timeout=8000)
        except Exception:
            try:
                self.page.keyboard.press("Escape")
                self.page.wait_for_timeout(800)
            except Exception:
                pass
            self._selesaikanKonsen()
            try:
                composer.click(timeout=8000)
            except Exception:
                composer.click(force=True, timeout=8000)
        self.page.keyboard.press("Control+A")
        self.page.keyboard.press("Delete")
        try:
            self.page.keyboard.insert_text(text)
        except Exception:
            self.page.keyboard.type(text, delay=2)
        self.page.wait_for_timeout(300)
        _log(f"typed {text[:50]!r}")

        for lbl in SEND_LABELS:
            try:
                loc = self.page.locator(f'button[aria-label="{lbl}"]')
                if loc.count() and loc.first.is_visible() and not loc.first.is_disabled():
                    loc.first.click()
                    _log(f"clicked send '{lbl}'")
                    self.page.wait_for_timeout(600)
                    return
            except Exception:
                continue
        self.page.keyboard.press("Enter")
        self.page.wait_for_timeout(600)

    def _teks_terakhir(self) -> str:
        try:
            return str(
                self.page.evaluate(
                    """() => {
                        const w = Array.from(document.querySelectorAll('model-response, message-content'));
                        if (w.length) return (w[w.length - 1].innerText || w[w.length - 1].textContent || '').trim();
                        const md = Array.from(document.querySelectorAll('.markdown'));
                        return md.length ? (md[md.length - 1].innerText || '').trim() : '';
                    }"""
                )
                or ""
            )
        except Exception:
            return ""

    def _wait_image(self, timeout: float) -> bytes | None:
        deadline = time.time() + timeout
        k = 0
        self._penolakan = None
        while time.time() < deadline:
            for sel in DOWNLOAD_SELECTORS:
                try:
                    loc = self.page.locator(sel)
                    if loc.count() and loc.first.is_visible():
                        try:
                            with self.page.expect_download(timeout=30000) as dl:
                                loc.first.click()
                            path = dl.value.path()
                            if path:
                                return Path(path).read_bytes()
                        except Exception as exc:
                            _log(f"download capture failed: {exc}")
                except Exception:
                    continue

            data = self.page.evaluate(
                """async () => {
                    const big = Array.from(document.querySelectorAll('img'))
                        .filter(i => i.naturalWidth >= 384 && i.complete);
                    if (!big.length) return null;
                    big.sort((a, b) => b.naturalWidth*b.naturalHeight - a.naturalWidth*a.naturalHeight);
                    try {
                        const r = await fetch(big[0].currentSrc || big[0].src, {credentials: 'include'});
                        const bytes = new Uint8Array(await r.arrayBuffer());
                        let bin = '';
                        for (let i = 0; i < bytes.length; i += 1024)
                            bin += String.fromCharCode.apply(null, bytes.subarray(i, i + Math.min(1024, bytes.length - i)));
                        return btoa(bin);
                    } catch (e) { return null; }
                }"""
            )
            if data:
                _log(f"got inline image after {k} polls")
                return base64.b64decode(data)

            teks = self._teks_terakhir().lower()
            if teks and any(f in teks for f in PENOLAKAN):
                _log("penolakan Gemini terdeteksi")
                self._penolakan = teks
                return None

            _log(f"poll {k}: waiting for image")
            k += 1
            self.page.wait_for_timeout(1000)
        return None

    def generate(self, prompt: str, width: int, height: int, negative: str | None = None, timeout: float = 180, refs: list | None = None) -> bytes:
        list_refs = list(refs or [])
        full = f"Generate one high-quality image of: {prompt}"
        if width and height:
            full += f". Aspect ratio {width}:{height}."
        if negative:
            full += f" Avoid: {negative}."
        if list_refs:
            full += " Gunakan foto referensi yang sudah dilampirkan sebagai acuan utama objek, gaya, dan komposisi."

        percobaan = int(os.environ.get("IMGCLI_RETRY", "3") or "3")
        percobaan = max(1, min(6, percobaan))
        self._pastikan_model(os.environ.get("IMGCLI_MODEL", "Pro"), os.environ.get("IMGCLI_REASONING"))
        for c in range(percobaan):
            self._new_chat()
            pesan = full if c == 0 else full + " Buat hasilnya aman, sopan, profesional, dan tidak eksplisit."
            self._send(pesan, list_refs)
            data = self._wait_image(timeout)
            if data:
                if c > 0:
                    _log(f"berhasil setelah percobaan {c + 1}")
                return data
            if self._penolakan:
                _log(f"ditolak Gemini (percobaan {c + 1}/{percobaan}) - ulangi")
                self.page.wait_for_timeout(2500)
                continue
            if c < percobaan - 1:
                _log(f"gambar tak muncul (percobaan {c + 1}/{percobaan}) - ulangi")
                continue
        if self._penolakan:
            raise RuntimeError("ditolak Gemini: permintaan dianggap tidak aman")
        self.page.screenshot(path="/tmp/gemini-no-image.png")
        raise RuntimeError("no image appeared in time (screenshot: /tmp/gemini-no-image.png)")

    def menilai(self, daftar_gambar: list, prompt_asli: str, timeout: float = 240) -> dict:
        list_gambar = [str(Path(g)) for g in daftar_gambar]
        if not list_gambar:
            raise RuntimeError("tidak ada gambar untuk dinilai")
        n = len(list_gambar)
        instruksi = (
            "Kamu adalah juri kualitas gambar AI. Lampiran adalah "
            f"{n} gambar hasil generate dari prompt berikut: {prompt_asli}\n\n"
            "Untuk SETIAP gambar, tulis satu blok persis seperti ini:\n"
            "Gambar <nomor>: <penilaian singkat>. Saran perbaikan: <1-2 kalimat saran konkret>\n\n"
            "Setelah semua blok gambar, tulis dua baris:\n"
            "Rekomendasi: Gambar <nomor> terbaik\n"
            "Alasan: <paragraf 80-140 kata Bahasa Indonesia yang membandingkan semua gambar dan "
            "menjelaskan mengapa gambar terpilih paling unggul>\n\n"
            "Nilai pada aspek: kesesuaian dengan prompt, komposisi, gaya dan estetika, warna dan "
            "pencahayaan, detail dan ketajaman, serta naturalitas tanpa cacat. "
            "Jangan membuat gambar. Jangan gunakan tabel."
        )
        self._pastikan_model(os.environ.get("IMGCLI_MODEL_JURI") or "Flash")
        teks = ""
        for _usaha in range(3):
            self._new_chat()
            self._attach(list_gambar)
            self.page.wait_for_timeout(8000)
            comp = self._composer()
            if comp is not None:
                comp.click()
            self.page.keyboard.type(instruksi, delay=6)
            self.page.wait_for_timeout(800)
            terkirim = False
            for lbl in SEND_LABELS:
                try:
                    loc = self.page.locator(f'button[aria-label="{lbl}"]')
                    if loc.count() and loc.first.is_visible() and not loc.first.is_disabled():
                        loc.first.click()
                        terkirim = True
                        break
                except Exception:
                    continue
            if not terkirim:
                self.page.keyboard.press("Enter")
            self.page.wait_for_timeout(600)
            teks = self._wait_teks(timeout)
            if not _penolakan_teks(teks):
                break
            _log(f"juri ditolak/aneh (usaha {_usaha + 1}/3), ulangi")
            self.page.wait_for_timeout(2000)
        nomor = _ekstrak_nomor(teks)
        if nomor is not None and (nomor < 1 or nomor > n):
            nomor = None
        m = re.search(r"alasan\s*:\s*(.+)$", teks, re.IGNORECASE | re.DOTALL)
        alasan = _bersihkan(m.group(1)) if m else _bersihkan(teks)
        return {
            "rekomendasi": nomor,
            "alasan": alasan,
            "saran": _saran_per_gambar(teks, n),
        }

    def tanya(self, teks: str, timeout: float = 180) -> str:
        self._pastikan_model(os.environ.get("IMGCLI_MODEL_JURI") or "Flash")
        sebelum = self._jumlah_respon()
        self._send(teks)
        return _bersihkan(self._wait_teks(timeout, min_baru=sebelum))

    def _jumlah_respon(self) -> int:
        try:
            return int(self.page.evaluate("() => document.querySelectorAll('model-response, message-content').length"))
        except Exception:
            return 0

    def _wait_teks(self, timeout: float, min_baru: int | None = None) -> str:
        deadline = time.time() + timeout
        terakhir = ""
        stabil = 0
        k = 0
        while time.time() < deadline:
            k += 1
            jumlah = self._jumlah_respon()
            siap_baru = min_baru is None or jumlah > min_baru
            try:
                blok = self.page.evaluate(
                    """() => {
                        const wadah = Array.from(document.querySelectorAll('model-response, message-content'));
                        if (wadah.length) {
                            const t = wadah[wadah.length - 1];
                            const teks = (t && (t.innerText || t.textContent) || '').trim();
                            if (teks) return teks;
                        }
                        const md = Array.from(document.querySelectorAll('.markdown'));
                        if (!md.length) return '';
                        return (md[md.length - 1].innerText || '').trim();
                    }"""
                )
            except Exception:
                blok = terakhir
            blok = (blok or "").strip()
            if siap_baru and not _teks_status(blok):
                if len(blok) > len(terakhir):
                    terakhir = blok
                    stabil = 0
                else:
                    stabil += 1
            else:
                stabil += 1
            selesai = "REKOMENDASI" in terakhir.upper()
            if siap_baru and terakhir and not _mengetik(terakhir) and not _teks_status(terakhir) and ((selesai and stabil >= 3) or stabil >= 8):
                _log(f"jawaban teks diterima setelah {k} poll (~{len(terakhir)} char)")
                return terakhir
            _log(f"poll teks {k}: resp={jumlah} siap={siap_baru} {len(blok)} char")
            self.page.wait_for_timeout(1000)
        return terakhir


def generate(prompt: str, storage: Path, width: int, height: int, negative: str | None, timeout: float, refs: list | None = None) -> bytes:
    with WebSession(storage) as session:
        return session.generate(prompt, width, height, negative, timeout, refs)
