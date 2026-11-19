#!/usr/bin/env bash
# imgcli — one-line installer
#   curl -fsSL https://raw.githubusercontent.com/azisn2102/imgcli/main/install.sh | bash
# Also usable offline-ish: it only needs python >= 3.11 and network to GitHub/Playwright.
set -euo pipefail

REPO="https://github.com/azisn2102/imgcli"

say() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

# --- 1) python >= 3.11 ---------------------------------------------------------
PY=""
for c in python3.13 python3.12 python3.11 python3 python; do
  if command -v "$c" >/dev/null 2>&1; then
    ok=$("$c" -c 'import sys; print(sys.version_info[:2] >= (3, 11))' 2>/dev/null || echo False)
    if [ "$ok" = "True" ]; then PY="$c"; break; fi
  fi
done
if [ -z "$PY" ]; then
  cat >&2 <<'EOF'
error: Python >= 3.11 tidak ditemukan.
  Debian/Ubuntu : sudo apt-get install -y python3 python3-pip
  Fedora        : sudo dnf install -y python3 python3-pip
  macOS         : brew install python@3.12
Lalu jalankan ulang installer ini.
EOF
  exit 1
fi
say "python: $("$PY" --version) ($PY)"

# --- 2) install imgcli (pipx preferred, fallback pip --user) --------------------
if command -v pipx >/dev/null 2>&1; then
  say "menginstall imgcli lewat pipx dari $REPO ..."
  pipx install --force "git+$REPO.git"
  BIN="$(command -v imgcli || true)"
else
  say "pipx tidak ada — memakai pip --user ..."
  say "(direkomendasikan: install pipx dulu: '$PY' -m pip install --user pipx)"
  "$PY" -m pip install --user --upgrade "git+$REPO.git"
  BIN="$(command -v imgcli || true)"
  [ -z "$BIN" ] && BIN="$HOME/.local/bin/imgcli"
fi
[ -n "$BIN" ] && [ -x "$BIN" ] || die "binary imgcli tidak ditemukan setelah instalasi"
say "terpasang: $BIN ($("$BIN" --version))"

# --- 3) download chromium (sekali saja) ------------------------------------------
say "menyiapkan chromium (sekali saja, ±170MB) ..."
"$BIN" setup

# --- 4) PATH check ----------------------------------------------------------------
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) say "catatan: $HOME/.local/bin belum ada di PATH — tambahkan ke ~/.bashrc:"
     say "        export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac

say "selesai. Langkah berikutnya:"
say "  imgcli login    # login Google sekali (mode web gratis)"
say "  imgcli \"a red apple, photorealistic\""
