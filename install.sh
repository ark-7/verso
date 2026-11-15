#!/usr/bin/env bash
# Install Verso from a GitHub Release. macOS and Linux only.
#
#   curl -fsSL https://github.com/ark-7/verso/releases/latest/download/install.sh | bash
#
# Requires Python 3.11 or newer. The script downloads the release wheel,
# verifies its SHA-256 checksum, and installs it with uv, pipx, or pip.

set -euo pipefail

REPO="ark-7/verso"
API="https://api.github.com/repos/${REPO}"

VERSION=""
INSTALLER="auto"
WITH=""
DRY_RUN=0

usage() {
    cat <<'EOF'
Usage: install.sh [options]

Options:
  --version vX.Y.Z   Install a specific release (default: latest)
  --installer NAME   auto, uv, pipx, or pip (default: auto)
  --with EXTRA       Also install provider SDKs: anthropic, openai, or all
  --dry-run          Resolve and verify the release without installing
  -h, --help         Show this help
EOF
}

die() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

info() {
    printf '%s\n' "$*"
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --version)
            [ "$#" -ge 2 ] || die "--version needs a value"
            VERSION="$2"
            shift 2
            ;;
        --version=*)
            VERSION="${1#*=}"
            shift
            ;;
        --installer)
            [ "$#" -ge 2 ] || die "--installer needs a value"
            INSTALLER="$2"
            shift 2
            ;;
        --installer=*)
            INSTALLER="${1#*=}"
            shift
            ;;
        --with)
            [ "$#" -ge 2 ] || die "--with needs a value"
            WITH="$2"
            shift 2
            ;;
        --with=*)
            WITH="${1#*=}"
            shift
            ;;
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        -h | --help)
            usage
            exit 0
            ;;
        *)
            die "unknown option: $1 (try --help)"
            ;;
    esac
done

case "$INSTALLER" in
    auto | uv | pipx | pip) ;;
    *) die "--installer must be auto, uv, pipx, or pip" ;;
esac

case "$WITH" in
    "" | anthropic | openai | all) ;;
    *) die "--with must be anthropic, openai, or all" ;;
esac

command -v curl >/dev/null 2>&1 || die "curl is required to download the release"

python_is_usable() {
    "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' \
        >/dev/null 2>&1
}

PYTHON_BIN=""
if [ -n "${VIRTUAL_ENV:-}" ]; then
    if [ -x "${VIRTUAL_ENV}/bin/python" ] \
        && python_is_usable "${VIRTUAL_ENV}/bin/python"; then
        PYTHON_BIN="${VIRTUAL_ENV}/bin/python"
    else
        die "the active virtualenv uses Python older than 3.11; create a new virtualenv with Python 3.11 or newer"
    fi
else
    for candidate in python3.13 python3.12 python3.11 python3 python; do
        if command -v "$candidate" >/dev/null 2>&1; then
            resolved="$(command -v "$candidate")"
            if python_is_usable "$resolved"; then
                PYTHON_BIN="$resolved"
                break
            fi
        fi
    done
fi

if [ -z "$PYTHON_BIN" ]; then
    die "Python 3.11 or newer is required. Install it and rerun this script. \
macOS: brew install python@3.12. Debian/Ubuntu: sudo apt install python3.11. \
Or install uv: https://docs.astral.sh/uv/getting-started/installation/"
fi

api_get() {
    curl -fsSL --retry 3 -H "Accept: application/vnd.github+json" "$1"
}

release_field() {
    "$PYTHON_BIN" -I -c \
        'import json, sys; print(json.load(sys.stdin).get(sys.argv[1], ""))' "$1"
}

if [ -z "$VERSION" ]; then
    release_json="$(api_get "${API}/releases/latest")" \
        || die "could not find the latest Verso release (the GitHub API may be rate-limited; try again later)"
    TAG="$(printf '%s' "$release_json" | release_field tag_name)"
