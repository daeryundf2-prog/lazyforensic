#!/bin/sh
# LazyForensic installer for macOS / Linux.
#
# One-line install (Terminal, normal user, no sudo):
#   curl -fsSL https://raw.githubusercontent.com/daeryundf2-prog/lazyforensic/main/install.sh | sh
#
# Offline install: copy an extracted release folder to the machine and run
#   sh install.sh
# inside it.
#
# What it does:
#   1. Finds Python 3.10+. On macOS with Homebrew and no suitable Python, runs
#      `brew install python@3.12`.
#   2. Copies LazyForensic to ~/.lazyforensic/app (replaces an older copy).
#   3. Creates the venv ~/.lfenv and installs Pillow (photo EXIF checks).
#      Without internet this step is skipped; the report still works.
#   4. Links ~/.local/bin/lazyforensic and adds ~/.local/bin to PATH in your shell profile.
#
# Environment overrides:
#   LAZYFORENSIC_REF     tag or branch to download (default: latest release, else main)
#   LAZYFORENSIC_SOURCE  local folder to install from instead of downloading
set -eu

REPO="daeryundf2-prog/lazyforensic"
INSTALL_ROOT="$HOME/.lazyforensic"
APP_DIR="$INSTALL_ROOT/app"
VENV_DIR="$HOME/.lfenv"
LINK_DIR="$HOME/.local/bin"

say() { printf '[LazyForensic] %s\n' "$*"; }

find_python() {
  for c in python3.13 python3.12 python3.11 python3.10 python3 python; do
    if command -v "$c" >/dev/null 2>&1 && \
       "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
      command -v "$c"
      return 0
    fi
  done
  return 1
}

# 1. Python
PY="$(find_python || true)"
if [ -z "$PY" ] && [ "$(uname -s)" = "Darwin" ] && command -v brew >/dev/null 2>&1; then
  say "Python 3.10+ not found. Installing python@3.12 with Homebrew..."
  brew install python@3.12
  PY="$(find_python || true)"
fi
if [ -z "$PY" ]; then
  say "Python 3.10+ is required."
  say "  macOS: install from https://www.python.org/downloads/macos/ then run this again."
  say "  Linux: install python3 (3.10+) and python3-venv with your package manager."
  exit 1
fi
say "Python $("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])'): $PY"

# 2. Program files
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT INT TERM
SELF_DIR=""
case "$0" in
  */install.sh|install.sh) SELF_DIR="$(cd "$(dirname "$0")" && pwd)" ;;
esac
if [ -n "${LAZYFORENSIC_SOURCE:-}" ]; then
  SRC="$(cd "$LAZYFORENSIC_SOURCE" && pwd)"
elif [ -n "$SELF_DIR" ] && [ -f "$SELF_DIR/scripts/report_draft.py" ]; then
  SRC="$SELF_DIR"
else
  REF="${LAZYFORENSIC_REF:-}"
  if [ -z "$REF" ]; then
    REF="$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" 2>/dev/null \
      | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -n 1)"
    [ -n "$REF" ] || REF="main"
  fi
  say "Downloading $REPO ($REF)..."
  curl -fsSL "https://github.com/$REPO/archive/$REF.tar.gz" -o "$TMP/lf.tar.gz"
  tar -xzf "$TMP/lf.tar.gz" -C "$TMP"
  SRC="$(find "$TMP" -mindepth 2 -maxdepth 3 -path '*/scripts/report_draft.py' | head -n 1)"
  [ -n "$SRC" ] || { say "Downloaded archive does not contain LazyForensic."; exit 1; }
  SRC="$(dirname "$(dirname "$SRC")")"
fi
if [ "$SRC" != "$APP_DIR" ]; then
  (cd "$SRC" && tar --exclude=.git -cf "$TMP/app.tar" .)
  mkdir "$TMP/app"
  (cd "$TMP/app" && tar -xf "$TMP/app.tar")
  mkdir -p "$INSTALL_ROOT"
  rm -rf "$APP_DIR"
  mv "$TMP/app" "$APP_DIR"
fi
chmod +x "$APP_DIR/bin/lazyforensic"
say "Installed to $APP_DIR"

# 3. venv + Pillow
if [ ! -x "$VENV_DIR/bin/python" ]; then
  say "Creating Python environment $VENV_DIR"
  "$PY" -m venv "$VENV_DIR"
fi
if ! "$VENV_DIR/bin/python" -m pip install --disable-pip-version-check -q "pillow>=10.0,<12"; then
  say "WARNING: Pillow install failed (offline?). Photo EXIF checks will be skipped; reports still work."
fi

# 4. Command on PATH
mkdir -p "$LINK_DIR"
ln -sf "$APP_DIR/bin/lazyforensic" "$LINK_DIR/lazyforensic"
case ":$PATH:" in
  *":$LINK_DIR:"*) ;;
  *)
    case "${SHELL:-}" in
      */bash) active_rc="$HOME/.bashrc" ;;
      */zsh) active_rc="$HOME/.zshrc" ;;
      *) active_rc="$HOME/.profile" ;;
    esac
    for rc in "$HOME/.zshrc" "$HOME/.bashrc" "$HOME/.bash_profile" "$HOME/.bash_login" "$HOME/.profile"; do
      if [ -f "$rc" ] || [ "$rc" = "$active_rc" ] || [ "$rc" = "$HOME/.profile" ]; then
        if ! grep -qs 'LazyForensic PATH' "$rc"; then
          printf '\n# LazyForensic PATH\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$rc"
          say "Added ~/.local/bin to PATH in $rc"
        fi
      fi
    done
    ;;
esac

"$LINK_DIR/lazyforensic" report --help >/dev/null
say "Done. Open a NEW Terminal window and run:"
say "    lazyforensic report /path/to/evidence-folder"
