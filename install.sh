#!/usr/bin/env bash
# imgcli — safe one-line installer
#   curl -fsSL https://raw.githubusercontent.com/azisn2102/imgcli/main/install.sh | bash
#
# What it does:
#   1) finds python >= 3.11
#   2) installs imgcli from the pinned release tarball (NO git required)
#   3) pre-downloads Chromium (skipped on Termux/Android and when IMGCLI_NO_BROWSER=1)
#
# Environment overrides:
#   IMGCLI_VERSION=v2.1.2   install a different release tag
#   IMGCLI_NO_BROWSER=1     skip the Chromium download (API-only install)
set -euo pipefail

REPO="https://github.com/azisn2102/imgcli"
VERSION="${IMGCLI_VERSION:-v2.1.2}"
TARBALL="$REPO/archive/refs/tags/${VERSION}.tar.gz"

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

is_termux() {
  # Termux saja (PREFIX selalu diset Termux), bukan sekadar kernel Android:
  # userland Linux biasa di atas kernel Android tetap bisa menjalankan Chromium.
  case "${PREFIX:-}" in *com.termux*) return 0 ;; esac
  [ -d /data/data/com.termux/files/usr ] && return 0
  return 1
}

# --- 0) preflight ----------------------------------------------------------------
say "imgcli installer ($VERSION) — preflight checks"
for tool in curl tar; do
  command -v "$tool" >/dev/null 2>&1 || die "'$tool' tidak ditemukan. Install dulu, mis.: pkg install $tool (Termux) / sudo apt-get install $tool"
done
if ! curl -fsSIL --connect-timeout 10 -o /dev/null "$TARBALL"; then
  die "tidak bisa mengakses $TARBALL — periksa koneksi internet / tag rilis"
fi

if is_termux; then
  PLATFORM="termux-android"
else
  case "$(uname -s)" in
    Linux)  PLATFORM="linux" ;;
    Darwin) PLATFORM="macos" ;;
    *)      PLATFORM="$(uname -s | tr 'A-Z' 'a-z')" ;;
  esac
fi
say "platform: $PLATFORM"

# --- 1) python >= 3.11 -------------------------------------------------------------
PY=""
for c in python3.14 python3.13 python3.12 python3.11 python3 python; do
  if command -v "$c" >/dev/null 2>&1; then
    ok=$("$c" -c 'import sys; print(sys.version_info[:2] >= (3, 11))' 2>/dev/null || echo False)
    if [ "$ok" = "True" ]; then PY="$c"; break; fi
  fi
done
if [ -z "$PY" ]; then
  if is_termux; then
    die "Python >= 3.11 tidak ditemukan. Jalankan: pkg install python"
  fi
  die "Python >= 3.11 tidak ditemukan. Install dulu: sudo apt-get install python3 python3-pip (Debian/Ubuntu) | sudo dnf install python3 python3-pip (Fedora) | brew install python@3.12 (macOS)"
fi
say "python: $("$PY" --version) ($PY)"

# --- 2) download tarball & install --------------------------------------------------
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
say "mengunduh $TARBALL ..."
curl -fsSL "$TARBALL" | tar xz -C "$TMP" || die "gagal mengunduh/mengekstrak tarball"
SRC_DIR="$(find "$TMP" -mindepth 1 -maxdepth 1 -type d | head -n1)"
[ -n "$SRC_DIR" ] || die "tarball kosong / format tidak terduga"

BIN=""
if command -v pipx >/dev/null 2>&1; then
  say "menginstall lewat pipx ..."
  pipx install --force "$SRC_DIR"
  PIPX_BIN_DIR="$(pipx environment --value PIPX_BIN_DIR 2>/dev/null || echo "$HOME/.local/bin")"
  BIN="$PIPX_BIN_DIR/imgcli"
elif is_termux; then
  say "menginstall lewat pip (Termux — tanpa --user, sesuai konvensi Termux) ..."
  "$PY" -m pip install --upgrade "$SRC_DIR"
  BIN="$("$PY" -c 'import sysconfig; print(sysconfig.get_path("scripts"))')/imgcli"
else
  say "pipx tidak ada — menginstall lewat pip --user ..."
  say "(direkomendasikan install pipx dulu: '$PY' -m pip install --user pipx)"
  "$PY" -m pip install --user --upgrade "$SRC_DIR"
  BIN="$HOME/.local/bin/imgcli"
fi
[ -x "$BIN" ] || BIN="$(command -v imgcli || true)"
[ -n "$BIN" ] && [ -x "$BIN" ] || die "binary imgcli tidak ditemukan setelah instalasi (periksa PATH)"
say "terpasang: $BIN ($("$BIN" --version))"

# --- 3) chromium (dilewati di Termux / IMGCLI_NO_BROWSER=1) -------------------------
if is_termux; then
  warn "Termux/Android: mode web (Chromium) TIDAK tersedia — gunakan API resmi:"
  warn "    export GEMINI_API_KEY=...   lalu  imgcli --api \"a red apple\""
elif [ "${IMGCLI_NO_BROWSER:-0}" = "1" ]; then
  say "IMGCLI_NO_BROWSER=1 — melewati unduhan Chromium (mode --api tidak butuh browser)"
else
  say "menyiapkan Chromium (sekali saja, ±170MB) ..."
  "$BIN" setup
fi

# --- 4) PATH check & next steps ------------------------------------------------------
if ! echo ":$PATH:" | grep -q ":$(dirname "$BIN"):"; then
  warn "$(dirname "$BIN") belum ada di PATH — tambahkan ke ~/.bashrc:"
  warn "    export PATH=\"$(dirname "$BIN"):\$PATH\""
fi

say "selesai:"
if is_termux; then
  say "  export GEMINI_API_KEY=..."
  say "  imgcli --api \"a red apple, photorealistic\""
else
  say "  imgcli login    # login Google sekali (mode web gratis)"
  say "  imgcli \"a red apple, photorealistic\""
fi
say "  masalah/bug: $REPO/issues"