else
    TAG="$VERSION"
    case "$TAG" in
        v*) ;;
        *) TAG="v${TAG}" ;;
    esac
    release_json="$(api_get "${API}/releases/tags/${TAG}")" \
        || die "release ${TAG} was not found, or the GitHub API is rate-limited"
fi

[ -n "$TAG" ] || die "could not determine the release tag"

asset_lines="$(
    printf '%s' "$release_json" | "$PYTHON_BIN" -I -c '
import json
import re
import sys

release = json.load(sys.stdin)
wheel = ""
checksums = ""
for asset in release.get("assets", []):
    name = asset.get("name", "")
    if re.fullmatch(r"verso_cli-.+-py3-none-any\.whl", name):
        wheel = asset.get("browser_download_url", "")
    elif name == "SHA256SUMS":
        checksums = asset.get("browser_download_url", "")
print(wheel)
print(checksums)
'
)"
wheel_url="$(printf '%s\n' "$asset_lines" | sed -n '1p')"
checksums_url="$(printf '%s\n' "$asset_lines" | sed -n '2p')"

[ -n "$wheel_url" ] || die "release ${TAG} does not contain a verso_cli wheel"
[ -n "$checksums_url" ] || die "release ${TAG} does not contain SHA256SUMS"

wheel_name="${wheel_url##*/}"
wheel_version="${wheel_name#verso_cli-}"
wheel_version="${wheel_version%-py3-none-any.whl}"

select_installer() {
    if [ "$INSTALLER" != "auto" ]; then
        printf '%s\n' "$INSTALLER"
        return
    fi
    if command -v uv >/dev/null 2>&1; then
        printf 'uv\n'
    elif command -v pipx >/dev/null 2>&1; then
        printf 'pipx\n'
    else
        printf 'pip\n'
    fi
}

SELECTED_INSTALLER="$(select_installer)"

case "$SELECTED_INSTALLER" in
    uv)
        command -v uv >/dev/null 2>&1 \
            || die "uv is not installed: https://docs.astral.sh/uv/getting-started/installation/"
        ;;
    pipx)
        command -v pipx >/dev/null 2>&1 \
            || die "pipx is not installed: https://pipx.pypa.io/stable/installation/"
        ;;
    pip)
        "$PYTHON_BIN" -m pip --version >/dev/null 2>&1 \
            || die "pip is not available for ${PYTHON_BIN}"
        ;;
esac

TMP_DIR="$(mktemp -d)"
cleanup() {
    rm -rf "$TMP_DIR"
}
trap cleanup EXIT

curl -fsSL --retry 3 "$checksums_url" -o "${TMP_DIR}/SHA256SUMS" \
    || die "could not download SHA256SUMS for ${TAG}"

expected="$(awk -v name="$wheel_name" '
    {
        file = $2
        sub(/^\*/, "", file)
        sub(/\r$/, "", file)
        if (file == name) {
            print $1
            exit
        }
    }
' "${TMP_DIR}/SHA256SUMS")"

[ -n "$expected" ] || die "SHA256SUMS does not list ${wheel_name}"
expected="$(printf '%s' "$expected" | tr 'A-F' 'a-f')"

if [ "$DRY_RUN" -eq 1 ]; then
    info "Verso installer dry run"
    info "  tag:       ${TAG}"
    info "  wheel:     ${wheel_url}"
    info "  checksums: ${checksums_url}"
    info "  sha256:    ${expected}"
    info "  python:    ${PYTHON_BIN}"
    info "  installer: ${SELECTED_INSTALLER}"
    exit 0
fi

curl -fsSL --retry 3 "$wheel_url" -o "${TMP_DIR}/${wheel_name}" \
    || die "could not download ${wheel_name}"

actual=""
if command -v sha256sum >/dev/null 2>&1; then
    actual="$(sha256sum "${TMP_DIR}/${wheel_name}" | awk '{print $1}')"
