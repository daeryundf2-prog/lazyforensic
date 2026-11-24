#!/bin/sh
# LazyForensic installer for macOS / Linux.
#
# One-line install (Terminal, normal user, no sudo). The installer attached to a release installs
# exactly that release:
#   curl -fsSL https://github.com/daeryundf2-prog/lazyforensic/releases/latest/download/install.sh | sh
#
# Offline install: copy an extracted release folder to the machine and run
#   sh install.sh
# inside it.
#
# What it does:
#   1. Downloads the release archive lazyforensic-<tag>.tar.gz and checks its SHA-256 against the
#      release's SHA256SUMS and (release installer) the value stamped below. The release lookup must
#      answer with the requested tag. Any mismatch or a failed release lookup stops before anything is
#      changed. There is no fallback to the main branch.
#   2. Finds Python 3.10+. On macOS with Homebrew and no suitable Python, runs
#      `brew install python@3.12`.
#   3. Copies LazyForensic to ~/.lazyforensic/app (replaces an older copy).
#   4. Creates the venv ~/.lfenv and installs Pillow from requirements-install.txt
#      (pip --require-hashes). Without internet this step is skipped; the report still works.
#   5. Links ~/.local/bin/lazyforensic, adds ~/.local/bin to PATH in your shell profile and writes
#      ~/.lazyforensic/install-receipt.json (archive tag, name and SHA-256).
#
# Environment overrides:
#   LAZYFORENSIC_REF          release tag to install (default: the release this installer belongs to,
#                             else the latest release). A release installer installs another tag only
#                             together with LAZYFORENSIC_SHA256; otherwise use that release's installer.
#   LAZYFORENSIC_SHA256       expected SHA-256 of the release archive (an extra pin you got elsewhere)
#   LAZYFORENSIC_SOURCE       local folder to install from instead of downloading (recorded as unverified)
#   LAZYFORENSIC_RELEASE_API  GitHub API repository URL (mirrors and tests)
set -eu

REPO="daeryundf2-prog/lazyforensic"
# Stamped by .github/workflows/release-assets.yml into the installer attached to a release.
RELEASE_TAG=""
RELEASE_ARCHIVE_SHA256=""
API="${LAZYFORENSIC_RELEASE_API:-https://api.github.com/repos/$REPO}"
INSTALL_ROOT="$HOME/.lazyforensic"
APP_DIR="$INSTALL_ROOT/app"
VENV_DIR="$HOME/.lfenv"
LINK_DIR="$HOME/.local/bin"

say() { printf '[LazyForensic] %s\n' "$*"; }
die() { say "$*" >&2; exit 1; }

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
  else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

asset_url() {  # asset_url <release.json> <asset name>
  grep -o '"browser_download_url": *"[^"]*"' "$1" | sed 's/.*"\([^"]*\)"$/\1/' | while read -r url; do
    case "$url" in */"$2") printf '%s\n' "$url"; break ;; esac
  done
}

fetch_release() {  # sets SRC and SOURCE_* from a verified release archive
  ref="${LAZYFORENSIC_REF:-$RELEASE_TAG}"
  # A release installer carries the SHA-256 of its own archive only. Another tag needs its own installer or a pin.
  if [ -n "$RELEASE_TAG" ] && [ "$ref" != "$RELEASE_TAG" ] && [ -z "${LAZYFORENSIC_SHA256:-}" ]; then
    die "This installer is for release $RELEASE_TAG and has no stamped SHA-256 for $ref. Use the install.sh attached to release $ref, or set LAZYFORENSIC_SHA256 to its archive's SHA-256. Nothing was changed."
  fi
  if [ -n "$ref" ]; then url="$API/releases/tags/$ref"; else url="$API/releases/latest"; fi
  curl -fsSL "$url" -o "$TMP/release.json" || die "Release lookup failed ($url). Nothing was changed. The installer does not fall back to the main branch; retry, or install from a verified local copy with LAZYFORENSIC_SOURCE."
  TAG="$(sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' "$TMP/release.json" | head -n 1)"
  [ -n "$TAG" ] || die "Release lookup returned no tag ($url). Nothing was changed."
  # The answer must be the release that was asked for, before any asset is looked at.
  [ -z "$ref" ] || [ "$TAG" = "$ref" ] || die "Release lookup for $ref returned release $TAG. Nothing was changed."
  NAME="lazyforensic-$TAG.tar.gz"
  archive_url="$(asset_url "$TMP/release.json" "$NAME")"
  sums_url="$(asset_url "$TMP/release.json" SHA256SUMS)"
  [ -n "$archive_url" ] && [ -n "$sums_url" ] || die "Release $TAG has no $NAME with SHA256SUMS, so it cannot be verified. Nothing was changed."
  say "Downloading $NAME ($REPO $TAG)..."
  curl -fsSL "$archive_url" -o "$TMP/$NAME"
  curl -fsSL "$sums_url" -o "$TMP/SHA256SUMS"
  actual="$(sha256_of "$TMP/$NAME")"
  listed="$(awk -v n="$NAME" '($2 == n || $2 == "*" n) && $1 ~ /^[0-9A-Fa-f]+$/ && length($1) == 64 { print tolower($1) }' "$TMP/SHA256SUMS")"
  [ "$(printf '%s\n' "$listed" | grep -c .)" = 1 ] || die "SHA256SUMS of $TAG does not list $NAME exactly once. Nothing was changed."
  SOURCE_CHECKS="SHA256SUMS"
  check_pin() {  # check_pin <source> <expected>
    expected="$(printf '%s' "$2" | tr 'A-F' 'a-f')"
    [ "$expected" = "$actual" ] || die "SHA-256 of $NAME does not match $1: expected $expected, got $actual. Nothing was changed."
  }
  check_pin SHA256SUMS "$listed"
  if [ -n "$RELEASE_TAG" ] && [ "$TAG" = "$RELEASE_TAG" ]; then
    [ -n "$RELEASE_ARCHIVE_SHA256" ] || die "This release installer has no stamped SHA-256 for $NAME. Nothing was changed."
    check_pin "release installer" "$RELEASE_ARCHIVE_SHA256"; SOURCE_CHECKS="$SOURCE_CHECKS,release installer"
  fi
  if [ -n "${LAZYFORENSIC_SHA256:-}" ]; then
    check_pin LAZYFORENSIC_SHA256 "$LAZYFORENSIC_SHA256"; SOURCE_CHECKS="$SOURCE_CHECKS,LAZYFORENSIC_SHA256"
  fi
  say "Verified $NAME SHA-256 $actual ($SOURCE_CHECKS)"
  mkdir "$TMP/extract"
  tar -xzf "$TMP/$NAME" -C "$TMP/extract"
  SRC="$(find "$TMP/extract" -mindepth 2 -maxdepth 3 -path '*/scripts/report_draft.py' | head -n 1)"
  [ -n "$SRC" ] || die "Downloaded archive does not contain LazyForensic. Nothing was changed."
  SRC="$(dirname "$(dirname "$SRC")")"
  SOURCE_KIND="release"; SOURCE_TAG="$TAG"; SOURCE_ARCHIVE="$NAME"; SOURCE_SHA256="$actual"
}

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

