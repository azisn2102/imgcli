# imgcli

Generate AI images from the command line with Google Gemini (Nano Banana).

Two backends, one interface:

| Mode | Auth | Cost | Notes |
|------|------|------|-------|
| `web` (default when signed in) | your Google account session | free | drives `gemini.google.com` with Playwright |
| `api` | `GEMINI_API_KEY` | per quota | official REST endpoint |

> **Disclaimer:** the `web` backend automates the consumer Gemini UI and may
> violate Google's Terms of Service. Use at your own risk; prefer `--api` for
> production. Your `storage_state.json` contains live session credentials — keep
> it private. See [LICENSE](LICENSE) (MIT) for the software license only; it does
> not grant any rights to Google's services.

## Install

**One line** (Linux/macOS/Termux, needs Python ≥ 3.11 and curl+tar — no git required):

```bash
curl -fsSL https://raw.githubusercontent.com/azisn2102/imgcli/main/install.sh | bash
```

The installer checks its environment (platform, network, Python ≥ 3.11),
installs `imgcli` from the **pinned release tarball** (via `pipx` if available,
else `pip`), and pre-downloads Chromium. Useful overrides:

```bash
IMGCLI_NO_BROWSER=1 ... | bash   # skip Chromium (API-only install)
IMGCLI_VERSION=v2.1.1 ... | bash  # install a different release tag
```

**Termux / Android:** the `web` backend (Playwright Chromium) does not exist
there, so the installer skips Chromium automatically — use the official API:

```bash
export GEMINI_API_KEY=...
imgcli --api "a red apple, photorealistic"
```

After installing on Linux/macOS:

```bash
imgcli login    # sign in to Google once (free web mode)
imgcli "a red apple, photorealistic"
```

**Manual install:**

```bash
pipx install git+https://github.com/azisn2102/imgcli.git   # or: pip install git+...
imgcli setup    # downloads Chromium (once; also automatic on first web use)
```

From a clone:

```bash
git clone https://github.com/azisn2102/imgcli.git
cd imgcli
pip install -e .
imgcli setup
```

Chromium is downloaded automatically the first time a browser is needed —
`imgcli setup` just does it up front. `--api` mode needs no browser at all.

## Login once (free web mode)

```bash
# headless server: start a virtual display + VNC, then log in manually
Xvfb :99 -screen 0 1280x900x24 &
x11vnc -display :99 -forever -nopw -rport 5900 &
imgcli login --manual --display :99
# connect a VNC client to 127.0.0.1:5900 and complete the Google sign-in

# or non-interactive (may trigger extra Google verification):
GOOGLE_EMAIL=you@gmail.com GOOGLE_PASSWORD=... imgcli login
```

The session is stored at `~/.config/imgcli/storage_state.json` (mode `600`).

### Stay logged in (headless servers)

`keepalive.sh` refreshes the session periodically so the login does not
expire — useful on headless machines:

```bash
./keepalive.sh start     # refresh every 30 min (IMGCLI_KEEPALIVE_MIN=15 to change)
./keepalive.sh status
./keepalive.sh stop
```

For servers, one persistent Chromium driven over CDP is faster than a fresh
browser per run (each image saves several seconds):

```bash
imgcli browser --port 9222 --headless   # run once, keep alive
IMGCLI_CDP=http://127.0.0.1:9222 imgcli "a red apple, photorealistic"
```

## Usage

```bash
imgcli "a red apple on a wooden table, photorealistic"
imgcli "neon cyberpunk city" --ar 16:9 -n 3 -o out/
imgcli "cute robot" --negative blurry,low quality --sidecar
imgcli "poster" --api -m gemini-2.5-flash-image      # official API
imgcli flow "isometric cat poster" --ar 1:1 -n 4     # flow.google.com, 4 candidates
imgcli --list-models
```

Options: `-n/--num`, `-W/--width`, `-H/--height`, `--ar`, `-o/--output`,
`--negative`, `--ref`, `--storage`, `--headless`, `--json`, `-q/--quiet`,
`--sidecar`.

Sub-commands: `login`, `browser` (persistent CDP Chromium), `keepalive`,
`tanya` (ask Gemini, text only), `judge` (pick the best of several images),
`nilai-prompt` (rate and improve a prompt), `flow`, `gen`/`generate`.

## Configuration

`~/.config/imgcli/config.toml`:

```toml
[imgcli]
outdir = "output"
width = 1024
height = 1024
model = "gemini-2.5-flash-image"
provider = "auto"     # auto | web | api
web_timeout = 180
```

Useful environment variables: `IMGCLI_CDP` (attach to a running Chromium,
fastest), `IMGCLI_MODEL` (web model, default `Pro`), `IMGCLI_REASONING`
(`rendah`/`sedang`/`tinggi`), `IMGCLI_RETRY` (1–6), `IMGCLI_WEB_STORAGE=1`,
`IMGCLI_DEBUG=1` (verbose logs), `GEMINI_API_KEY`.

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check src
IMGCLI_DEBUG=1 imgcli "test"   # verbose web-mode diagnostics
```

## Layout

```
src/imgcli/
  cli.py            argument parsing + UX
  config.py         config + paths
  image.py          naming, format detection, metadata
  browser.py        persistent Chromium / CDP attach
  session.py        Google sign-in / saved session
  providers/
    web.py          gemini.google.com via Playwright
    flow.py         flow.google.com via Playwright
    api.py          Gemini REST API
keepalive.sh        session-refresh daemon for headless servers
install.sh          one-line installer (curl | bash)
tests/              offline tests (no API calls)
```

## Troubleshooting

- **Termux/Android: `mode web (Playwright Chromium) tidak tersedia`** — expected:
  Android has no Chromium build for Playwright. Use `imgcli --api` with
  `GEMINI_API_KEY` instead.
- **`curl | tar` fails** — `pkg install curl tar` (Termux) or your package manager.
- **Chromium download skipped/failed** — retry with `imgcli setup` (Linux/macOS),
  or install fully without Chromium: `IMGCLI_NO_BROWSER=1` and use `--api`.
- **`imgcli` not found after install** — add the printed bin directory to PATH:
  `export PATH="$HOME/.local/bin:$PATH"`.
- **Google session expired** — run `imgcli login` again.

## License

MIT — see [LICENSE](LICENSE).
