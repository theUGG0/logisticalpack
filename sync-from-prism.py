#!/usr/bin/env python3
"""Sync changes from a Prism Launcher instance into this packwiz repo.

Copies *.pw.toml files from <instance>/<kind>/.index/ into <repo>/<kind>/, and
mirrors config-style directories verbatim. Then runs `packwiz refresh` to
rebuild index.toml.

Configuration via .env file in the repo root (see .env.example):
    INSTANCE  Path to the Prism instance's .minecraft folder.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

# --- Constants --------------------------------------------------------------

# Folders where Prism stores packwiz metadata under a `.index/` subdir.
METADATA_KINDS = ("mods", "resourcepacks", "shaderpacks")

# Folders to mirror verbatim (configs, scripts, datapacks).
VERBATIM_DIRS = ("config", "kubejs", "defaultconfigs")


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


def resolve_instance_path(env: dict[str, str]) -> Path:
    raw = env.get("INSTANCE") or os.environ.get("INSTANCE")
    if not raw:
        fail(
            "INSTANCE not set — create a .env file in the repo root with:\n"
            "       INSTANCE=/path/to/PrismLauncher/instances/MyPack/.minecraft"
        )
    return Path(os.path.expandvars(os.path.expanduser(raw)))


def repo_root() -> Path:
    return Path(__file__).resolve().parent


def fail(msg: str) -> "NoReturn":
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def run(cmd: list[str], cwd: Path | None = None, check: bool = True,
        capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=cwd, check=check,
        capture_output=capture, text=True,
    )


def check_branch_not_behind(repo: Path) -> None:
    """Refuse to run if the current branch is strictly behind origin."""
    try:
        run(["git", "fetch", "--quiet"], cwd=repo)
    except subprocess.CalledProcessError:
        # No remote, no upstream, offline — let it slide.
        return

    try:
        local = run(["git", "rev-parse", "@"], cwd=repo, capture=True).stdout.strip()
        upstream = run(["git", "rev-parse", "@{u}"], cwd=repo, capture=True).stdout.strip()
        base = run(["git", "merge-base", "@", "@{u}"], cwd=repo, capture=True).stdout.strip()
    except subprocess.CalledProcessError:
        # No upstream configured.
        return

    if local == base and local != upstream:
        fail("branch is behind origin — git pull first")


def sync_metadata(instance: Path, repo: Path) -> None:
    """Copy *.pw.toml from instance/<kind>/.index/ to repo/<kind>/, deleting stale entries."""
    for kind in METADATA_KINDS:
        src = instance / kind / ".index"
        dst = repo / kind
        if not src.is_dir():
            continue

        dst.mkdir(parents=True, exist_ok=True)

        # Delete stale .pw.toml files that no longer exist in the source.
        src_names = {p.name for p in src.iterdir() if p.suffix == ".toml" and p.name.endswith(".pw.toml")}
        for existing in dst.iterdir():
            if existing.is_file() and existing.name.endswith(".pw.toml") and existing.name not in src_names:
                existing.unlink()

        # Copy current .pw.toml files.
        for entry in src.iterdir():
            if entry.is_file() and entry.name.endswith(".pw.toml"):
                shutil.copy2(entry, dst / entry.name)

        print(f"  synced {kind}/ ({len(src_names)} entries)")


def sync_verbatim(instance: Path, repo: Path) -> None:
    """Mirror full directories from instance to repo."""
    for name in VERBATIM_DIRS:
        src = instance / name
        dst = repo / name
        if not src.is_dir():
            continue

        # Wipe and recopy. Simpler and more correct than incremental
        # for the small data sizes involved here.
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst, dirs_exist_ok=False)
        print(f"  mirrored {name}/")


def packwiz_refresh(repo: Path) -> None:
    if shutil.which("packwiz") is None:
        fail("'packwiz' not found in PATH — install from https://packwiz.infra.link/")
    run(["packwiz", "refresh"], cwd=repo)


# --- Main -------------------------------------------------------------------

def main() -> None:
    repo = repo_root()
    env = load_dotenv(repo)
    instance = resolve_instance_path(env)

    if not instance.is_dir():
        fail(f"instance not found at {instance}")
    if not (repo / ".git").exists():
        fail(f"{repo} is not a git repo")

    check_branch_not_behind(repo)

    print(f"instance: {instance}")
    print(f"repo:     {repo}")
    print()

    sync_metadata(instance, repo)
    sync_verbatim(instance, repo)
    packwiz_refresh(repo)

    print("\n\u2713 sync complete \u2014 review with 'git status' / 'git diff'")


if __name__ == "__main__":
    main()