# 1. Program files: verify before anything on this machine changes.
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT INT TERM
SELF_DIR=""
case "$0" in
  */install.sh|install.sh) SELF_DIR="$(cd "$(dirname "$0")" && pwd)" ;;
esac
SOURCE_KIND="local"; SOURCE_TAG=""; SOURCE_ARCHIVE=""; SOURCE_SHA256=""; SOURCE_CHECKS=""
if [ -n "${LAZYFORENSIC_SOURCE:-}" ]; then
  SRC="$(cd "$LAZYFORENSIC_SOURCE" && pwd)"
elif [ -n "$SELF_DIR" ] && [ -f "$SELF_DIR/scripts/report_draft.py" ]; then
  SRC="$SELF_DIR"
else
  fetch_release
fi

# 2. Python
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

# 3. Replace the app only after the new copy is complete.
if [ "$SRC" != "$APP_DIR" ]; then
  (cd "$SRC" && tar --exclude=.git -cf "$TMP/app.tar" .)
  mkdir "$TMP/app"
  (cd "$TMP/app" && tar -xf "$TMP/app.tar")
  mkdir -p "$INSTALL_ROOT"
  rm -rf "$INSTALL_ROOT/app.new"
  mv "$TMP/app" "$INSTALL_ROOT/app.new"
  rm -rf "$APP_DIR"
  mv "$INSTALL_ROOT/app.new" "$APP_DIR"
fi
chmod +x "$APP_DIR/bin/lazyforensic"
say "Installed to $APP_DIR"

# 4. venv + Pillow (hash-pinned)
if [ ! -x "$VENV_DIR/bin/python" ]; then
  say "Creating Python environment $VENV_DIR"
  "$PY" -m venv "$VENV_DIR"
fi
REQUIREMENTS="$APP_DIR/requirements-install.txt"
if ! "$VENV_DIR/bin/python" -m pip install --disable-pip-version-check -q \
    --require-hashes --only-binary=:all: -r "$REQUIREMENTS"; then
  say "WARNING: Pillow install failed (offline?). Photo EXIF checks will be skipped; reports still work."
fi

# 5. Command on PATH
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

# Install receipt: what was installed, from which verified archive.
LF_RECEIPT="$INSTALL_ROOT/install-receipt.json" LF_KIND="$SOURCE_KIND" LF_TAG="$SOURCE_TAG" \
LF_ARCHIVE="$SOURCE_ARCHIVE" LF_SHA256="$SOURCE_SHA256" LF_CHECKS="$SOURCE_CHECKS" LF_SRC="$SRC" \
LF_INSTALLER="$RELEASE_TAG" LF_REQUIREMENTS="$REQUIREMENTS" "$VENV_DIR/bin/python" - <<'EOF'
import datetime, hashlib, json, os, sys
e = os.environ
release = e["LF_KIND"] == "release"
try:
    import PIL
    pillow = PIL.__version__
except ImportError:
    pillow = None
source = ({"kind": "release", "tag": e["LF_TAG"], "archive": e["LF_ARCHIVE"], "sha256": e["LF_SHA256"],
           "verified_against": e["LF_CHECKS"].split(",")} if release else
          {"kind": "local", "path": e["LF_SRC"], "sha256": None, "verified_against": []})
try:
    with open(e["LF_REQUIREMENTS"], "rb") as f:
        requirements = hashlib.sha256(f.read()).hexdigest()
except FileNotFoundError:  # a local source copy older than the hash-pinned requirements
    requirements = None
receipt = {"installed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
           "installer": f"release {e['LF_INSTALLER']}" if e["LF_INSTALLER"] else "repository",
           "source": source, "python": "%d.%d" % sys.version_info[:2],
           "requirements_sha256": requirements, "pillow": pillow}
with open(e["LF_RECEIPT"], "w", encoding="utf-8") as f:
    json.dump(receipt, f, ensure_ascii=False, indent=2)
EOF
say "Done. Open a NEW Terminal window and run:"
say "    lazyforensic report /path/to/evidence-folder"