elif command -v shasum >/dev/null 2>&1; then
    actual="$(shasum -a 256 "${TMP_DIR}/${wheel_name}" | awk '{print $1}')"
else
    actual="$(
        "$PYTHON_BIN" -I -c \
            'import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' \
            "${TMP_DIR}/${wheel_name}"
    )"
fi
actual="$(printf '%s' "$actual" | tr 'A-F' 'a-f')"

[ "$actual" = "$expected" ] \
    || die "checksum mismatch for ${wheel_name} (expected ${expected}, got ${actual})"

install_with_uv() {
    uv_args=(tool install --force)
    if [ "$WITH" = "all" ]; then
        uv_args+=(--with anthropic --with openai)
    elif [ -n "$WITH" ]; then
        uv_args+=(--with "$WITH")
    fi
    uv "${uv_args[@]}" "${TMP_DIR}/${wheel_name}"
}

install_with_pipx() {
    pipx install --force "${TMP_DIR}/${wheel_name}"
    if [ "$WITH" = "all" ]; then
        pipx inject verso-cli anthropic openai
    elif [ -n "$WITH" ]; then
        pipx inject verso-cli "$WITH"
    fi
}

install_with_pip() {
    if [ -z "${VIRTUAL_ENV:-}" ]; then
        if "$PYTHON_BIN" -c '
import pathlib
import sysconfig

marker = pathlib.Path(sysconfig.get_path("stdlib")) / "EXTERNALLY-MANAGED"
raise SystemExit(0 if marker.exists() else 1)
'; then
            die "this Python is externally managed (PEP 668). Install uv or pipx, \
or create and activate a virtualenv, then rerun this script"
        fi
        "$PYTHON_BIN" -m pip install --user --upgrade "${TMP_DIR}/${wheel_name}"
        if [ "$WITH" = "all" ]; then
            "$PYTHON_BIN" -m pip install --user anthropic openai
        elif [ -n "$WITH" ]; then
            "$PYTHON_BIN" -m pip install --user "$WITH"
        fi
    else
        "$PYTHON_BIN" -m pip install --upgrade "${TMP_DIR}/${wheel_name}"
        if [ "$WITH" = "all" ]; then
            "$PYTHON_BIN" -m pip install anthropic openai
        elif [ -n "$WITH" ]; then
            "$PYTHON_BIN" -m pip install "$WITH"
        fi
    fi
}

case "$SELECTED_INSTALLER" in
    uv) install_with_uv ;;
    pipx) install_with_pipx ;;
    pip) install_with_pip ;;
esac

hash -r 2>/dev/null || true
expected_output="verso ${wheel_version}"

if [ "$SELECTED_INSTALLER" = "pip" ]; then
    output="$("$PYTHON_BIN" -m verso --version 2>&1 || true)"
    [ "$output" = "$expected_output" ] \
        || die "installed verso reported '${output}', expected '${expected_output}'"
    info "Installed ${output}"
    if ! command -v verso >/dev/null 2>&1; then
        info "The 'verso' command is not on PATH yet. Add your user scripts directory:"
        "$PYTHON_BIN" -c '
import site
import sys
from pathlib import Path

suffix = "Scripts" if sys.platform == "win32" else "bin"
print(f"  {Path(site.USER_BASE) / suffix}")
' || true
    fi
    exit 0
fi

if command -v verso >/dev/null 2>&1; then
    output="$(verso --version 2>&1 || true)"
    [ "$output" = "$expected_output" ] \
        || die "installed verso reported '${output}', expected '${expected_output}'"
    info "Installed ${output}"
    exit 0
fi

if [ "$SELECTED_INSTALLER" = "uv" ]; then
    info "verso was installed with uv but is not on PATH yet."
    info "Run: uv tool update-shell"
else
    info "verso was installed with pipx but is not on PATH yet."
    info "Run: pipx ensurepath"
fi

info "Open a new terminal, then run: verso --version"
