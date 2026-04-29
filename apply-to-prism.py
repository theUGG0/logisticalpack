#!/usr/bin/env python3
"""Apply the current pack state into a Prism Launcher instance.

Runs packwiz-installer-bootstrap.jar against PACK_URL, which fetches the
current published pack and reconciles the instance to match it.

Configuration via .env file in the repo root (see .env.example):
    INSTANCE      Path to the Prism instance's .minecraft folder.
    PACK_URL      HTTP(S) URL of the pack.toml to install from (production).
    DEV_PACK_URL  URL to use with --dev (default: http://localhost:8080/pack.toml).

Usage:
    apply_to_prism.py            # use PACK_URL (production / Pages)
    apply_to_prism.py --dev      # use DEV_PACK_URL (with `packwiz serve`)
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

# --- Constants --------------------------------------------------------------

BOOTSTRAP_JAR = "packwiz-installer-bootstrap.jar"
DEFAULT_DEV_PACK_URL = "http://localhost:8080/pack.toml"

# Folders where Prism stores packwiz metadata under a `.index/` subdir. Edit this as needed
METADATA_KINDS = ("mods", "resourcepacks", "shaderpacks")


# --- Helpers ----------------------------------------------------------------

def load_dotenv(repo: Path) -> dict[str, str]:
    """Parse a minimal .env file. Supports KEY=value, # comments, blank lines,
    and optional surrounding single or double quotes on the value."""
    env_path = repo / ".env"
    if not env_path.is_file():
        return {}

    values: dict[str, str] = {}
    for lineno, raw in enumerate(env_path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            print(f"warning: .env line {lineno} ignored (no '='): {raw}", file=sys.stderr)
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[key] = value
    return values


def repo_root() -> Path:
    return Path(__file__).resolve().parent


def fail(msg: str) -> "NoReturn":
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def get_setting(env: dict[str, str], key: str) -> str | None:
    return env.get(key) or os.environ.get(key) or None


def resolve_instance_path(env: dict[str, str]) -> Path:
    raw = get_setting(env, "INSTANCE")
    if not raw:
        fail(
            "INSTANCE not set — create a .env file in the repo root with:\n"
            "       INSTANCE=/path/to/PrismLauncher/instances/MyPack/.minecraft"
        )
    return Path(os.path.expandvars(os.path.expanduser(raw)))


def resolve_pack_url(env: dict[str, str], dev: bool) -> str:
    if dev:
        return get_setting(env, "DEV_PACK_URL") or DEFAULT_DEV_PACK_URL
    url = get_setting(env, "PACK_URL")
    if not url:
        fail(
            "PACK_URL not set — add it to .env, or pass --dev for localhost.\n"
            "       PACK_URL=https://you.github.io/repo/pack.toml"
        )
    return url


# --- Main -------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dev",
        action="store_true",
        help="install from DEV_PACK_URL (default http://localhost:8080/pack.toml) "
             "instead of the production PACK_URL. Pair with `packwiz serve` "
             "running in the repo to test a feature branch.",
    )
    parser.add_argument(
        "--keep-stale",
        action="store_true",
        help="don't remove stale .pw.toml files when copying files from repo to Prism instance's .index directories"
    )
    args = parser.parse_args()

    repo = repo_root()
    env = load_dotenv(repo)

    instance = resolve_instance_path(env)
    pack_url = resolve_pack_url(env, args.dev)

    if not instance.is_dir():
        fail(f"instance not found at {instance}")

    bootstrap = instance / BOOTSTRAP_JAR
    if not bootstrap.is_file():
        fail(
            f"{BOOTSTRAP_JAR} missing from {instance}\n"
            f"       download from https://github.com/packwiz/packwiz-installer-bootstrap/releases"
        )

    if shutil.which("java") is None:
        fail("'java' not found in PATH")

    mode = "dev (localhost)" if args.dev else "production"
    print(f"mode:     {mode}")
    print(f"instance: {instance}")
    print(f"pack:     {pack_url}")
    print()

    # Run the bootstrapper from inside the instance dir, inheriting stdio so
    # the user sees its progress.
    result = subprocess.run(
        ["java", "-jar", BOOTSTRAP_JAR, pack_url],
        cwd=instance,
    )
    if result.returncode != 0:
        sys.exit(result.returncode)

    # Populate Prism's .index/ folders from the repo's metadata files.
    # The bootstrapper installs the jars but doesn't write Prism's metadata,
    # which means Prism's UI wouldn't recognize the mods and sync_from_prism.py
    # would falsely think they were uninstalled.
    print()
    populate_prism_index(repo, instance, args.keep_stale)
    print("\u2713 done")


def populate_prism_index(repo: Path, instance: Path, keep_stale: bool) -> None:
    for kind in METADATA_KINDS:
        src = repo / kind
        dst = instance / kind / ".index"
        if not src.is_dir():
            continue

        dst.mkdir(parents=True, exist_ok=True)

        # Mirror: dst should end up with exactly the .pw.toml files from src.
        repo_names = {
            p.name for p in src.iterdir()
            if p.is_file() and p.name.endswith(".pw.toml")
        }

        if not keep_stale:
            # Remove stale entries in dst.
            for existing in dst.iterdir():
                if existing.is_file() and existing.name.endswith(".pw.toml") \
                        and existing.name not in repo_names:
                    existing.unlink()

        # Copy current entries from repo to dst.
        for name in repo_names:
            shutil.copy2(src / name, dst / name)

        if repo_names:
            print(f"  populated {kind}/.index/ ({len(repo_names)} entries)")


if __name__ == "__main__":
    main()