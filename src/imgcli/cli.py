"""Command-line interface for imgcli."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from . import __version__, config
from .image import detect_ext, output_path, save_image, slugify
from .providers import api, web


def _color(enabled: bool):
    def wrap(code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if enabled else text

    return wrap


def _print(msg: str, quiet: bool) -> None:
    if not quiet:
        print(msg, file=sys.stderr)


def parse_ratio(text: str) -> tuple[int, int]:
    try:
        a, b = text.replace("x", ":").split(":")
        return int(a), int(b)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid aspect ratio: {text!r} (use e.g. 16:9)") from exc


def size_from_ratio(a: int, b: int, base: int = 1024) -> tuple[int, int]:
    if a >= b:
        w, h = base, round(base * b / a)
    else:
        w, h = round(base * a / b), base
    return (w // 8) * 8, (h // 8) * 8


# --------------------------------------------------------------------------- #
# sub-commands
# --------------------------------------------------------------------------- #

def _run_generate(args) -> int:
    cfg = config.load_config()
    use_color = sys.stderr.isatty() and not args.json
    c = _color(use_color)
    quiet = args.quiet or args.json

    if args.list_models:
        print("\n".join(config.GEMINI_MODELS))
        return 0
    if not args.prompt:
        print("error: a prompt is required (try: imgcli --help)", file=sys.stderr)
        return 2
    if args.num < 1:
        print("error: --num must be >= 1", file=sys.stderr)
        return 2

    width, height = args.width, args.height
    if args.ar:
        width, height = size_from_ratio(*args.ar)

    storage = Path(args.storage) if args.storage else config.default_storage_path()
    profile = Path(args.profile) if getattr(args, "profile", None) else config.profile_dir()
    provider = args.provider
    if args.web:
        provider = "web"
    elif args.api:
        provider = "api"
    if provider == "auto":
        provider = "web" if (storage.exists() or profile.exists()) else "api"

    if provider == "api" and not config.get_api_key():
        print("error: no API key (export GEMINI_API_KEY=...) and no web session", file=sys.stderr)
        print("       run: imgcli login   to use your free Google account", file=sys.stderr)
        return 2
    if provider == "web" and not (storage.exists() or profile.exists()):
        print(f"error: no saved session at {storage}", file=sys.stderr)
        print("       run: imgcli login", file=sys.stderr)
        return 2

    refs = list(args.ref or [])
    for r in refs:
        if not os.path.isfile(r):
            print(f"error: reference image not found: {r}", file=sys.stderr)
            return 2
        if detect_ext(Path(r).read_bytes()[:64]) == ".img":
            print(f"error: reference image is not a supported format: {r}", file=sys.stderr)
            return 2

    model = args.model or cfg["model"]
    outdir = args.output or cfg["outdir"]
    headless = True if args.headless else None

    results: list[dict] = []
    started_all = time.time()

    def handle(i: int, data: bytes) -> None:
        path = output_path(outdir, args.prompt, i, detect_ext(data))
        meta = {"prompt": args.prompt, "provider": provider, "model": model, "width": width, "height": height}
        save_image(path, data, meta if args.sidecar else None)
        results.append({"index": i + 1, "ok": True, "path": str(path.resolve()), "bytes": len(data)})
        _print(f"  {c('32', 'saved')} {path} ({len(data)} bytes)", quiet)

    try:
        if provider == "web":
            _print(
                f"[{args.num}] '{args.prompt}' via web session ({width}x{height}) ..."
                if not quiet else "", quiet,
            )
            with web.WebSession(storage, headless=headless, profile=profile) as session:
                for i in range(args.num):
                    t = time.time()
                    try:
                        data = session.generate(args.prompt, width, height, args.negative, args.web_timeout, refs or None)
                    except Exception as exc:
                        _print(f"  {c('31', 'gagal')} gambar {i + 1}: {exc}", quiet)
                        results.append({"index": i + 1, "ok": False, "error": str(exc)[:200]})
                        continue
                    _print(f"  ({time.time() - t:.1f}s)", quiet)
                    handle(i, data)
        else:
            for i in range(args.num):
                _print(f"[{i + 1}/{args.num}] '{args.prompt}' via api ({model}, {width}x{height}) ...", quiet)
                data = api.generate(args.prompt, model, width, height, args.negative, args.timeout)
                handle(i, data)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        ok = len(results)
        total = time.time() - started_all
        _print(f"{c('1', 'done')}: {ok}/{args.num} image(s) in {total:.1f}s", quiet)
    return 0 if any(r.get("ok") for r in results) else 1


def _run_login(args) -> int:
    from . import session

    email = args.email or os.environ.get("GOOGLE_EMAIL")
    password = args.password or os.environ.get("GOOGLE_PASSWORD")
    if not email and not args.manual:
        print("error: set GOOGLE_EMAIL (or pass --email), or use --manual", file=sys.stderr)
        return 2
    storage = Path(args.storage) if args.storage else config.default_storage_path()
    try:
        saved = session.login(
            email or "",
            password,
            manual=args.manual,
            display=args.display,
            wait=args.wait,
            storage=storage,
            profile=Path(args.profile) if args.profile else config.profile_dir(),
        )
    except SystemExit as exc:
        return int(exc.code or 1)
    print(saved)
    return 0


def _run_keepalive(args) -> int:
    from .providers.web import WebSession

    if args.display:
        os.environ["DISPLAY"] = args.display
    storage = Path(args.storage) if args.storage else config.default_storage_path()
    profile = Path(args.profile) if args.profile else config.profile_dir()

    while True:
        try:
            with WebSession(storage, headless=True if args.headless else None, profile=profile) as session:
                ok = session.keepalive(wait=args.wait, storage=storage)
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr)
            ok = False

        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        if ok:
            print(f"[{stamp}] session OK (refreshed)")
        else:
            print(f"[{stamp}] session NOT authenticated - run: imgcli login", file=sys.stderr)

        if not args.interval:
            return 0 if ok else 1
        time.sleep(max(1, args.interval) * 60)


def _build_keepalive_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="imgcli keepalive",
        description="Open the Gemini app to refresh the session so it does not expire. Run periodically (cron).",
    )
    p.add_argument("--storage", help="path to the backup storage_state.json")
    p.add_argument("--profile", help="path to the persistent Chromium profile")
    p.add_argument("--headless", action="store_true", help="run headless (no X server needed)")
    p.add_argument("--display", help="X display for the browser (e.g. :99)")
    p.add_argument("--wait", type=float, default=6.0, help="seconds to stay on the page (default: 6)")
    p.add_argument("--interval", type=int, default=0, help="run forever, repeating every N minutes (default: run once)")
    return p


def _run_judge(args) -> int:
    from .providers.web import WebSession

    if not args.img:
        print("error: butuh minimal satu gambar (--img)", file=sys.stderr)
        return 2
    storage = Path(args.storage) if args.storage else config.default_storage_path()
    try:
        with WebSession(storage, headless=True) as session:
            hasil = session.menilai(args.img, args.prompt, args.timeout)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(hasil, ensure_ascii=False, indent=2))
    return 0


def _run_nilai_prompt(args) -> int:
    from .providers.web import WebSession

    storage = Path(args.storage) if args.storage else config.default_storage_path()

    def tanya(teks: str, timeout: float) -> str:
        with WebSession(storage, headless=True) as session:
            return session.tanya(teks, timeout)

    try:
        penilaian = tanya(
            "Dalam 1-3 kalimat, nilai kualitas prompt gambar berikut: \""
            + args.prompt
            + "\". Sebut apakah prompt sudah jelas, spesifik, dan cukup detail. Balas hanya penilaiannya, tanpa basa-basi.",
            args.timeout,
        )
        saran = tanya(
            "PENTING: jangan menghasilkan gambar apa pun, balas hanya teks. "
            "Tulis satu prompt gambar versi yang lebih baik dan lebih detail dari prompt ini: \""
            + args.prompt
            + "\". Balas hanya teks prompt barunya saja, tanpa penjelasan atau daftar.",
            min(args.timeout, 150.0),
        )
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"penilaian": penilaian, "saran": saran}, ensure_ascii=False, indent=2))
    return 0


def _build_nilai_prompt_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="imgcli nilai-prompt", description="Nilai kualitas prompt dan usulkan versi perbaikan.")
    p.add_argument("--prompt", required=True, help="prompt yang mau dinilai")
    p.add_argument("--timeout", type=float, default=180, help="detik menunggu jawaban (default: 180)")
    p.add_argument("--storage", help="path ke sesi tersimpan")
    return p


def _run_flow(args) -> int:
    from .providers.flow import FlowSession

    storage = Path(args.storage) if args.storage else config.default_storage_path()
    outdir = Path(args.output or config.load_config()["outdir"])
    outdir.mkdir(parents=True, exist_ok=True)
    try:
        with FlowSession(storage, ratio=(args.ar or "1:1"), n=args.num) as session:
            blobs = session.generate(args.prompt, timeout=args.timeout)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    results = []
    for i, data in enumerate(blobs):
        ext = detect_ext(data)
        p = outdir / f"flow-{slugify(args.prompt)}-{i + 1:02d}{ext}"
        p.write_bytes(data)
        results.append({"index": i + 1, "ok": True, "path": str(p.resolve()), "bytes": len(data), "sumber": "flow"})
    print(json.dumps(results, ensure_ascii=False))
    return 0 if results else 1


def _build_flow_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="imgcli flow", description="Generate images via Google Flow (flow.google.com).")
    p.add_argument("prompt", help="text prompt")
    p.add_argument("-n", "--num", type=int, default=4, help="number of images (default: 4)")
    p.add_argument("--ar", help="aspect ratio, e.g. 1:1 or 16:9")
    p.add_argument("-o", "--output", help="output directory")
    p.add_argument("--timeout", type=float, default=420, help="seconds to wait (default: 420)")
    p.add_argument("--storage", help="path to the saved session")
    return p


def _build_judge_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="imgcli judge",
        description="Bandingkan beberapa gambar hasil generate dan rekomendasikan yang terbaik.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            '  imgcli judge --prompt "kucing" --img out/g1.jpg --img out/g2.jpg\n'
        ),
    )
    p.add_argument("--prompt", required=True, help="prompt asli yang dipakai membuat gambar")
    p.add_argument("--img", action="append", default=[], help="path gambar hasil (repeatable)")
    p.add_argument("--timeout", type=float, default=240, help="detik menunggu jawaban juri (default: 240)")
    p.add_argument("--storage", help="path ke sesi tersimpan")
    return p


# --------------------------------------------------------------------------- #
# argument parsing
# --------------------------------------------------------------------------- #

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="imgcli",
        description="Generate AI images with Google Gemini (Nano Banana) from the command line.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  imgcli \"a red apple, photorealistic\"\n"
            "  imgcli \"neon city\" --ar 16:9 -n 3 -o out/\n"
            "  imgcli login                       # sign in once (free Google account)\n"
            "  imgcli \"robot\" --api -m gemini-2.5-flash-image\n"
        ),
    )
    p.add_argument("prompt", nargs="?", help="text description of the image")
    p.add_argument("-p", "--provider", choices=("auto", "web", "api"), default=None, help="backend (default: auto)")
    p.add_argument("--web", action="store_true", help="use your signed-in Google session (free)")
    p.add_argument("--api", action="store_true", help="use the Gemini API key")
    p.add_argument("-o", "--output", help="output file (single) or directory")
    p.add_argument("-n", "--num", type=int, default=1, help="number of images (default: 1)")
    p.add_argument("-W", "--width", type=int, default=None, help="image width in px")
    p.add_argument("-H", "--height", type=int, default=None, help="image height in px")
    p.add_argument("--ar", type=parse_ratio, help="aspect ratio, e.g. 16:9 (overrides -W/-H)")
    p.add_argument("-m", "--model", help="API model name (see --list-models)")
    p.add_argument("--negative", help="things to avoid")
    p.add_argument("--ref", action="append", default=[], help="path to a reference image (repeatable, web mode)")
    p.add_argument("--storage", help="path to the saved session (default: ~/.config/imgcli/storage_state.json)")
    p.add_argument("--profile", help="path to the persistent Chromium profile (default: ~/.config/imgcli/chrome-profile)")
    p.add_argument("--headless", action="store_true", help="run the web browser headless")
    p.add_argument("--timeout", type=int, default=None, help="API request timeout (s)")
    p.add_argument("--web-timeout", type=float, default=None, help="seconds to wait for a web image")
    p.add_argument("--sidecar", action="store_true", help="also write a .json metadata file per image")
    p.add_argument("--json", action="store_true", help="print results as JSON")
    p.add_argument("-q", "--quiet", action="store_true", help="suppress progress output")
    p.add_argument("--list-models", action="store_true", help="list API models")
    p.add_argument("--version", action="version", version=f"imgcli {__version__}")
    return p


def _build_login_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="imgcli login", description="Sign in to Google once and save the session.")
    p.add_argument("--email", help="Google account email")
    p.add_argument("--password", help="Google password (prefer GOOGLE_PASSWORD env)")
    p.add_argument("--manual", action="store_true", help="type credentials in the browser window")
    p.add_argument("--display", help="X display for the browser (e.g. :99)")
    p.add_argument("--wait", type=int, default=600, help="seconds to wait in --manual mode (default: 600)")
    p.add_argument("--storage", help="where to save the session")
    p.add_argument("--profile", help="persistent Chromium profile directory to use/store")
    return p


def _build_browser_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="imgcli browser",
        description="Jalankan SATU Chromium persisten (CDP) agar Gemini & Flow berbagi 1 sesi/1 browser.",
    )
    p.add_argument("--port", type=int, default=9222, help="port remote debugging (default 9222)")
    p.add_argument("--profile", help="direktori profil Chromium (default: chrome-profile)")
    p.add_argument("--display", help="X display (mis. :99) untuk mode kepala")
    p.add_argument("--headless", action="store_true", help="jalankan headless (tanpa X)")
    return p


def _run_browser(args) -> int:
    from . import browser, config

    profile = Path(args.profile) if args.profile else config.profile_dir()
    if not args.headless and not args.display:
        args.display = ":99"
    proc = browser.serve(profile, port=args.port, display=args.display, headless=args.headless)
    print(
        f"imgcli browser: Chromium pid={proc.pid} port={args.port} profile={profile} "
        f"headless={args.headless} display={args.display}",
        flush=True,
    )
    try:
        proc.wait()
    except KeyboardInterrupt:
        try:
            proc.terminate()
        except Exception:
            pass
    return 0


def _build_tanya_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="imgcli tanya",
        description="Tanya teks ke Gemini (sesi web via Playwright/CDP, tanpa membuat gambar).",
    )
    p.add_argument("teks", nargs="*", help="teks pertanyaan (kosong = dibaca dari stdin)")
    p.add_argument("--timeout", type=float, default=240, help="batas tunggu detik (default 240)")
    p.add_argument("--storage", help="path storage_state.json")
    p.add_argument("--json", action="store_true", help="keluarkan JSON")
    return p


def _run_tanya(args) -> int:
    from .providers.web import WebSession

    teks = " ".join(args.teks).strip()
    if not teks:
        try:
            teks = sys.stdin.read().strip()
        except Exception:
            teks = ""
    if not teks:
        print("error: teks kosong", file=sys.stderr)
        return 2
    storage = Path(args.storage) if args.storage else config.default_storage_path()
    try:
        with WebSession(storage, headless=True) as session:
            jawab = session.tanya(teks, args.timeout)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({"ok": True, "teks": jawab}, ensure_ascii=False))
    else:
        print(jawab)
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if argv and argv[0] == "login":
        args = _build_login_parser().parse_args(argv[1:])
        return _run_login(args)

    if argv and argv[0] == "browser":
        args = _build_browser_parser().parse_args(argv[1:])
        return _run_browser(args)

    if argv and argv[0] == "tanya":
        args = _build_tanya_parser().parse_args(argv[1:])
        return _run_tanya(args)

    if argv and argv[0] == "keepalive":
        args = _build_keepalive_parser().parse_args(argv[1:])
        return _run_keepalive(args)

    if argv and argv[0] == "judge":
        args = _build_judge_parser().parse_args(argv[1:])
        return _run_judge(args)

    if argv and argv[0] == "nilai-prompt":
        args = _build_nilai_prompt_parser().parse_args(argv[1:])
        return _run_nilai_prompt(args)

    if argv and argv[0] == "flow":
        args = _build_flow_parser().parse_args(argv[1:])
        return _run_flow(args)

    if argv and argv[0] in ("gen", "generate"):
        argv = argv[1:]

    args = _build_parser().parse_args(argv)

    cfg = config.load_config()
    if args.provider is None:
        args.provider = cfg["provider"]
    if args.width is None:
        args.width = cfg["width"]
    if args.height is None:
        args.height = cfg["height"]
    if args.timeout is None:
        args.timeout = cfg["timeout"]
    if args.web_timeout is None:
        args.web_timeout = cfg["web_timeout"]

    return _run_generate(args)
