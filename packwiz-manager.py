#!/usr/bin/env python3
"""Packwiz Manager — local web GUI for managing this packwiz modpack.

Run from the pack repo's root:

    python3 packwiz-manager.py [--port N] [--no-browser]      (Linux / macOS)
    py packwiz-manager.py [--port N] [--no-browser]           (Windows)

Stdlib only (requires Python 3.11+). No pip, no flatpak, no system packages.

What it does:
  - shows the state of repo / Prism / server side-by-side
  - detects mismatches: missing .pw.toml, duplicates, empty CF download URLs
  - looks up unknown jars on Modrinth + CurseForge by hash
  - syncs Local↔Prism, Local→Server, GitHub branch→Prism
  - per-file diff for both .pw.toml and configs, with merge direction picker
  - asks for confirmation on every destructive action

Per-user configuration lives in `.env` (gitignored). All paths and tokens —
INSTANCE, PACK_URL, CRAFTY_*, CURSEFORGE_API_KEY, PACKWIZ_BIN, JAVA_BIN —
can be set there or via the in-app Settings tab. See `.env.example` for the
full list. Nothing about your specific machine is hardcoded in this script.

Cross-platform notes:
  - Windows: works without WSL. `packwiz` and `java` discovered via PATH; if
    you installed them somewhere unusual, set PACKWIZ_BIN / JAVA_BIN.
  - macOS: same, plus /opt/homebrew/bin is searched.
  - Linux: ~/.local/bin, ~/go/bin, /usr/local/bin, /usr/bin searched.
  - The Local→Server flow uses rsync + ssh and the existing
    sync-prism-to-crafty-server.py script. That script imports `fcntl` so
    it's Linux/macOS only — Windows users should leave CRAFTY_* unset to
    keep that card disabled, or run deploys from a *nix box.
"""

from __future__ import annotations

import argparse
import hashlib
import http.server
import io
import json
import os
import queue
import re
import shutil
import socket
import socketserver
import ssl
import subprocess
import sys
import threading
import time
import tomllib
import traceback
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError


# --- paths / config ---------------------------------------------------------

REPO = Path(__file__).resolve().parent
ENV_FILE = REPO / ".env"
GITHUB_DEFAULT_BRANCH = "main"
DEFAULT_PORT_RANGE = (51800, 51900)

METADATA_KINDS = ("mods", "resourcepacks", "shaderpacks")
VERBATIM_DIRS = ("config", "kubejs", "defaultconfigs")

MODRINTH_API = "https://api.modrinth.com/v2"
CURSEFORGE_API = "https://api.curseforge.com/v1"
USER_AGENT = "logisticalpack-manager/1.0 (+https://github.com/theUGG0/logisticalpack)"


# --- env loader -------------------------------------------------------------

def load_dotenv(path: Path = ENV_FILE) -> dict[str, str]:
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
            v = v[1:-1]
        out[k.strip()] = v
    return out


def save_dotenv(updates: dict[str, str], path: Path = ENV_FILE) -> None:
    """Update .env in place, preserving order/comments. Adds missing keys at end."""
    existing_lines: list[str] = []
    if path.is_file():
        existing_lines = path.read_text(encoding="utf-8").splitlines()

    seen_keys: set[str] = set()
    new_lines: list[str] = []
    for line in existing_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            new_lines.append(line)
            continue
        key = stripped.partition("=")[0].strip()
        if key in updates:
            new_lines.append(f"{key}={updates[key]}")
            seen_keys.add(key)
        else:
            new_lines.append(line)

    for key, value in updates.items():
        if key not in seen_keys:
            new_lines.append(f"{key}={value}")

    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def truthy(s: str | None) -> bool:
    return (s or "").strip().lower() in {"1", "true", "yes", "on"}


# --- binary discovery -------------------------------------------------------

def find_binary(name: str, env: dict[str, str] | None = None,
                extra_paths: Iterable[str] = ()) -> str | None:
    """Find an executable. Honours per-binary overrides like PACKWIZ_BIN /
    JAVA_BIN from .env, then falls back to PATH lookup, then to common
    per-OS install locations. Cross-platform (Linux / macOS / Windows)."""
    # 1. Explicit override via .env or environment
    override_key = f"{name.upper().replace('-', '_')}_BIN"
    override = ""
    if env:
        override = env.get(override_key, "")
    if not override:
        override = os.environ.get(override_key, "")
    if override:
        op = Path(os.path.expanduser(override))
        if op.is_file() and (sys.platform == "win32" or os.access(op, os.X_OK)):
            return str(op)

    # 2. PATH lookup (handles .exe / PATHEXT on Windows automatically)
    p = shutil.which(name)
    if p:
        return p

    # 3. Common per-OS install locations
    home = Path.home()
    candidates: list[Path] = []
    if sys.platform == "win32":
        exe = name if name.endswith(".exe") else name + ".exe"
        candidates += [
            home / "go" / "bin" / exe,
            home / "scoop" / "shims" / exe,
            home / ".cargo" / "bin" / exe,
            home / "AppData" / "Local" / "Programs" / name / exe,
            Path(rf"C:\Program Files\{name}\{exe}"),
            Path(rf"C:\Program Files (x86)\{name}\{exe}"),
        ]
    elif sys.platform == "darwin":
        candidates += [
            home / ".local" / "bin" / name,
            home / "go" / "bin" / name,
            Path(f"/usr/local/bin/{name}"),
            Path(f"/opt/homebrew/bin/{name}"),
        ]
    else:  # linux / *bsd
        candidates += [
            home / ".local" / "bin" / name,
            home / "go" / "bin" / name,
            Path(f"/usr/local/bin/{name}"),
            Path(f"/usr/bin/{name}"),
        ]
    candidates.extend(Path(p) / name for p in extra_paths)
    for c in candidates:
        if c.is_file() and (sys.platform == "win32" or os.access(c, os.X_OK)):
            return str(c)
    return None


def github_remote_slug(repo: Path = REPO) -> str | None:
    """Return 'owner/name' of origin if it's a GitHub remote."""
    try:
        r = subprocess.run(
            ["git", "-C", str(repo), "remote", "get-url", "origin"],
            capture_output=True, text=True, check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    url = r.stdout.strip()
    m = re.search(r"github\.com[:/]([^/]+/[^/.]+)", url)
    return m.group(1) if m else None


# --- TOML parser/writer (packwiz dialect) -----------------------------------

def parse_pw_toml(path: Path) -> dict[str, Any]:
    """Parse a packwiz .pw.toml file (TOML). Returns {} if unreadable."""
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except (tomllib.TOMLDecodeError, OSError):
        return {}


def _toml_escape_str(s: str) -> str:
    return "'" + s.replace("'", "''") + "'" if "\n" not in s and "'" not in s else \
           '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return str(v)
    if isinstance(v, str):
        return _toml_escape_str(v)
    if isinstance(v, list):
        return "[ " + ", ".join(_toml_value(x) for x in v) + " ]"
    raise TypeError(f"unsupported TOML value: {type(v).__name__}")


def write_pw_toml(path: Path, data: dict[str, Any]) -> None:
    """Write a .pw.toml in the packwiz dialect.

    Top-level scalars first (sorted by canonical packwiz order, others alpha),
    then [download], then [update.<provider>], then any other tables, then
    arrays-of-tables (e.g. [[x-prismlauncher-dependencies]]) last.
    """
    canonical_order = [
        "filename", "name", "side",
        "x-prismlauncher-dependencies", "x-prismlauncher-loaders",
        "x-prismlauncher-mc-versions", "x-prismlauncher-release-type",
        "x-prismlauncher-version-number",
    ]

    def order_key(k: str) -> tuple[int, str]:
        try:
            return (canonical_order.index(k), k)
        except ValueError:
            return (len(canonical_order), k)

    out: list[str] = []
    scalars: dict[str, Any] = {}
    tables: dict[str, dict[str, Any]] = {}
    arrays_of_tables: dict[str, list[dict[str, Any]]] = {}

    for k, v in data.items():
        if isinstance(v, dict):
            tables[k] = v
        elif isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
            arrays_of_tables[k] = v
        else:
            scalars[k] = v

    # x-prismlauncher-dependencies can be either an array of strings (rare) or
    # an array of tables ([[x-prismlauncher-dependencies]]). The latter appears
    # under arrays_of_tables already; the former stays in scalars.
    for k in sorted(scalars, key=order_key):
        out.append(f"{k} = {_toml_value(scalars[k])}")

    # [download] first, then [update.*], then anything else
    table_order = []
    if "download" in tables:
        table_order.append("download")
    if "update" in tables:
        table_order.append("update")
    table_order.extend(k for k in tables if k not in ("download", "update"))

    for tk in table_order:
        sub = tables[tk]
        # nested {update: {modrinth: {...}}} -> [update.modrinth]
        # If the table's values are all dicts, emit each as its own header.
        if sub and all(isinstance(v, dict) for v in sub.values()):
            for inner_k, inner_v in sub.items():
                out.append("")
                out.append(f"[{tk}.{inner_k}]")
                for kk, vv in inner_v.items():
                    out.append(f"{kk} = {_toml_value(vv)}")
        else:
            out.append("")
            out.append(f"[{tk}]")
            for kk, vv in sub.items():
                out.append(f"{kk} = {_toml_value(vv)}")

    for tk, items in arrays_of_tables.items():
        for entry in items:
            out.append("")
            out.append(f"[[{tk}]]")
            for kk, vv in entry.items():
                out.append(f"{kk} = {_toml_value(vv)}")

    path.write_text("\n".join(out) + "\n", encoding="utf-8")


# --- hashes -----------------------------------------------------------------

def hash_file(path: Path) -> dict[str, str]:
    """Return {sha1, sha512, murmur2} for a file."""
    sha1 = hashlib.sha1()
    sha512 = hashlib.sha512()
    raw = path.read_bytes()
    sha1.update(raw)
    sha512.update(raw)
    return {
        "sha1": sha1.hexdigest(),
        "sha512": sha512.hexdigest(),
        "murmur2": str(curseforge_fingerprint(raw)),
        "size": str(len(raw)),
    }


def curseforge_fingerprint(data: bytes) -> int:
    """CurseForge's normalized Murmur2 fingerprint.

    Strips bytes 0x09, 0x0A, 0x0D, 0x20 before hashing with Murmur2 (seed=1).
    """
    filtered = bytes(b for b in data if b not in (9, 10, 13, 32))
    return _murmur2_32(filtered, seed=1)


def _murmur2_32(data: bytes, seed: int = 1) -> int:
    m = 0x5BD1E995
    r = 24
    length = len(data)
    h = (seed ^ length) & 0xFFFFFFFF
    i = 0
    while length >= 4:
        k = data[i] | (data[i + 1] << 8) | (data[i + 2] << 16) | (data[i + 3] << 24)
        k = (k * m) & 0xFFFFFFFF
        k ^= (k >> r)
        k = (k * m) & 0xFFFFFFFF
        h = (h * m) & 0xFFFFFFFF
        h ^= k
        i += 4
        length -= 4
    if length == 3:
        h ^= data[i + 2] << 16
        h ^= data[i + 1] << 8
        h ^= data[i]
        h = (h * m) & 0xFFFFFFFF
    elif length == 2:
        h ^= data[i + 1] << 8
        h ^= data[i]
        h = (h * m) & 0xFFFFFFFF
    elif length == 1:
        h ^= data[i]
        h = (h * m) & 0xFFFFFFFF
    h ^= (h >> 13)
    h = (h * m) & 0xFFFFFFFF
    h ^= (h >> 15)
    return h & 0xFFFFFFFF


# --- HTTP helpers -----------------------------------------------------------

def http_get_json(url: str, *, headers: dict[str, str] | None = None,
                  timeout: float = 15.0, insecure: bool = False) -> tuple[int, Any]:
    return _http(url, "GET", None, headers, timeout, insecure)


def http_post_json(url: str, body: Any, *, headers: dict[str, str] | None = None,
                   timeout: float = 15.0, insecure: bool = False) -> tuple[int, Any]:
    return _http(url, "POST", body, headers, timeout, insecure)


def _http(url: str, method: str, body: Any, headers: dict[str, str] | None,
          timeout: float, insecure: bool) -> tuple[int, Any]:
    h = {"Accept": "application/json", "User-Agent": USER_AGENT}
    if headers:
        h.update(headers)
    data: bytes | None = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    ctx: ssl.SSLContext | None = None
    if insecure:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            status = resp.status
    except HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        status = e.code
    except URLError as e:
        return 0, {"error": f"connection failed: {e.reason}"}
    try:
        return status, json.loads(raw)
    except json.JSONDecodeError:
        return status, raw


# --- state scanner ----------------------------------------------------------

@dataclass
class ModEntry:
    """One mod, as known from a .pw.toml file."""
    pw_toml_path: str          # absolute path to the .pw.toml
    pw_toml_name: str          # basename of pw.toml (e.g. 'lithium.pw.toml')
    filename: str              # the jar filename it points to
    name: str                  # display name
    side: str = "both"
    download_mode: str = ""    # 'url' | 'metadata:curseforge'
    download_url: str = ""
    download_hash: str = ""
    download_hash_format: str = ""
    modrinth_id: str | None = None
    modrinth_version: str | None = None
    curseforge_project: int | None = None
    curseforge_file: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def slug(self) -> str:
        return self.pw_toml_name.removesuffix(".pw.toml")

    @property
    def provider(self) -> str:
        if self.modrinth_id and self.download_mode == "url" and "modrinth" in self.download_url:
            return "modrinth"
        if self.curseforge_project:
            return "curseforge"
        if self.download_mode == "url" and self.download_url:
            return "custom"
        return "unknown"

    @property
    def has_download_url(self) -> bool:
        return self.download_mode == "url" and bool(self.download_url)


@dataclass
class LooseJar:
    """A jar present on disk without a matching .pw.toml."""
    path: str
    filename: str
    size: int
    location: str  # 'repo' | 'prism'


@dataclass
class Issue:
    kind: str          # 'duplicate' | 'loose-jar' | 'missing-jar' | 'cf-empty-url' | 'orphan-pw' | 'mismatch'
    severity: str      # 'warning' | 'info'
    message: str
    targets: list[str] = field(default_factory=list)  # paths or ids relevant to fixing
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class State:
    repo: dict[str, Any]
    prism: dict[str, Any]
    server: dict[str, Any]
    issues: list[dict[str, Any]]


def parse_mod_entry(path: Path) -> ModEntry | None:
    raw = parse_pw_toml(path)
    if not raw:
        return None
    download = raw.get("download") or {}
    update = raw.get("update") or {}
    modrinth = (update.get("modrinth") or {}) if isinstance(update, dict) else {}
    curseforge = (update.get("curseforge") or {}) if isinstance(update, dict) else {}
    return ModEntry(
        pw_toml_path=str(path),
        pw_toml_name=path.name,
        filename=raw.get("filename", ""),
        name=raw.get("name", path.stem),
        side=raw.get("side", "both") or "both",
        download_mode=download.get("mode", ""),
        download_url=download.get("url", "") or "",
        download_hash=download.get("hash", ""),
        download_hash_format=download.get("hash-format", ""),
        modrinth_id=modrinth.get("mod-id"),
        modrinth_version=modrinth.get("version"),
        curseforge_project=curseforge.get("project-id"),
        curseforge_file=curseforge.get("file-id"),
        raw=raw,
    )


def scan_pw_dir(dir_path: Path) -> tuple[list[ModEntry], list[LooseJar]]:
    entries: list[ModEntry] = []
    jars: list[LooseJar] = []
    if not dir_path.is_dir():
        return entries, jars

    referenced_jars: set[str] = set()
    for p in sorted(dir_path.iterdir()):
        if p.is_file() and p.name.endswith(".pw.toml"):
            e = parse_mod_entry(p)
            if e is not None:
                entries.append(e)
                if e.filename:
                    referenced_jars.add(e.filename)

    location = "prism" if ".index" in dir_path.parts else "repo"
    for p in sorted(dir_path.iterdir()):
        if p.is_file() and p.name.endswith(".jar") and p.name not in referenced_jars:
            try:
                size = p.stat().st_size
            except OSError:
                continue
            jars.append(LooseJar(path=str(p), filename=p.name, size=size, location=location))
    return entries, jars


def detect_issues(repo_entries: list[ModEntry], repo_jars: list[LooseJar],
                  prism_entries: list[ModEntry], prism_jars: list[LooseJar]) -> list[Issue]:
    issues: list[Issue] = []

    # Build filename → repo .pw.toml lookup so we can tell whether a Prism-side
    # loose jar already has metadata in the repo (stale .index/, easy fix) or
    # has none anywhere (needs Identify).
    repo_pw_by_filename: dict[str, ModEntry] = {}
    for e in repo_entries:
        if e.filename:
            repo_pw_by_filename[e.filename] = e

    # 1. Loose jars in repo (no metadata at all)
    for j in repo_jars:
        issues.append(Issue(
            kind="loose-jar", severity="warning",
            message=f"{j.filename} has no .pw.toml — needs identification",
            targets=[j.path],
            data={"jar": asdict(j)},
        ))

    # 2. Prism jars without metadata in .index/. If the repo already has a
    # .pw.toml referencing this filename, it's just a stale index → one-click
    # sync. Otherwise it needs identification.
    for j in prism_jars:
        repo_match = repo_pw_by_filename.get(j.filename)
        if repo_match:
            issues.append(Issue(
                kind="prism-index-stale", severity="info",
                message=f"Prism's .index/ missing {repo_match.pw_toml_name} (exists in repo)",
                targets=[j.path, repo_match.pw_toml_path],
                data={"jar": asdict(j), "repo_pw": repo_match.pw_toml_path,
                      "repo_pw_name": repo_match.pw_toml_name},
            ))
        else:
            issues.append(Issue(
                kind="loose-jar", severity="warning",
                message=f"Prism: {j.filename} present but no .pw.toml anywhere",
                targets=[j.path],
                data={"jar": asdict(j)},
            ))

    # 3. CurseForge entries with empty download URL (in repo only — Prism may legitimately have these)
    for e in repo_entries:
        if e.curseforge_project and not e.has_download_url:
            issues.append(Issue(
                kind="cf-empty-url", severity="warning",
                message=f"{e.slug}: CurseForge entry has no direct download URL — packwiz-installer can't fetch it",
                targets=[e.pw_toml_path],
                data={"entry": _entry_dict(e)},
            ))

    # 4. Duplicates: two .pw.toml in repo for the same project on different platforms
    by_modrinth: dict[str, list[ModEntry]] = {}
    by_curseforge: dict[int, list[ModEntry]] = {}
    by_filename: dict[str, list[ModEntry]] = {}
    for e in repo_entries:
        if e.modrinth_id:
            by_modrinth.setdefault(e.modrinth_id, []).append(e)
        if e.curseforge_project:
            by_curseforge.setdefault(e.curseforge_project, []).append(e)
        if e.filename:
            by_filename.setdefault(e.filename, []).append(e)

    seen_pairs: set[tuple[str, str]] = set()

    def report_dup(group: list[ModEntry], reason: str) -> None:
        if len(group) < 2:
            return
        names = sorted(g.pw_toml_name for g in group)
        key = (reason, ":".join(names))
        if key in seen_pairs:
            return
        seen_pairs.add(key)
        issues.append(Issue(
            kind="duplicate", severity="warning",
            message=f"Duplicate ({reason}): {', '.join(names)}",
            targets=[g.pw_toml_path for g in group],
            data={"entries": [_entry_dict(g) for g in group], "reason": reason},
        ))

    for fname, group in by_filename.items():
        report_dup(group, f"same filename {fname}")
    for pid, group in by_modrinth.items():
        report_dup(group, f"same Modrinth project {pid}")
    for pid, group in by_curseforge.items():
        report_dup(group, f"same CurseForge project {pid}")

    # Cross-platform duplicates (same mod on different platforms)
    for e in repo_entries:
        if e.modrinth_id and e.curseforge_project:
            # Single entry covers both platforms — that's fine
            continue
    # Detect: one Modrinth entry + one CurseForge entry referring to the same jar by filename
    # (already covered by by_filename loop above) or by display name (best-effort)
    by_lname: dict[str, list[ModEntry]] = {}
    for e in repo_entries:
        if not e.name:
            continue
        # Normalize: strip parens, "API", non-alphanumeric
        norm = re.sub(r"[^a-z0-9]+", "", e.name.lower())
        norm = re.sub(r"(neoforge|forge|fabric|api)$", "", norm)
        if norm:
            by_lname.setdefault(norm, []).append(e)
    for norm, group in by_lname.items():
        if len(group) < 2:
            continue
        # Only flag if they're on different platforms (one MR, one CF)
        platforms = {g.provider for g in group}
        if "modrinth" in platforms and "curseforge" in platforms:
            report_dup(group, "cross-platform (Modrinth + CurseForge)")

    # 5. Mismatches between repo and Prism .pw.toml inventories. Skip entries
    # already covered by prism-index-stale to avoid double-reporting.
    already_stale = {
        i.data["repo_pw_name"]
        for i in issues if i.kind == "prism-index-stale" and "repo_pw_name" in i.data
    }
    repo_pw_names = {e.pw_toml_name for e in repo_entries}
    prism_pw_names = {e.pw_toml_name for e in prism_entries}
    only_repo = repo_pw_names - prism_pw_names - already_stale
    only_prism = prism_pw_names - repo_pw_names
    for n in sorted(only_repo):
        issues.append(Issue(
            kind="mismatch", severity="info",
            message=f"In repo but not in Prism: {n}",
            targets=[n],
            data={"side": "repo-only"},
        ))
    for n in sorted(only_prism):
        issues.append(Issue(
            kind="mismatch", severity="info",
            message=f"In Prism but not in repo: {n}",
            targets=[n],
            data={"side": "prism-only"},
        ))

    return issues


def _entry_dict(e: ModEntry, icons: IconCache | None = None,
                installed_jars: set[str] | None = None) -> dict[str, Any]:
    d = asdict(e)
    d["provider"] = e.provider
    d["has_download_url"] = e.has_download_url
    d["icon_url"] = ""
    if icons:
        d["icon_url"] = (
            (e.modrinth_id and icons.get("modrinth", e.modrinth_id))
            or (e.curseforge_project and icons.get("curseforge", str(e.curseforge_project)))
            or ""
        )
    # If the caller knows which jars actually exist on disk, mark presence.
    if installed_jars is not None:
        d["jar_installed"] = bool(e.filename) and e.filename in installed_jars
    return d


def scan_state(env: dict[str, str], icons: IconCache | None = None,
               mr: "ModrinthClient | None" = None,
               cf: "CurseForgeClient | None" = None) -> State:
    repo_mods_dir = REPO / "mods"
    repo_entries, repo_jars = scan_pw_dir(repo_mods_dir)

    prism_path = env.get("INSTANCE", "")
    prism_path = os.path.expanduser(os.path.expandvars(prism_path))
    prism_root = Path(prism_path) if prism_path else None
    prism_entries: list[ModEntry] = []
    prism_jars: list[LooseJar] = []
    prism_status: dict[str, Any] = {"path": str(prism_root) if prism_root else "", "ok": False}
    prism_installed_jars: set[str] = set()
    not_installed_entries: list[ModEntry] = []
    if prism_root and prism_root.is_dir():
        prism_index = prism_root / "mods" / ".index"
        prism_mods = prism_root / "mods"
        prism_entries, _ = scan_pw_dir(prism_index)
        if prism_mods.is_dir():
            for p in prism_mods.iterdir():
                if p.is_file() and p.name.endswith(".jar"):
                    prism_installed_jars.add(p.name)
        # Loose jars: jars on disk not referenced by any .pw.toml in .index/
        referenced = {e.filename for e in prism_entries if e.filename}
        for jar_name in sorted(prism_installed_jars):
            if jar_name not in referenced:
                p = prism_mods / jar_name
                try:
                    prism_jars.append(LooseJar(
                        path=str(p), filename=p.name, size=p.stat().st_size, location="prism",
                    ))
                except OSError:
                    pass
        # Inverse: .pw.toml entries whose jars aren't installed
        for e in prism_entries:
            if e.filename and e.filename not in prism_installed_jars:
                not_installed_entries.append(e)
        prism_status = {
            "path": str(prism_root),
            "ok": True,
            "jar_count": len(prism_installed_jars),
            "index_count": len(prism_entries),
            "not_installed_count": len(not_installed_entries),
        }

    issues = detect_issues(repo_entries, repo_jars, prism_entries, prism_jars)

    # Surface "missing jar" issues — Prism .index/ entry exists but the jar
    # itself is absent from <instance>/<kind>/. Common cause: bootstrapper
    # skipped a side='server' mod, or a single-mod sync didn't fetch the jar.
    for e in not_installed_entries:
        issues.append(Issue(
            kind="missing-jar", severity="warning",
            message=f"{e.name or e.slug}: jar not installed in Prism (filename: {e.filename})",
            targets=[e.pw_toml_path],
            data={
                "entry": _entry_dict(e, icons),
                "filename": e.filename,
                "pw_toml_path": e.pw_toml_path,
                "pw_toml_name": e.pw_toml_name,
            },
        ))

    if icons is not None and mr is not None and cf is not None:
        try:
            populate_icons([*repo_entries, *prism_entries], mr, cf, icons)
        except Exception:
            pass  # icons are nice-to-have, never fail the scan

    return State(
        repo={
            "path": str(REPO),
            "entries": [_entry_dict(e, icons) for e in repo_entries],
            "loose_jars": [asdict(j) for j in repo_jars],
            "entry_count": len(repo_entries),
            "loose_count": len(repo_jars),
        },
        prism={
            **prism_status,
            "entries": [_entry_dict(e, icons, prism_installed_jars) for e in prism_entries],
            "loose_jars": [asdict(j) for j in prism_jars],
        },
        server={},  # filled in by status poller
        issues=_inject_icons_into_issues([asdict(i) for i in issues], icons),
    )


def _inject_icons_into_issues(issues_dicts: list[dict[str, Any]],
                              icons: IconCache | None) -> list[dict[str, Any]]:
    if not icons:
        return issues_dicts
    for i in issues_dicts:
        d = i.get("data") or {}
        if "entry" in d and isinstance(d["entry"], dict):
            d["entry"]["icon_url"] = (
                (d["entry"].get("modrinth_id") and icons.get("modrinth", d["entry"]["modrinth_id"]))
                or (d["entry"].get("curseforge_project") and icons.get("curseforge", str(d["entry"]["curseforge_project"])))
                or ""
            )
        if "entries" in d and isinstance(d["entries"], list):
            for ent in d["entries"]:
                if isinstance(ent, dict):
                    ent["icon_url"] = (
                        (ent.get("modrinth_id") and icons.get("modrinth", ent["modrinth_id"]))
                        or (ent.get("curseforge_project") and icons.get("curseforge", str(ent["curseforge_project"])))
                        or ""
                    )
    return issues_dicts


# --- Modrinth + CurseForge clients ------------------------------------------

class ModrinthClient:
    def __init__(self) -> None:
        self.base = MODRINTH_API

    def lookup_by_hash(self, sha512: str, sha1: str = "") -> dict[str, Any] | None:
        s, body = http_get_json(
            f"{self.base}/version_file/{sha512}?algorithm=sha512",
        )
        if s == 200 and isinstance(body, dict):
            return body
        if sha1:
            s, body = http_get_json(
                f"{self.base}/version_file/{sha1}?algorithm=sha1",
            )
            if s == 200 and isinstance(body, dict):
                return body
        return None

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        s, body = http_get_json(f"{self.base}/project/{project_id}")
        return body if s == 200 and isinstance(body, dict) else None

    def search(self, query: str, *, mc_version: str = "", loader: str = "") -> list[dict[str, Any]]:
        facets: list[list[str]] = [["project_type:mod"]]
        if mc_version:
            facets.append([f"versions:{mc_version}"])
        if loader:
            facets.append([f"categories:{loader}"])
        params = {
            "query": query,
            "facets": json.dumps(facets),
            "limit": "10",
        }
        url = f"{self.base}/search?" + urllib.parse.urlencode(params)
        s, body = http_get_json(url)
        if s == 200 and isinstance(body, dict):
            return body.get("hits", [])
        return []

    def get_version(self, project_id: str, version_id: str) -> dict[str, Any] | None:
        s, body = http_get_json(f"{self.base}/version/{version_id}")
        return body if s == 200 and isinstance(body, dict) else None

    def latest_version(self, project_id: str, *, mc_version: str = "", loader: str = "") -> dict[str, Any] | None:
        params: dict[str, str] = {}
        if mc_version:
            params["game_versions"] = json.dumps([mc_version])
        if loader:
            params["loaders"] = json.dumps([loader])
        url = f"{self.base}/project/{project_id}/version"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        s, body = http_get_json(url)
        if s == 200 and isinstance(body, list) and body:
            return body[0]
        return None


class IconCache:
    """In-memory cache of provider:id → icon URL. Populated by batch lookups
    on scan_state so the UI can render icons without a per-mod round-trip."""

    def __init__(self) -> None:
        self.cache: dict[str, str] = {}
        self.lock = threading.Lock()

    def get(self, provider: str, id_: Any) -> str | None:
        if id_ is None:
            return None
        return self.cache.get(f"{provider}:{id_}")

    def set_many(self, provider: str, items: dict[Any, str]) -> None:
        with self.lock:
            for k, v in items.items():
                if v:
                    self.cache[f"{provider}:{k}"] = v

    def known(self, provider: str) -> set[str]:
        prefix = f"{provider}:"
        return {k.removeprefix(prefix) for k in self.cache if k.startswith(prefix)}


def populate_icons(entries: Iterable[ModEntry], mr: "ModrinthClient",
                   cf: "CurseForgeClient", cache: IconCache) -> None:
    """Batch-fetch icon URLs for any project IDs not already cached."""
    mr_known = cache.known("modrinth")
    cf_known = cache.known("curseforge")
    mr_ids: set[str] = set()
    cf_ids: set[int] = set()
    for e in entries:
        if e.modrinth_id and e.modrinth_id not in mr_known:
            mr_ids.add(e.modrinth_id)
        if e.curseforge_project and str(e.curseforge_project) not in cf_known:
            cf_ids.add(e.curseforge_project)

    if mr_ids:
        # Modrinth: GET /v2/projects?ids=["a","b",...]
        ids_param = json.dumps(sorted(mr_ids))
        url = f"{MODRINTH_API}/projects?ids={urllib.parse.quote(ids_param)}"
        s, body = http_get_json(url, timeout=10.0)
        new: dict[str, str] = {}
        if s == 200 and isinstance(body, list):
            for proj in body:
                if isinstance(proj, dict):
                    pid = proj.get("id")
                    icon = proj.get("icon_url") or ""
                    if pid:
                        new[pid] = icon
        cache.set_many("modrinth", new)

    if cf_ids and cf.configured:
        # CF: POST /v1/mods with {modIds: [...]}
        s, body = http_post_json(
            f"{CURSEFORGE_API}/mods",
            {"modIds": sorted(cf_ids)},
            headers={"x-api-key": cf.api_key} if cf.api_key else None,
            timeout=10.0,
        )
        new = {}
        if s == 200 and isinstance(body, dict):
            for m in body.get("data") or []:
                if isinstance(m, dict):
                    mid = m.get("id")
                    logo = m.get("logo") or {}
                    icon = (logo.get("url") if isinstance(logo, dict) else "") or ""
                    if mid is not None:
                        new[str(mid)] = icon
        cache.set_many("curseforge", new)


class CurseForgeClient:
    def __init__(self, api_key: str | None) -> None:
        self.base = CURSEFORGE_API
        self.api_key = api_key

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            return {}
        return {"x-api-key": self.api_key}

    def lookup_by_fingerprint(self, murmur2: int) -> dict[str, Any] | None:
        if not self.configured:
            return None
        s, body = http_post_json(
            f"{self.base}/fingerprints",
            {"fingerprints": [murmur2]},
            headers=self._headers(),
        )
        if s == 200 and isinstance(body, dict):
            data = body.get("data") or {}
            matches = data.get("exactMatches") or []
            return matches[0] if matches else None
        return None

    def get_mod(self, project_id: int) -> dict[str, Any] | None:
        if not self.configured:
            return None
        s, body = http_get_json(f"{self.base}/mods/{project_id}", headers=self._headers())
        if s == 200 and isinstance(body, dict):
            return body.get("data")
        return None

    def get_file(self, project_id: int, file_id: int) -> dict[str, Any] | None:
        if not self.configured:
            return None
        s, body = http_get_json(
            f"{self.base}/mods/{project_id}/files/{file_id}", headers=self._headers(),
        )
        if s == 200 and isinstance(body, dict):
            return body.get("data")
        return None

    def get_download_url(self, project_id: int, file_id: int) -> str | None:
        """Try to get the actual CDN URL for a file. May return None if the
        project disables third-party downloads."""
        f = self.get_file(project_id, file_id)
        if not f:
            return None
        url = f.get("downloadUrl")
        if url:
            return url
        # Fallback to the dedicated endpoint
        s, body = http_get_json(
            f"{self.base}/mods/{project_id}/files/{file_id}/download-url",
            headers=self._headers(),
        )
        if s == 200 and isinstance(body, dict):
            data = body.get("data")
            if isinstance(data, str):
                return data
        return None

    def search(self, query: str, *, mc_version: str = "", loader_id: int | None = None,
               game_id: int = 432) -> list[dict[str, Any]]:
        if not self.configured:
            return []
        params: dict[str, str] = {
            "gameId": str(game_id),
            "searchFilter": query,
            "pageSize": "10",
        }
        if mc_version:
            params["gameVersion"] = mc_version
        if loader_id is not None:
            params["modLoaderType"] = str(loader_id)
        url = f"{self.base}/mods/search?" + urllib.parse.urlencode(params)
        s, body = http_get_json(url, headers=self._headers())
        if s == 200 and isinstance(body, dict):
            return body.get("data", [])
        return []



# --- Crafty client (slim wrapper for status; deploy uses the existing script) ---

class CraftyClient:
    def __init__(self, base_url: str, token: str, server_id: str, *, insecure: bool):
        self.base = base_url.rstrip("/")
        self.token = token
        self.server_id = server_id
        self.insecure = insecure

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def stats(self) -> dict[str, Any]:
        s, body = http_get_json(
            f"{self.base}/api/v2/servers/{self.server_id}/stats",
            headers=self._headers(),
            insecure=self.insecure,
            timeout=5.0,
        )
        if s == 200 and isinstance(body, dict) and body.get("status") == "ok":
            return body.get("data") or {}
        return {}

    def list_servers(self) -> list[dict[str, Any]]:
        s, body = http_get_json(
            f"{self.base}/api/v2/servers",
            headers=self._headers(),
            insecure=self.insecure,
            timeout=5.0,
        )
        if s == 200 and isinstance(body, dict) and body.get("status") == "ok":
            return body.get("data") or []
        return []


def make_crafty(env: dict[str, str]) -> CraftyClient | None:
    base = env.get("CRAFTY_URL")
    token = env.get("CRAFTY_TOKEN")
    sid = env.get("CRAFTY_SERVER_ID")
    if not (base and token and sid):
        return None
    return CraftyClient(base, token, sid, insecure=truthy(env.get("CRAFTY_INSECURE", "true")))


def server_status(env: dict[str, str]) -> dict[str, Any]:
    client = make_crafty(env)
    if client is None:
        return {"configured": False}
    s = client.stats()
    if not s:
        return {"configured": True, "reachable": False}
    return {
        "configured": True,
        "reachable": True,
        "running": bool(s.get("running")),
        "version": s.get("version"),
        "online": s.get("online"),
        "max": s.get("max"),
        "cpu": s.get("cpu"),
        "world_size": s.get("world_size"),
    }


# --- job manager (background subprocess + log buffer) -----------------------

@dataclass
class Job:
    id: str
    name: str
    status: str = "pending"  # pending | running | done | error
    return_code: int | None = None
    started: float = 0.0
    ended: float = 0.0
    log: list[str] = field(default_factory=list)
    subscribers: list["queue.Queue[str | None]"] = field(default_factory=list)
    proc: subprocess.Popen | None = None
    cleanup: list[Any] = field(default_factory=list)  # callables to run on completion


class JobManager:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()
        self._next_id = 1
        self._current: str | None = None

    def submit(self, name: str, run: "callable[[Job], int]") -> Job:
        with self.lock:
            if self._current and self.jobs[self._current].status == "running":
                raise RuntimeError("another job is already running")
            jid = f"j{self._next_id}"
            self._next_id += 1
            job = Job(id=jid, name=name, status="running", started=time.time())
            self.jobs[jid] = job
            self._current = jid
        t = threading.Thread(target=self._run_thread, args=(job, run), daemon=True)
        t.start()
        return job

    def _run_thread(self, job: Job, run) -> None:
        try:
            rc = run(job)
            job.return_code = rc
            job.status = "done" if rc == 0 else "error"
        except Exception as e:
            self.append(job, f"\n*** EXCEPTION: {e}\n{traceback.format_exc()}")
            job.return_code = -1
            job.status = "error"
        finally:
            job.ended = time.time()
            for cb in job.cleanup:
                try:
                    cb()
                except Exception:
                    pass
            # Notify SSE subscribers to close
            for q in list(job.subscribers):
                try:
                    q.put_nowait(None)
                except Exception:
                    pass
            with self.lock:
                if self._current == job.id:
                    self._current = None

    def append(self, job: Job, text: str) -> None:
        job.log.append(text)
        for q in list(job.subscribers):
            try:
                q.put_nowait(text)
            except Exception:
                pass

    def stream_subprocess(self, job: Job, cmd: list[str], *, cwd: Path | None = None,
                          env: dict[str, str] | None = None) -> int:
        self.append(job, f"$ {' '.join(cmd)}\n")
        try:
            proc = subprocess.Popen(
                cmd, cwd=str(cwd) if cwd else None,
                env={**os.environ, **(env or {})},
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
        except FileNotFoundError as e:
            self.append(job, f"error: {e}\n")
            return 127
        job.proc = proc
        assert proc.stdout is not None
        for line in proc.stdout:
            self.append(job, line)
        proc.wait()
        job.proc = None
        return proc.returncode


# --- action runners ---------------------------------------------------------

def populate_prism_index(repo: Path, instance: Path, *, keep_stale: bool = False,
                         job: Job | None = None, jm: JobManager | None = None) -> None:
    """Mirror repo/<kind>/*.pw.toml into instance/<kind>/.index/."""
    def log(msg: str) -> None:
        if jm and job:
            jm.append(job, msg + "\n")
        else:
            print(msg)

    for kind in METADATA_KINDS:
        src = repo / kind
        dst = instance / kind / ".index"
        if not src.is_dir():
            continue
        dst.mkdir(parents=True, exist_ok=True)
        repo_names = {p.name for p in src.iterdir() if p.is_file() and p.name.endswith(".pw.toml")}
        if not keep_stale:
            for existing in dst.iterdir():
                if existing.is_file() and existing.name.endswith(".pw.toml") and existing.name not in repo_names:
                    existing.unlink()
        for name in repo_names:
            shutil.copy2(src / name, dst / name)
        log(f"  populated {kind}/.index/ ({len(repo_names)} entries)")


def find_free_port(start: int = 0) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", start))
        return s.getsockname()[1]


class PackwizServeManager:
    """Runs `packwiz serve` in the background on a chosen port. Used by
    Local→Prism and Local→Server flows."""

    def __init__(self, packwiz_bin: str, repo: Path) -> None:
        self.packwiz_bin = packwiz_bin
        self.repo = repo
        self.proc: subprocess.Popen | None = None
        self.port: int | None = None

    def __enter__(self) -> "PackwizServeManager":
        port = find_free_port()
        self.port = port
        # packwiz serve listens on 0.0.0.0:8080 by default; use --port to choose
        self.proc = subprocess.Popen(
            [self.packwiz_bin, "serve", "--port", str(port)],
            cwd=str(self.repo),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        # Wait for it to be ready
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    return self
            except OSError:
                time.sleep(0.1)
        return self

    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/pack.toml"

    def __exit__(self, *exc) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def action_local_to_prism(env: dict[str, str], jm: JobManager, job: Job) -> int:
    """Build local pack via `packwiz serve`, run packwiz-installer-bootstrap,
    then mirror repo's .pw.toml files into Prism's .index/ dirs."""
    instance = Path(os.path.expanduser(env.get("INSTANCE", "")))
    if not instance.is_dir():
        jm.append(job, f"error: INSTANCE not found at {instance}\n")
        return 1
    bootstrap = instance / "packwiz-installer-bootstrap.jar"
    if not bootstrap.is_file():
        jm.append(job, f"error: {bootstrap} missing — download from\n"
                       f"  https://github.com/packwiz/packwiz-installer-bootstrap/releases\n")
        return 1
    packwiz_bin = find_binary("packwiz", env)
    if not packwiz_bin:
        jm.append(job, "error: packwiz not found in PATH\n")
        return 1
    java_bin = find_binary("java", env)
    if not java_bin:
        jm.append(job, "error: java not found in PATH\n")
        return 1

    # Make sure index.toml is current before serving
    jm.append(job, f"→ packwiz refresh\n")
    rc = jm.stream_subprocess(job, [packwiz_bin, "refresh"], cwd=REPO)
    if rc != 0:
        return rc

    with PackwizServeManager(packwiz_bin, REPO) as serve:
        if serve.port is None:
            jm.append(job, "error: packwiz serve failed to start\n")
            return 1
        pack_url = serve.url()
        jm.append(job, f"→ packwiz serve on {pack_url}\n")
        jm.append(job, f"→ packwiz-installer-bootstrap.jar (instance: {instance})\n")
        rc = jm.stream_subprocess(
            job,
            [java_bin, "-jar", "packwiz-installer-bootstrap.jar",
             "--bootstrap-no-update",
             *(["--bootstrap-main-jar", str(instance / "packwiz-installer.jar")]
               if (instance / "packwiz-installer.jar").is_file() else []),
             pack_url],
            cwd=instance,
        )
        if rc != 0:
            return rc

    jm.append(job, "→ populate Prism .index/ from repo .pw.toml files\n")
    populate_prism_index(REPO, instance, keep_stale=False, job=job, jm=jm)
    jm.append(job, "✓ done\n")
    return 0


def action_prism_to_local(env: dict[str, str], jm: JobManager, job: Job) -> int:
    """Pull .pw.toml from Prism's .index/ into repo, then mirror config dirs.
    Caller (UI) should resolve loose-jar issues first; we copy whatever is there."""
    instance = Path(os.path.expanduser(env.get("INSTANCE", "")))
    if not instance.is_dir():
        jm.append(job, f"error: INSTANCE not found at {instance}\n")
        return 1
    packwiz_bin = find_binary("packwiz", env)
    if not packwiz_bin:
        jm.append(job, "error: packwiz not found in PATH\n")
        return 1

    for kind in METADATA_KINDS:
        src = instance / kind / ".index"
        dst = REPO / kind
        if not src.is_dir():
            continue
        dst.mkdir(parents=True, exist_ok=True)
        src_names = {p.name for p in src.iterdir() if p.suffix == ".toml" and p.name.endswith(".pw.toml")}
        for existing in dst.iterdir():
            if existing.is_file() and existing.name.endswith(".pw.toml") and existing.name not in src_names:
                existing.unlink()
        copied = 0
        for entry in src.iterdir():
            if entry.is_file() and entry.name.endswith(".pw.toml"):
                shutil.copy2(entry, dst / entry.name)
                copied += 1
        jm.append(job, f"  synced {kind}/ ({copied} entries)\n")

    for name in VERBATIM_DIRS:
        src = instance / name
        dst = REPO / name
        if not src.is_dir():
            continue
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        jm.append(job, f"  mirrored {name}/\n")

    jm.append(job, "→ packwiz refresh\n")
    rc = jm.stream_subprocess(job, [packwiz_bin, "refresh"], cwd=REPO)
    if rc != 0:
        return rc

    jm.append(job, "✓ done — review with `git status` / `git diff`\n")
    return 0


def action_deploy_to_server(env: dict[str, str], jm: JobManager, job: Job) -> int:
    """Local → Server: spin up `packwiz serve`, then run the existing
    sync-prism-to-crafty-server.py script with PACK_URL pointed at it."""
    script = REPO / "sync-prism-to-crafty-server.py"
    if not script.is_file():
        jm.append(job, f"error: {script} missing\n")
        return 1
    packwiz_bin = find_binary("packwiz", env)
    if not packwiz_bin:
        jm.append(job, "error: packwiz not found in PATH\n")
        return 1
    java_bin = find_binary("java", env)
    if not java_bin:
        jm.append(job, "error: java not found in PATH\n")
        return 1

    jm.append(job, "→ packwiz refresh\n")
    rc = jm.stream_subprocess(job, [packwiz_bin, "refresh"], cwd=REPO)
    if rc != 0:
        return rc

    with PackwizServeManager(packwiz_bin, REPO) as serve:
        if serve.port is None:
            jm.append(job, "error: packwiz serve failed to start\n")
            return 1
        pack_url = serve.url()
        jm.append(job, f"→ packwiz serve on {pack_url}\n")
        jm.append(job, "→ sync-prism-to-crafty-server.py deploy --apply (PACK_URL=local)\n")
        rc = jm.stream_subprocess(
            job,
            ["python3", str(script), "deploy", "--apply"],
            cwd=REPO,
            env={"PACK_URL": pack_url},
        )
        return rc


def action_github_to_prism(env: dict[str, str], jm: JobManager, job: Job, *, branch: str) -> int:
    """Install a remote branch's pack into Prism (without touching the local repo)."""
    instance = Path(os.path.expanduser(env.get("INSTANCE", "")))
    if not instance.is_dir():
        jm.append(job, f"error: INSTANCE not found at {instance}\n")
        return 1
    bootstrap = instance / "packwiz-installer-bootstrap.jar"
    if not bootstrap.is_file():
        jm.append(job, f"error: {bootstrap} missing\n")
        return 1
    java_bin = find_binary("java", env)
    if not java_bin:
        jm.append(job, "error: java not found in PATH\n")
        return 1
    slug = github_remote_slug()
    if not slug:
        jm.append(job, "error: can't determine GitHub remote slug\n")
        return 1

    pack_url = f"https://raw.githubusercontent.com/{slug}/{branch}/pack.toml"
    jm.append(job, f"→ pack URL: {pack_url}\n")
    jm.append(job, f"→ packwiz-installer-bootstrap.jar (instance: {instance})\n")

    # Pre-snapshot of the install state, for the summary at the end
    def count_jars() -> int:
        d = instance / "mods"
        if not d.is_dir():
            return 0
        return sum(1 for p in d.iterdir() if p.is_file() and p.suffix == ".jar")
    pre_jars = count_jars()

    rc = jm.stream_subprocess(
        job,
        [java_bin, "-jar", "packwiz-installer-bootstrap.jar",
             "--bootstrap-no-update",
             *(["--bootstrap-main-jar", str(instance / "packwiz-installer.jar")]
               if (instance / "packwiz-installer.jar").is_file() else []),
             pack_url],
        cwd=instance,
    )
    if rc != 0:
        jm.append(job, f"\n✗ bootstrapper exited with code {rc} — Prism was NOT updated.\n"
                       "  If you saw 'There was an error downloading packwiz-installer' or a 403,\n"
                       "  the error came from GitHub's API rate limit. Try again in a few minutes.\n")
        return rc

    post_jars = count_jars()
    jm.append(job, f"\n→ jars in Prism mods/: {pre_jars} → {post_jars}\n")

    # Populate Prism .index/ from the chosen branch — uses local git plumbing
    # to avoid GitHub API rate limits.
    jm.append(job, f"→ populating Prism .index/ from {branch} via git\n")
    rc = _populate_prism_index_from_github(slug, branch, instance, jm, job)
    if rc != 0:
        return rc

    jm.append(job, "✓ done\n")
    return 0


def _populate_prism_index_from_github(slug: str, branch: str, instance: Path,
                                      jm: JobManager, job: Job) -> int:
    """Populate Prism's .index/ from a remote branch using local git plumbing —
    no GitHub API calls, so no rate limits. Requires the local clone to have
    `origin` pointing at the same repo as `slug` (which it does, since we
    detected `slug` from origin's URL)."""
    # Ensure origin/<branch> is up to date
    fetch = subprocess.run(
        ["git", "fetch", "origin", branch, "--quiet"],
        cwd=str(REPO), capture_output=True, text=True,
    )
    if fetch.returncode != 0:
        jm.append(job, f"  warning: git fetch origin {branch} failed:\n  {fetch.stderr.strip()}\n")
        # try anyway with whatever ref is locally cached

    ref = f"origin/{branch}"

    # Verify ref exists
    rv = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", ref],
        cwd=str(REPO), capture_output=True, text=True,
    )
    if rv.returncode != 0:
        jm.append(job, f"  error: ref {ref} not found locally — try `git fetch origin {branch}` manually\n")
        return 1

    total_written = 0
    for kind in METADATA_KINDS:
        # List `<kind>/*.pw.toml` files on the branch
        ls = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", ref, f"{kind}/"],
            cwd=str(REPO), capture_output=True, text=True,
        )
        if ls.returncode != 0:
            jm.append(job, f"  skip {kind}/ (not present on {ref})\n")
            continue

        wanted: set[str] = set()
        for line in ls.stdout.splitlines():
            line = line.strip()
            if not line.startswith(f"{kind}/"):
                continue
            if not line.endswith(".pw.toml"):
                continue
            # Skip nested .pw.toml files (we only want top-level <kind>/X.pw.toml)
            tail = line[len(kind) + 1:]
            if "/" in tail:
                continue
            wanted.add(tail)

        dst_index = instance / kind / ".index"
        dst_index.mkdir(parents=True, exist_ok=True)

        # Remove stale entries
        removed = 0
        for existing in dst_index.iterdir():
            if existing.is_file() and existing.name.endswith(".pw.toml") and existing.name not in wanted:
                existing.unlink()
                removed += 1

        # Write wanted files (`git show <ref>:path`)
        written = 0
        for name in sorted(wanted):
            content = subprocess.run(
                ["git", "show", f"{ref}:{kind}/{name}"],
                cwd=str(REPO), capture_output=True,
            )
            if content.returncode == 0:
                (dst_index / name).write_bytes(content.stdout)
                written += 1
            else:
                jm.append(job, f"  warn: git show failed for {kind}/{name}\n")

        total_written += written
        msg = f"  {kind}/.index/: wrote {written}, removed {removed} stale"
        jm.append(job, msg + "\n")

    if total_written == 0:
        jm.append(job, "  warning: no .pw.toml files written — branch may be empty or git fetch failed\n")
    return 0


def list_remote_branches(slug: str) -> list[str]:
    """List branches via GitHub API. Falls back to git ls-remote."""
    s, body = http_get_json(f"https://api.github.com/repos/{slug}/branches?per_page=100")
    if s == 200 and isinstance(body, list):
        return [b["name"] for b in body if isinstance(b, dict) and "name" in b]
    try:
        r = subprocess.run(
            ["git", "ls-remote", "--heads", "origin"],
            capture_output=True, text=True, check=True, cwd=str(REPO),
        )
        out: list[str] = []
        for line in r.stdout.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].startswith("refs/heads/"):
                out.append(parts[1].removeprefix("refs/heads/"))
        return out
    except Exception:
        return []


# --- mod identification helpers ---------------------------------------------

def identify_jar(path: Path, mr: ModrinthClient, cf: CurseForgeClient) -> dict[str, Any]:
    """Hash a jar and look it up on both platforms in parallel.
    Returns {hashes, modrinth, modrinth_project, curseforge, curseforge_mod}."""
    h = hash_file(path)
    result: dict[str, Any] = {
        "hashes": h, "modrinth": None, "modrinth_project": None,
        "curseforge": None, "curseforge_mod": None,
    }

    def find_mr() -> None:
        v = mr.lookup_by_hash(h["sha512"], h["sha1"])
        result["modrinth"] = v
        if v and v.get("project_id"):
            result["modrinth_project"] = mr.get_project(v["project_id"])

    def find_cf() -> None:
        match = cf.lookup_by_fingerprint(int(h["murmur2"]))
        result["curseforge"] = match
        if match:
            file_info = match.get("file") or match
            mid = file_info.get("modId") or match.get("id")
            if mid:
                result["curseforge_mod"] = cf.get_mod(mid)

    threads = [
        threading.Thread(target=find_mr, daemon=True),
        threading.Thread(target=find_cf, daemon=True),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    return result


def build_pw_toml_from_modrinth(jar_path: Path, mr_version: dict[str, Any],
                                project: dict[str, Any] | None = None) -> dict[str, Any]:
    """Construct a .pw.toml dict from a Modrinth version response."""
    files = mr_version.get("files") or []
    primary = next((f for f in files if f.get("primary")), files[0] if files else {})
    hashes = primary.get("hashes") or {}
    project_name = (project or {}).get("title") or mr_version.get("name") or jar_path.stem
    return {
        "filename": primary.get("filename") or jar_path.name,
        "name": project_name,
        "side": "both",
        "download": {
            "hash-format": "sha512",
            "hash": hashes.get("sha512", ""),
            "mode": "url",
            "url": primary.get("url", ""),
        },
        "update": {
            "modrinth": {
                "mod-id": mr_version.get("project_id", ""),
                "version": mr_version.get("id", ""),
            }
        },
    }


def build_pw_toml_from_curseforge(jar_path: Path, cf_match: dict[str, Any],
                                  use_direct_url: bool = True,
                                  cf_client: CurseForgeClient | None = None) -> dict[str, Any]:
    """Construct a .pw.toml dict from a CurseForge fingerprint match."""
    file_info = cf_match.get("file") or cf_match
    project_id = file_info.get("modId") or cf_match.get("id")
    file_id = file_info.get("id")
    file_name = file_info.get("fileName") or jar_path.name
    file_hashes = file_info.get("hashes") or []
    sha1 = next((h.get("value") for h in file_hashes if h.get("algo") == 1), "")

    download_url = ""
    mode = "metadata:curseforge"
    if use_direct_url and cf_client and project_id and file_id:
        url = cf_client.get_download_url(project_id, file_id)
        if url:
            download_url = url
            mode = "url"

    name = jar_path.stem
    if cf_client and project_id:
        mod = cf_client.get_mod(project_id)
        if mod:
            name = mod.get("name") or name

    return {
        "filename": file_name,
        "name": name,
        "side": "both",
        "download": {
            "hash-format": "sha1",
            "hash": sha1,
            "mode": mode,
            "url": download_url,
        },
        "update": {
            "curseforge": {
                "file-id": file_id,
                "project-id": project_id,
            }
        },
    }


def build_pw_toml_custom(jar_path: Path, github_slug: str, branch: str = GITHUB_DEFAULT_BRANCH,
                         display_name: str | None = None) -> dict[str, Any]:
    """For user-marked-custom jars: serve directly from GitHub raw."""
    h = hash_file(jar_path)
    raw_url = f"https://raw.githubusercontent.com/{github_slug}/{branch}/mods/{jar_path.name}"
    return {
        "filename": jar_path.name,
        "name": display_name or jar_path.stem,
        "side": "both",
        "download": {
            "hash-format": "sha512",
            "hash": h["sha512"],
            "mode": "url",
            "url": raw_url,
        },
    }


def fix_curseforge_url(entry: ModEntry, cf_client: CurseForgeClient) -> bool:
    """Rewrite a .pw.toml entry to mode='url' with the actual CF download URL.
    Returns True if successful."""
    if not (entry.curseforge_project and entry.curseforge_file):
        return False
    url = cf_client.get_download_url(entry.curseforge_project, entry.curseforge_file)
    if not url:
        return False
    raw = entry.raw
    raw.setdefault("download", {})
    raw["download"]["mode"] = "url"
    raw["download"]["url"] = url
    write_pw_toml(Path(entry.pw_toml_path), raw)
    return True


# --- config diff helpers ----------------------------------------------------

def walk_dir_files(d: Path) -> dict[str, dict[str, Any]]:
    """Walk d recursively. Returns {relpath_str: {sha1, size, mtime}}.
    Hashes are computed lazily — only when comparison actually needs them."""
    out: dict[str, dict[str, Any]] = {}
    if not d.is_dir():
        return out
    for root, dirs, names in os.walk(d):
        dirs.sort()
        names.sort()
        for name in names:
            try:
                p = Path(root) / name
                rel = str(p.relative_to(d))
                st = p.stat()
                out[rel] = {
                    "size": st.st_size,
                    "mtime": st.st_mtime_ns,
                    "abs": str(p),
                    "sha1": None,  # computed only when needed
                }
            except (OSError, ValueError):
                pass
    return out


def _file_hash(path: Path, algo: str) -> str:
    """Hash a file with the given algorithm (sha1/sha256/sha512/md5)."""
    if algo == "sha1": h = hashlib.sha1()
    elif algo == "sha512": h = hashlib.sha512()
    elif algo == "sha256": h = hashlib.sha256()
    elif algo == "md5": h = hashlib.md5()
    else: return ""
    try:
        with open(path, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def download_pw_jar(pw_toml_path: Path, instance: Path,
                    cf_client: "CurseForgeClient | None",
                    kind: str | None = None) -> tuple[bool, str, str]:
    """Download the jar referenced by a .pw.toml into <instance>/<kind>/<filename>.

    The .pw.toml can live anywhere — typical sources are <REPO>/<kind>/X.pw.toml
    (used by sync-to-prism) and <instance>/<kind>/.index/X.pw.toml (used by
    install-missing-jar). The kind is auto-derived from the path unless given.

    Returns (ok, status, message) where status is one of:
        'downloaded'   - jar was just fetched
        'cached'       - jar already present with correct hash
        'skipped'      - intentionally not downloaded
        'error'        - something went wrong; ok will be False
    """
    raw = parse_pw_toml(pw_toml_path)
    if not raw:
        return False, "error", "couldn't parse .pw.toml"
    filename = raw.get("filename")
    if not filename:
        return False, "error", ".pw.toml has no filename"

    if kind is None:
        # Auto-derive: parent dir is either <kind>/ (repo) or .index/ (prism)
        parent = pw_toml_path.parent.name
        grandparent = pw_toml_path.parent.parent.name
        if parent in METADATA_KINDS:
            kind = parent
        elif parent == ".index" and grandparent in METADATA_KINDS:
            kind = grandparent
        else:
            try:
                parts = pw_toml_path.relative_to(REPO).parts
                if parts and parts[0] in METADATA_KINDS:
                    kind = parts[0]
            except ValueError:
                pass
    if not kind or kind not in METADATA_KINDS:
        return False, "error", f"couldn't determine kind for {pw_toml_path}"

    download = raw.get("download") or {}
    mode = download.get("mode", "")
    url = download.get("url", "") or ""
    expected_hash = download.get("hash", "") or ""
    hash_format = download.get("hash-format", "") or ""

    actual_url = ""
    if mode == "url" and url:
        actual_url = url
    elif mode == "metadata:curseforge":
        if not (cf_client and cf_client.configured):
            return False, "error", "CurseForge metadata mode — set CURSEFORGE_API_KEY in Settings"
        cf_meta = (raw.get("update") or {}).get("curseforge") or {}
        project_id = cf_meta.get("project-id")
        file_id = cf_meta.get("file-id")
        if not (project_id and file_id):
            return False, "error", "CF mode but missing project-id / file-id"
        actual_url = cf_client.get_download_url(project_id, file_id) or ""
        if not actual_url:
            return False, "error", "CurseForge API returned no download URL (project may disable third-party downloads)"
    else:
        return False, "error", f"unsupported download mode: {mode!r}"

    dest_jar = instance / kind / filename

    # Already installed with correct hash? Nothing to do.
    if dest_jar.is_file() and expected_hash and hash_format:
        if _file_hash(dest_jar, hash_format) == expected_hash:
            return True, "cached", "jar already installed (hash matches)"

    dest_jar.parent.mkdir(parents=True, exist_ok=True)
    try:
        req = urllib.request.Request(actual_url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
    except Exception as e:
        return False, "error", f"download failed: {e}"

    if expected_hash and hash_format:
        algos = {"sha1": hashlib.sha1, "sha512": hashlib.sha512,
                 "sha256": hashlib.sha256, "md5": hashlib.md5}
        hasher = algos.get(hash_format)
        if hasher:
            actual_hash = hasher(data).hexdigest()
            if actual_hash != expected_hash:
                return False, "error", (
                    f"hash mismatch — expected {expected_hash[:16]}…, got {actual_hash[:16]}…"
                )

    try:
        dest_jar.write_bytes(data)
    except OSError as e:
        return False, "error", f"write failed: {e}"
    return True, "downloaded", f"installed {filename} ({len(data):,} bytes)"


def _file_sha1(path: str) -> str:
    try:
        h = hashlib.sha1()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def diff_config_dirs(env: dict[str, str]) -> dict[str, Any]:
    """Compare repo VERBATIM_DIRS with Prism's. Returns {summary, items}.

    Items are only the differing files; identical files are not included
    individually but their count is in the summary.
    """
    instance_str = env.get("INSTANCE", "")
    instance = Path(os.path.expanduser(instance_str)) if instance_str else None

    items: list[dict[str, Any]] = []
    summary = {
        "repo_only": 0,
        "prism_only": 0,
        "different": 0,
        "synced": 0,
        "prism_configured": bool(instance and instance.is_dir()),
    }

    for kind in VERBATIM_DIRS:
        repo_dir = REPO / kind
        prism_dir = instance / kind if instance else None
        repo_files = walk_dir_files(repo_dir)
        prism_files = walk_dir_files(prism_dir) if prism_dir else {}

        all_paths = sorted(set(repo_files) | set(prism_files))
        for rel in all_paths:
            r = repo_files.get(rel)
            p = prism_files.get(rel)
            if r and p:
                # Compare size first (cheap), then content hash
                if r["size"] != p["size"]:
                    status = "different"
                else:
                    if r["sha1"] is None:
                        r["sha1"] = _file_sha1(r["abs"])
                    if p["sha1"] is None:
                        p["sha1"] = _file_sha1(p["abs"])
                    status = "synced" if r["sha1"] == p["sha1"] else "different"
                if status == "synced":
                    summary["synced"] += 1
                    continue
                summary["different"] += 1
                items.append({
                    "kind": kind, "relpath": rel,
                    "status": "different",
                    "repo_size": r["size"], "prism_size": p["size"],
                    "repo_abs": r["abs"], "prism_abs": p["abs"],
                })
            elif r:
                summary["repo_only"] += 1
                items.append({
                    "kind": kind, "relpath": rel,
                    "status": "repo-only",
                    "repo_size": r["size"], "prism_size": 0,
                    "repo_abs": r["abs"], "prism_abs": "",
                })
            elif p:
                summary["prism_only"] += 1
                items.append({
                    "kind": kind, "relpath": rel,
                    "status": "prism-only",
                    "repo_size": 0, "prism_size": p["size"],
                    "repo_abs": "", "prism_abs": p["abs"],
                })

    summary["total_diff"] = summary["repo_only"] + summary["prism_only"] + summary["different"]
    return {"summary": summary, "items": items}


# --- HTTP server ------------------------------------------------------------

class AppContext:
    def __init__(self) -> None:
        self.env = load_dotenv()
        self.modrinth = ModrinthClient()
        self.curseforge = CurseForgeClient(self.env.get("CURSEFORGE_API_KEY"))
        self.jobs = JobManager()
        self.icons = IconCache()
        self._lock = threading.Lock()
        # Auto-refresh: a background thread polls fs mtimes for repo + Prism
        # mod dirs every ~1.5s; when the fingerprint changes, this counter is
        # bumped so the client knows to re-fetch state without manual refresh.
        self.state_version = 0
        self._fs_fingerprint = ""
        self._watcher_stop = threading.Event()
        self._watcher_thread: threading.Thread | None = None

    def reload_env(self) -> None:
        with self._lock:
            self.env = load_dotenv()
            self.curseforge = CurseForgeClient(self.env.get("CURSEFORGE_API_KEY"))

    def watched_dirs(self) -> list[tuple[Path, str]]:
        """Returns (path, kind) where kind selects walk strategy."""
        out: list[tuple[Path, str]] = []
        for kind in METADATA_KINDS:
            out.append((REPO / kind, "metadata-shallow"))
        for kind in VERBATIM_DIRS:
            out.append((REPO / kind, "verbatim-recursive"))
        instance_str = self.env.get("INSTANCE", "")
        if instance_str:
            instance = Path(os.path.expanduser(instance_str))
            if instance.is_dir():
                for kind in METADATA_KINDS:
                    out.append((instance / kind, "metadata-shallow"))
                    out.append((instance / kind / ".index", "metadata-shallow"))
                for kind in VERBATIM_DIRS:
                    out.append((instance / kind, "verbatim-recursive"))
        return out

    def _compute_fingerprint(self) -> str:
        h = hashlib.sha1()
        for d, kind in self.watched_dirs():
            try:
                if not d.is_dir():
                    h.update(b"|missing|")
                    continue
                if kind == "metadata-shallow":
                    for entry in d.iterdir():
                        if entry.is_file() and (entry.suffix == ".toml" or entry.suffix == ".jar"):
                            try:
                                st = entry.stat()
                                h.update(f"{entry.name}|{st.st_size}|{st.st_mtime_ns}|".encode())
                            except OSError:
                                pass
                else:  # verbatim-recursive
                    for root, dirs, names in os.walk(d):
                        # Stable iteration order
                        dirs.sort()
                        names.sort()
                        for name in names:
                            try:
                                p = Path(root) / name
                                st = p.stat()
                                rel = p.relative_to(d)
                                h.update(f"{rel}|{st.st_size}|{st.st_mtime_ns}|".encode())
                            except (OSError, ValueError):
                                pass
            except OSError:
                continue
        return h.hexdigest()

    def start_watcher(self) -> None:
        if self._watcher_thread is not None:
            return
        self._fs_fingerprint = self._compute_fingerprint()

        def loop():
            while not self._watcher_stop.is_set():
                try:
                    fp = self._compute_fingerprint()
                    if fp != self._fs_fingerprint:
                        self._fs_fingerprint = fp
                        with self._lock:
                            self.state_version += 1
                except Exception:
                    pass
                self._watcher_stop.wait(1.5)

        t = threading.Thread(target=loop, daemon=True, name="fs-watcher")
        t.start()
        self._watcher_thread = t

    def bump_state_version(self) -> None:
        """Force a refresh signal; called after any handler that mutates fs."""
        with self._lock:
            self.state_version += 1


CTX: AppContext  # populated in main()


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "PackwizManager/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        # Quieter log
        if "/api/state" in (args[0] if args else ""):
            return
        sys.stderr.write("  %s - %s\n" % (self.address_string(), fmt % args))

    # --- helpers ---
    def _send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, body: str) -> None:
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> Any:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return None
        raw = self.rfile.read(length).decode("utf-8")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    def _send_sse_headers(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

    def _sse_event(self, data: str) -> None:
        for line in data.splitlines() or [""]:
            self.wfile.write(f"data: {line}\n".encode("utf-8"))
        self.wfile.write(b"\n")
        try:
            self.wfile.flush()
        except Exception:
            raise

    # --- routing ---
    def do_GET(self) -> None:
        url = urllib.parse.urlparse(self.path)
        path = url.path
        try:
            if path == "/" or path == "/index.html":
                self._send_html(INDEX_HTML)
            elif path == "/api/state":
                self._handle_state()
            elif path == "/api/state-version":
                self._send_json(200, {"version": CTX.state_version})
            elif path == "/api/server-status":
                self._handle_server_status()
            elif path == "/api/branches":
                self._handle_branches()
            elif path == "/api/jobs":
                self._handle_list_jobs()
            elif path.startswith("/api/jobs/") and path.endswith("/log"):
                jid = path.removeprefix("/api/jobs/").removesuffix("/log")
                self._handle_job_log(jid)
            elif path.startswith("/api/jobs/") and path.endswith("/stream"):
                jid = path.removeprefix("/api/jobs/").removesuffix("/stream")
                self._handle_job_stream(jid)
            elif path == "/api/settings":
                self._handle_get_settings()
            else:
                self._send_json(404, {"error": "not found"})
        except BrokenPipeError:
            pass
        except Exception as e:
            try:
                self._send_json(500, {"error": str(e), "trace": traceback.format_exc()})
            except Exception:
                pass

    def do_POST(self) -> None:
        url = urllib.parse.urlparse(self.path)
        path = url.path
        try:
            body = self._read_json()
            if path == "/api/identify":
                self._handle_identify(body)
            elif path == "/api/resolve-jar":
                self._handle_resolve_jar(body)
            elif path == "/api/resolve-duplicate":
                self._handle_resolve_duplicate(body)
            elif path == "/api/fix-cf-url":
                self._handle_fix_cf_url(body)
            elif path == "/api/sync-pw-to-prism":
                self._handle_sync_pw_to_prism(body)
            elif path == "/api/sync-pw-from-prism":
                self._handle_sync_pw_from_prism(body)
            elif path == "/api/install-missing-jar":
                self._handle_install_missing_jar(body)
            elif path == "/api/diff-pw":
                self._handle_diff_pw(body)
            elif path == "/api/configs-diff":
                self._handle_configs_diff(body)
            elif path == "/api/sync-config":
                self._handle_sync_config(body)
            elif path == "/api/view-config":
                self._handle_view_config(body)
            elif path == "/api/delete-config":
                self._handle_delete_config(body)
            elif path == "/api/inspect-pw":
                self._handle_inspect_pw(body)
            elif path == "/api/swap-to-modrinth":
                self._handle_swap_to_modrinth(body)
            elif path == "/api/swap-to-curseforge":
                self._handle_swap_to_curseforge(body)
            elif path == "/api/swap-to-custom":
                self._handle_swap_to_custom(body)
            elif path == "/api/set-side":
                self._handle_set_side(body)
            elif path == "/api/inspect-source":
                self._handle_inspect_source(body)
            elif path == "/api/delete-pw":
                self._handle_delete_pw(body)
            elif path == "/api/delete-from-prism":
                self._handle_delete_from_prism(body)
            elif path == "/api/action/local-to-prism":
                self._handle_action(body, "Local → Prism", action_local_to_prism)
            elif path == "/api/action/prism-to-local":
                self._handle_action(body, "Prism → Local", action_prism_to_local)
            elif path == "/api/action/local-to-server":
                self._handle_action(body, "Local → Server", action_deploy_to_server)
            elif path == "/api/action/github-to-prism":
                branch = (body or {}).get("branch") or GITHUB_DEFAULT_BRANCH
                self._handle_action(
                    body, f"GitHub({branch}) → Prism",
                    lambda env, jm, j: action_github_to_prism(env, jm, j, branch=branch),
                )
            elif path == "/api/settings":
                self._handle_save_settings(body)
            elif path == "/api/refresh":
                rc = self._run_packwiz_refresh()
                self._send_json(200, {"ok": rc == 0, "rc": rc})
            else:
                self._send_json(404, {"error": "not found"})
        except BrokenPipeError:
            pass
        except Exception as e:
            try:
                self._send_json(500, {"error": str(e), "trace": traceback.format_exc()})
            except Exception:
                pass

    # --- handlers ---
    def _handle_state(self) -> None:
        state = scan_state(CTX.env, CTX.icons, CTX.modrinth, CTX.curseforge)
        payload = asdict(state)
        payload["server"] = server_status(CTX.env)
        payload["meta"] = {
            "github_slug": github_remote_slug(),
            "default_branch": GITHUB_DEFAULT_BRANCH,
            "packwiz": find_binary("packwiz", CTX.env),
            "java": find_binary("java", CTX.env),
            "instance_set": bool(CTX.env.get("INSTANCE")),
            "curseforge_configured": CTX.curseforge.configured,
            "repo": str(REPO),
        }
        self._send_json(200, payload)

    def _handle_server_status(self) -> None:
        self._send_json(200, server_status(CTX.env))

    def _handle_branches(self) -> None:
        slug = github_remote_slug()
        if not slug:
            self._send_json(200, [])
            return
        self._send_json(200, list_remote_branches(slug))

    def _handle_list_jobs(self) -> None:
        out = []
        for j in CTX.jobs.jobs.values():
            out.append({
                "id": j.id, "name": j.name, "status": j.status,
                "started": j.started, "ended": j.ended,
                "rc": j.return_code,
            })
        self._send_json(200, out)

    def _handle_job_log(self, jid: str) -> None:
        j = CTX.jobs.jobs.get(jid)
        if not j:
            self._send_json(404, {"error": "no such job"})
            return
        self._send_json(200, {
            "id": j.id, "name": j.name, "status": j.status,
            "rc": j.return_code, "log": "".join(j.log),
        })

    def _handle_job_stream(self, jid: str) -> None:
        j = CTX.jobs.jobs.get(jid)
        if not j:
            self._send_json(404, {"error": "no such job"})
            return
        self._send_sse_headers()
        # First, dump existing log
        try:
            self._sse_event("".join(j.log).rstrip("\n") if j.log else "")
        except Exception:
            return
        if j.status in ("done", "error"):
            try:
                self._sse_event(f"__END__:{j.status}:{j.return_code}")
            except Exception:
                pass
            return

        q: queue.Queue = queue.Queue(maxsize=2048)
        j.subscribers.append(q)
        try:
            while True:
                try:
                    chunk = q.get(timeout=15)
                except queue.Empty:
                    # Heartbeat
                    try:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                    except Exception:
                        return
                    continue
                if chunk is None:
                    try:
                        self._sse_event(f"__END__:{j.status}:{j.return_code}")
                    except Exception:
                        pass
                    return
                try:
                    self._sse_event(chunk.rstrip("\n"))
                except Exception:
                    return
        finally:
            try:
                j.subscribers.remove(q)
            except ValueError:
                pass

    # --- mod actions ---
    def _handle_identify(self, body: dict[str, Any] | None) -> None:
        if not body or "path" not in body:
            self._send_json(400, {"error": "path required"})
            return
        p = Path(body["path"])
        if not p.is_file():
            self._send_json(404, {"error": f"file not found: {p}"})
            return
        result = identify_jar(p, CTX.modrinth, CTX.curseforge)
        self._send_json(200, result)

    def _handle_resolve_jar(self, body: dict[str, Any] | None) -> None:
        """Create a .pw.toml for a loose jar based on user's choice.

        Always writes the .pw.toml to repo/mods/<slug>.pw.toml. If the source
        jar lives in Prism's mods/ but not the repo's, copies it into the repo
        first (so the Custom mode's GitHub raw URL resolves and so the next
        Local→Prism stays consistent). Mirrors the new .pw.toml into Prism's
        mods/.index/ if a Prism instance is configured, so both repo-side and
        Prism-side loose-jar warnings clear in one click.
        """
        if not body:
            self._send_json(400, {"error": "body required"})
            return
        jar = Path(body.get("path", ""))
        choice = body.get("choice")  # 'modrinth' | 'curseforge' | 'custom'
        target_dir = Path(body.get("target_dir") or (REPO / "mods"))
        slug = body.get("slug") or jar.stem.replace(" ", "-").lower()
        if not jar.is_file():
            self._send_json(404, {"error": f"file not found: {jar}"})
            return

        kind = target_dir.name if target_dir.name in METADATA_KINDS else "mods"
        instance_str = CTX.env.get("INSTANCE", "")
        instance = Path(os.path.expanduser(instance_str)) if instance_str else None

        # Ensure the jar lives in the repo so it's actually trackable + the
        # GitHub raw URL (Custom mode) resolves. If it's only in Prism, copy it.
        repo_kind_dir = REPO / kind
        repo_jar = repo_kind_dir / jar.name
        try:
            if not repo_jar.exists() and not str(jar.resolve()).startswith(str(REPO.resolve())):
                repo_kind_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(jar, repo_jar)
        except Exception as e:
            self._send_json(500, {"error": f"failed to copy jar into repo: {e}"})
            return
        # Hash from whichever copy exists; for Custom mode in particular, we
        # want the hash to match the file at the GitHub raw URL.
        jar_for_meta = repo_jar if repo_jar.exists() else jar

        out_path = target_dir / f"{slug}.pw.toml"
        try:
            if choice == "modrinth":
                version = body.get("modrinth_version") or {}
                project = body.get("modrinth_project") or {}
                if not version:
                    h = hash_file(jar_for_meta)
                    version = CTX.modrinth.lookup_by_hash(h["sha512"], h["sha1"]) or {}
                if not version:
                    self._send_json(400, {"error": "no Modrinth version supplied or found"})
                    return
                pw = build_pw_toml_from_modrinth(jar_for_meta, version, project)
            elif choice == "curseforge":
                match = body.get("curseforge_match")
                if not match:
                    h = hash_file(jar_for_meta)
                    match = CTX.curseforge.lookup_by_fingerprint(int(h["murmur2"]))
                if not match:
                    self._send_json(400, {"error": "no CurseForge match supplied or found"})
                    return
                pw = build_pw_toml_from_curseforge(jar_for_meta, match, use_direct_url=True, cf_client=CTX.curseforge)
            elif choice == "custom":
                slug_gh = github_remote_slug()
                if not slug_gh:
                    self._send_json(400, {"error": "GitHub remote not detected; can't build raw URL"})
                    return
                branch = body.get("branch") or GITHUB_DEFAULT_BRANCH
                display = body.get("name")
                pw = build_pw_toml_custom(jar_for_meta, slug_gh, branch=branch, display_name=display)
            else:
                self._send_json(400, {"error": f"unknown choice: {choice!r}"})
                return

            target_dir.mkdir(parents=True, exist_ok=True)
            write_pw_toml(out_path, pw)

            mirrored_to_prism = ""
            if instance and instance.is_dir():
                prism_index = instance / kind / ".index"
                prism_index.mkdir(parents=True, exist_ok=True)
                shutil.copy2(out_path, prism_index / out_path.name)
                mirrored_to_prism = str(prism_index / out_path.name)

            self._send_json(200, {
                "ok": True,
                "wrote": str(out_path),
                "mirrored_to_prism": mirrored_to_prism,
                "jar_copied_to_repo": str(repo_jar) if repo_jar.exists() and not jar.samefile(repo_jar) else "",
            })
        except Exception as e:
            self._send_json(500, {"error": str(e), "trace": traceback.format_exc()})

    def _handle_resolve_duplicate(self, body: dict[str, Any] | None) -> None:
        if not body:
            self._send_json(400, {"error": "body required"})
            return
        keep = body.get("keep")           # path to keep
        delete = body.get("delete") or [] # list of paths to delete
        if not keep or not delete:
            self._send_json(400, {"error": "keep + delete required"})
            return
        deleted: list[str] = []
        mirrored: list[str] = []
        try:
            for p in delete:
                rp = Path(p)
                if rp.is_file():
                    rp.unlink()
                    deleted.append(p)
                # Also remove the matching entry from Prism's .index/ so the
                # two sides stay in sync.
                m = self._delete_matching_prism_index(rp)
                if m:
                    mirrored.append(m)
            self._send_json(200, {"ok": True, "deleted": deleted, "mirrored_deletes": mirrored})
        except Exception as e:
            self._send_json(500, {"error": str(e)})

    def _delete_matching_prism_index(self, repo_pw: Path) -> str:
        """If repo_pw is mods/X.pw.toml in this repo, delete <INSTANCE>/mods/.index/X.pw.toml
        too. Returns the path that was deleted, or '' if nothing was deleted."""
        instance_str = CTX.env.get("INSTANCE", "")
        if not instance_str:
            return ""
        instance = Path(os.path.expanduser(instance_str))
        if not instance.is_dir():
            return ""
        try:
            rel = repo_pw.relative_to(REPO)
        except ValueError:
            return ""
        if not rel.parts or rel.parts[0] not in METADATA_KINDS:
            return ""
        prism_match = instance / rel.parts[0] / ".index" / repo_pw.name
        if prism_match.is_file():
            prism_match.unlink()
            return str(prism_match)
        return ""

    def _handle_fix_cf_url(self, body: dict[str, Any] | None) -> None:
        if not body:
            self._send_json(400, {"error": "body required"})
            return
        if not CTX.curseforge.configured:
            self._send_json(400, {"error": "CurseForge API key not configured (Settings tab)"})
            return
        path = body.get("path")
        if not path:
            self._send_json(400, {"error": "path required"})
            return
        e = parse_mod_entry(Path(path))
        if not e:
            self._send_json(404, {"error": "couldn't parse .pw.toml"})
            return
        ok = fix_curseforge_url(e, CTX.curseforge)
        if ok:
            self._send_json(200, {"ok": True})
        else:
            self._send_json(400, {"error": "no download URL available (project may disable third-party downloads)"})

    def _handle_inspect_pw(self, body: dict[str, Any] | None) -> None:
        """Look at an existing .pw.toml and report what fix options exist:
        Modrinth match by hash, CurseForge direct URL, etc."""
        if not body or not body.get("path"):
            self._send_json(400, {"error": "path required"})
            return
        path = Path(body["path"])
        e = parse_mod_entry(path)
        if not e:
            self._send_json(404, {"error": "couldn't parse .pw.toml"})
            return

        modrinth: dict[str, Any] | None = None
        modrinth_project: dict[str, Any] | None = None
        if e.download_hash and e.download_hash_format in ("sha1", "sha512"):
            s, body_ = http_get_json(
                f"{MODRINTH_API}/version_file/{e.download_hash}?algorithm={e.download_hash_format}"
            )
            if s == 200 and isinstance(body_, dict):
                modrinth = body_
        if modrinth and modrinth.get("project_id"):
            modrinth_project = CTX.modrinth.get_project(modrinth["project_id"])

        cf_direct_url: str | None = None
        if e.curseforge_project and e.curseforge_file and CTX.curseforge.configured:
            cf_direct_url = CTX.curseforge.get_download_url(e.curseforge_project, e.curseforge_file)

        self._send_json(200, {
            "entry": _entry_dict(e),
            "modrinth_match": modrinth,
            "modrinth_project": modrinth_project,
            "curseforge_direct_url": cf_direct_url,
        })

    def _handle_swap_to_modrinth(self, body: dict[str, Any] | None) -> None:
        """Rewrite a .pw.toml to use Modrinth metadata in place of CurseForge."""
        if not body or not body.get("path"):
            self._send_json(400, {"error": "path required"})
            return
        path = Path(body["path"])
        e = parse_mod_entry(path)
        if not e:
            self._send_json(404, {"error": "couldn't parse .pw.toml"})
            return

        version = body.get("modrinth_version")
        if not version:
            if e.download_hash and e.download_hash_format in ("sha1", "sha512"):
                s, body_ = http_get_json(
                    f"{MODRINTH_API}/version_file/{e.download_hash}?algorithm={e.download_hash_format}"
                )
                if s == 200 and isinstance(body_, dict):
                    version = body_
        if not version:
            self._send_json(400, {"error": "no Modrinth version found"})
            return

        files = version.get("files") or []
        primary = next((f for f in files if f.get("primary")), files[0] if files else {})
        hashes = primary.get("hashes") or {}

        raw = e.raw
        # Optional: refresh display name from project
        if version.get("project_id"):
            project = CTX.modrinth.get_project(version["project_id"])
            if project and project.get("title"):
                raw["name"] = project["title"]
        raw["filename"] = primary.get("filename") or raw.get("filename", "")
        raw.setdefault("download", {})
        raw["download"]["hash-format"] = "sha512"
        raw["download"]["hash"] = hashes.get("sha512", "")
        raw["download"]["mode"] = "url"
        raw["download"]["url"] = primary.get("url", "")
        raw["update"] = {"modrinth": {
            "mod-id": version.get("project_id", ""),
            "version": version.get("id", ""),
        }}

        try:
            write_pw_toml(path, raw)
            self._send_json(200, {"ok": True, "wrote": str(path)})
        except Exception as e2:
            self._send_json(500, {"error": str(e2), "trace": traceback.format_exc()})

    def _handle_set_side(self, body: dict[str, Any] | None) -> None:
        if not body or not body.get("path") or "side" not in body:
            self._send_json(400, {"error": "path and side required"})
            return
        side = body["side"]
        if side not in ("client", "server", "both"):
            self._send_json(400, {"error": f"invalid side: {side!r}"})
            return
        path = Path(body["path"])
        e = parse_mod_entry(path)
        if not e:
            self._send_json(404, {"error": "couldn't parse .pw.toml"})
            return
        raw = e.raw
        raw["side"] = side
        try:
            write_pw_toml(path, raw)
        except Exception as e2:
            self._send_json(500, {"error": str(e2)})
            return
        # Mirror to Prism .index/ if present
        mirrored = ""
        instance_str = CTX.env.get("INSTANCE", "")
        instance = Path(os.path.expanduser(instance_str)) if instance_str else None
        if instance and instance.is_dir():
            try:
                rel = path.relative_to(REPO)
                if rel.parts and rel.parts[0] in METADATA_KINDS:
                    pm_path = instance / rel.parts[0] / ".index" / path.name
                    if pm_path.is_file():
                        pm_e = parse_mod_entry(pm_path)
                        if pm_e:
                            pm_raw = pm_e.raw
                            pm_raw["side"] = side
                            write_pw_toml(pm_path, pm_raw)
                            mirrored = str(pm_path)
            except ValueError:
                pass
        self._send_json(200, {"ok": True, "mirrored": mirrored})

    def _handle_inspect_source(self, body: dict[str, Any] | None) -> None:
        """Return all viable source-swap targets for a .pw.toml: Modrinth match
        (by hash), CurseForge candidates (by hash if jar present, else by name),
        and Custom (always available if a GitHub remote is set)."""
        if not body or not body.get("path"):
            self._send_json(400, {"error": "path required"})
            return
        path = Path(body["path"])
        e = parse_mod_entry(path)
        if not e:
            self._send_json(404, {"error": "couldn't parse .pw.toml"})
            return

        # Modrinth via hash
        modrinth_match: dict[str, Any] | None = None
        modrinth_project: dict[str, Any] | None = None
        if e.download_hash and e.download_hash_format in ("sha1", "sha512"):
            s, b = http_get_json(
                f"{MODRINTH_API}/version_file/{e.download_hash}?algorithm={e.download_hash_format}"
            )
            if s == 200 and isinstance(b, dict):
                modrinth_match = b
        if modrinth_match and modrinth_match.get("project_id"):
            modrinth_project = CTX.modrinth.get_project(modrinth_match["project_id"])

        # CurseForge candidates
        cf_candidates: list[dict[str, Any]] = []
        if CTX.curseforge.configured:
            # If we can fingerprint the local jar, prefer that (exact match)
            repo_jar = REPO / "mods" / e.filename if e.filename else None
            fp_match = None
            if repo_jar and repo_jar.is_file():
                try:
                    h = hash_file(repo_jar)
                    fp_match = CTX.curseforge.lookup_by_fingerprint(int(h["murmur2"]))
                except Exception:
                    pass
            if fp_match:
                file_info = fp_match.get("file") or fp_match
                mid = file_info.get("modId") or fp_match.get("id")
                mod = CTX.curseforge.get_mod(mid) if mid else None
                cf_candidates.append({"match": fp_match, "mod": mod, "exact": True})
            else:
                # Search by name
                query = e.name or e.slug
                results = CTX.curseforge.search(
                    query, mc_version=CTX.env.get("MC_VERSION", ""), loader_id=6,
                )[:5]  # 6 = NeoForge
                for r in results:
                    cf_candidates.append({"mod": r, "exact": False})

        github_slug = github_remote_slug()

        self._send_json(200, {
            "entry": _entry_dict(e, CTX.icons),
            "current_source": e.provider,
            "modrinth_match": modrinth_match,
            "modrinth_project": modrinth_project,
            "curseforge_candidates": cf_candidates,
            "curseforge_configured": CTX.curseforge.configured,
            "github_slug": github_slug,
        })

    def _handle_swap_to_curseforge(self, body: dict[str, Any] | None) -> None:
        """Rewrite a .pw.toml to use CurseForge metadata. Caller must supply
        a `match` (from /api/inspect-source) or a `project_id` + `file_id`."""
        if not body or not body.get("path"):
            self._send_json(400, {"error": "path required"})
            return
        if not CTX.curseforge.configured:
            self._send_json(400, {"error": "CurseForge API key not configured"})
            return
        path = Path(body["path"])
        e = parse_mod_entry(path)
        if not e:
            self._send_json(404, {"error": "couldn't parse .pw.toml"})
            return

        match = body.get("curseforge_match") or {}
        project_id = body.get("project_id")
        file_id = body.get("file_id")
        if match and not (project_id and file_id):
            f = match.get("file") or match
            project_id = f.get("modId") or match.get("id")
            file_id = f.get("id")

        if not (project_id and file_id):
            # User picked a project but no file; fetch latest matching file
            project_id = body.get("project_id") or project_id
            mc = CTX.env.get("MC_VERSION", "")
            mod = CTX.curseforge.get_mod(project_id) if project_id else None
            files = (mod or {}).get("latestFiles") or []
            chosen = None
            for f in files:
                gv = f.get("gameVersions") or []
                if (not mc or mc in gv) and "NeoForge" in gv:
                    chosen = f
                    break
            if not chosen and files:
                chosen = files[0]
            if chosen:
                file_id = chosen.get("id")

        if not (project_id and file_id):
            self._send_json(400, {"error": "no CurseForge file resolved"})
            return

        file_data = CTX.curseforge.get_file(project_id, file_id) or {}
        download_url = CTX.curseforge.get_download_url(project_id, file_id) or ""
        mod = CTX.curseforge.get_mod(project_id) or {}
        file_hashes = file_data.get("hashes") or []
        sha1 = next((h.get("value") for h in file_hashes if h.get("algo") == 1), "")

        raw = e.raw
        raw["filename"] = file_data.get("fileName") or raw.get("filename", "")
        if mod.get("name"):
            raw["name"] = mod["name"]
        raw.setdefault("download", {})
        raw["download"]["hash-format"] = "sha1"
        raw["download"]["hash"] = sha1
        if download_url:
            raw["download"]["mode"] = "url"
            raw["download"]["url"] = download_url
        else:
            raw["download"]["mode"] = "metadata:curseforge"
            raw["download"]["url"] = ""
        raw["update"] = {"curseforge": {"file-id": file_id, "project-id": project_id}}
        try:
            write_pw_toml(path, raw)
            self._send_json(200, {"ok": True, "wrote": str(path)})
        except Exception as e2:
            self._send_json(500, {"error": str(e2)})

    def _handle_swap_to_custom(self, body: dict[str, Any] | None) -> None:
        """Rewrite a .pw.toml to mode='url' with a raw GitHub URL."""
        if not body or not body.get("path"):
            self._send_json(400, {"error": "path required"})
            return
        path = Path(body["path"])
        e = parse_mod_entry(path)
        if not e:
            self._send_json(404, {"error": "couldn't parse .pw.toml"})
            return
        slug_gh = github_remote_slug()
        if not slug_gh:
            self._send_json(400, {"error": "no GitHub remote configured"})
            return
        branch = body.get("branch") or GITHUB_DEFAULT_BRANCH

        # We need the jar present in repo/mods/ so the GitHub raw URL resolves.
        # If it's not there, we can't responsibly mark as custom.
        kind_dir = path.parent.name if path.parent.name in METADATA_KINDS else "mods"
        jar = REPO / kind_dir / e.filename if e.filename else None
        if not (jar and jar.is_file()):
            self._send_json(400, {
                "error": (f"Jar not present in repo/{kind_dir}/{e.filename}. "
                          "Custom mode requires the jar to be committed to the repo so the GitHub raw URL resolves.")
            })
            return

        h = hash_file(jar)
        raw_url = f"https://raw.githubusercontent.com/{slug_gh}/{branch}/{kind_dir}/{e.filename}"
        raw = e.raw
        raw["filename"] = e.filename
        raw.setdefault("download", {})
        raw["download"]["hash-format"] = "sha512"
        raw["download"]["hash"] = h["sha512"]
        raw["download"]["mode"] = "url"
        raw["download"]["url"] = raw_url
        raw.pop("update", None)  # custom has no update tracker
        try:
            write_pw_toml(path, raw)
            self._send_json(200, {"ok": True, "wrote": str(path)})
        except Exception as e2:
            self._send_json(500, {"error": str(e2)})

    def _handle_sync_pw_to_prism(self, body: dict[str, Any] | None) -> None:
        """Copy a single repo .pw.toml into Prism's .index/ AND download the
        actual jar referenced by it. The user explicitly clicked sync on a
        specific mod — they want the mod working, not just the metadata."""
        if not body:
            self._send_json(400, {"error": "body required"})
            return
        repo_pw = body.get("repo_pw")
        if not repo_pw:
            self._send_json(400, {"error": "repo_pw path required"})
            return
        src = Path(repo_pw)
        if not src.is_file():
            self._send_json(404, {"error": f"file not found: {src}"})
            return
        instance_str = CTX.env.get("INSTANCE", "")
        instance = Path(os.path.expanduser(instance_str)) if instance_str else None
        if not (instance and instance.is_dir()):
            self._send_json(400, {"error": "Prism instance not configured / not found"})
            return
        try:
            rel = src.relative_to(REPO)
        except ValueError:
            self._send_json(400, {"error": "repo_pw isn't inside the repo"})
            return
        kind = rel.parts[0] if rel.parts else ""
        if kind not in METADATA_KINDS:
            self._send_json(400, {"error": f"unsupported kind: {kind!r}"})
            return
        dst_dir = instance / kind / ".index"
        dst_dir.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(src, dst_dir / src.name)
        except Exception as e:
            self._send_json(500, {"error": f"copying .pw.toml failed: {e}"})
            return

        # Also fetch the jar — the metadata is useless without the actual file.
        jar_ok, jar_status, jar_msg = download_pw_jar(src, instance, CTX.curseforge)
        CTX.bump_state_version()

        response: dict[str, Any] = {
            "ok": True,
            "wrote": str(dst_dir / src.name),
            "jar_status": jar_status,
            "jar_message": jar_msg,
        }
        if not jar_ok:
            # .pw.toml copied but jar install failed — still return 200 so the
            # client can show both pieces of info; flag the jar issue.
            response["warning"] = jar_msg
        self._send_json(200, response)

    def _handle_configs_diff(self, body: dict[str, Any] | None) -> None:
        """Walk both repo VERBATIM_DIRS and Prism's, return per-file diff."""
        try:
            self._send_json(200, diff_config_dirs(CTX.env))
        except Exception as e:
            self._send_json(500, {"error": str(e), "trace": traceback.format_exc()})

    def _validate_config_target(self, kind: str, relpath: str) -> tuple[Path | None, Path | None, str]:
        """Resolve {kind, relpath} to (repo_path, prism_path, error). Both
        paths are returned even if files don't exist there yet."""
        if kind not in VERBATIM_DIRS:
            return None, None, f"unsupported kind: {kind!r}"
        if not relpath or ".." in relpath.split("/") or relpath.startswith("/"):
            return None, None, f"invalid relpath: {relpath!r}"
        instance_str = CTX.env.get("INSTANCE", "")
        if not instance_str:
            return None, None, "Prism INSTANCE not configured"
        instance = Path(os.path.expanduser(instance_str))
        if not instance.is_dir():
            return None, None, f"Prism instance not found at {instance}"
        repo_path = (REPO / kind / relpath).resolve()
        prism_path = (instance / kind / relpath).resolve()
        # Ensure paths stayed within their roots after resolve()
        repo_root = (REPO / kind).resolve()
        prism_root = (instance / kind).resolve()
        if not str(repo_path).startswith(str(repo_root)):
            return None, None, "path escapes repo root"
        if not str(prism_path).startswith(str(prism_root)):
            return None, None, "path escapes prism root"
        return repo_path, prism_path, ""

    def _handle_sync_config(self, body: dict[str, Any] | None) -> None:
        """Copy a single config file in either direction."""
        if not body:
            self._send_json(400, {"error": "body required"})
            return
        kind = body.get("kind", "")
        relpath = body.get("relpath", "")
        direction = body.get("direction", "")
        if direction not in ("push", "pull"):
            self._send_json(400, {"error": "direction must be 'push' or 'pull'"})
            return
        repo_path, prism_path, err = self._validate_config_target(kind, relpath)
        if err:
            self._send_json(400, {"error": err})
            return
        try:
            if direction == "push":
                if not repo_path.is_file():
                    self._send_json(404, {"error": f"source not found: {repo_path}"})
                    return
                prism_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(repo_path, prism_path)
                self._send_json(200, {"ok": True, "wrote": str(prism_path)})
            else:  # pull
                if not prism_path.is_file():
                    self._send_json(404, {"error": f"source not found: {prism_path}"})
                    return
                repo_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(prism_path, repo_path)
                self._send_json(200, {"ok": True, "wrote": str(repo_path)})
            CTX.bump_state_version()
        except Exception as e:
            self._send_json(500, {"error": str(e)})

    def _handle_view_config(self, body: dict[str, Any] | None) -> None:
        """Return raw content of a config file from both sides for diff display.
        Skips binary files larger than 1 MiB."""
        if not body:
            self._send_json(400, {"error": "body required"})
            return
        kind = body.get("kind", "")
        relpath = body.get("relpath", "")
        repo_path, prism_path, err = self._validate_config_target(kind, relpath)
        if err:
            self._send_json(400, {"error": err})
            return

        MAX_TEXT_SIZE = 512 * 1024  # 512 KiB

        def read_side(p: Path) -> dict[str, Any]:
            if not p.is_file():
                return {"exists": False}
            try:
                size = p.stat().st_size
            except OSError as e:
                return {"exists": True, "error": str(e)}
            if size > MAX_TEXT_SIZE:
                return {"exists": True, "size": size, "too_large": True}
            try:
                data = p.read_bytes()
            except OSError as e:
                return {"exists": True, "error": str(e)}
            # Heuristic: if a NUL byte appears in the first 4KB, treat as binary
            sample = data[:4096]
            is_binary = b"\x00" in sample
            if is_binary:
                return {"exists": True, "size": size, "binary": True}
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                try:
                    text = data.decode("latin-1")
                except Exception:
                    return {"exists": True, "size": size, "binary": True}
            return {"exists": True, "size": size, "text": text}

        self._send_json(200, {
            "kind": kind, "relpath": relpath,
            "repo": read_side(repo_path),
            "prism": read_side(prism_path),
        })

    def _handle_delete_config(self, body: dict[str, Any] | None) -> None:
        """Delete a config file from one side."""
        if not body:
            self._send_json(400, {"error": "body required"})
            return
        kind = body.get("kind", "")
        relpath = body.get("relpath", "")
        side = body.get("side", "")
        if side not in ("repo", "prism"):
            self._send_json(400, {"error": "side must be 'repo' or 'prism'"})
            return
        repo_path, prism_path, err = self._validate_config_target(kind, relpath)
        if err:
            self._send_json(400, {"error": err})
            return
        target = repo_path if side == "repo" else prism_path
        if not target.is_file():
            self._send_json(404, {"error": f"not found: {target}"})
            return
        try:
            target.unlink()
            CTX.bump_state_version()
            self._send_json(200, {"ok": True, "deleted": str(target)})
        except Exception as e:
            self._send_json(500, {"error": str(e)})

    def _handle_diff_pw(self, body: dict[str, Any] | None) -> None:
        """Return both repo and Prism versions of a .pw.toml + per-field diff."""
        if not body or not body.get("name"):
            self._send_json(400, {"error": "name required"})
            return
        name = body["name"]
        kind = body.get("kind") or "mods"
        if kind not in METADATA_KINDS:
            self._send_json(400, {"error": f"unsupported kind: {kind!r}"})
            return

        repo_path = REPO / kind / name
        instance_str = CTX.env.get("INSTANCE", "")
        instance = Path(os.path.expanduser(instance_str)) if instance_str else None
        prism_path = (instance / kind / ".index" / name) if (instance and instance.is_dir()) else None

        def side_payload(p: Path | None) -> dict[str, Any]:
            if not p or not p.is_file():
                return {"exists": False, "raw": "", "parsed": None}
            try:
                raw_text = p.read_text(encoding="utf-8")
            except OSError as e:
                return {"exists": False, "raw": "", "parsed": None, "error": str(e)}
            entry = parse_mod_entry(p)
            return {
                "exists": True,
                "path": str(p),
                "raw": raw_text,
                "parsed": _entry_dict(entry, CTX.icons) if entry else None,
            }

        repo_data = side_payload(repo_path)
        prism_data = side_payload(prism_path)

        # Per-field summary for the table view in the modal
        fields = [
            ("filename", "Filename"),
            ("name", "Name"),
            ("side", "Side"),
            ("download_mode", "Download mode"),
            ("download_url", "Download URL"),
            ("download_hash", "Hash"),
            ("download_hash_format", "Hash format"),
            ("modrinth_id", "Modrinth project"),
            ("modrinth_version", "Modrinth version"),
            ("curseforge_project", "CurseForge project"),
            ("curseforge_file", "CurseForge file"),
        ]
        summary: list[dict[str, Any]] = []
        for key, label in fields:
            rv = (repo_data.get("parsed") or {}).get(key, "") if repo_data.get("exists") else ""
            pv = (prism_data.get("parsed") or {}).get(key, "") if prism_data.get("exists") else ""
            rv = "" if rv is None else rv
            pv = "" if pv is None else pv
            differs = rv != pv and not (not repo_data.get("exists") or not prism_data.get("exists"))
            summary.append({
                "field": key, "label": label,
                "repo": str(rv), "prism": str(pv), "differs": differs,
            })

        self._send_json(200, {
            "name": name, "kind": kind,
            "repo": repo_data, "prism": prism_data, "summary": summary,
        })

    def _handle_install_missing_jar(self, body: dict[str, Any] | None) -> None:
        """Install the jar referenced by a Prism .index/ .pw.toml file.

        Used when an entry in Prism's .index/ exists but the jar isn't in
        <instance>/<kind>/ — e.g. a side='server' mod that the bootstrapper
        skipped, or a partial install.
        """
        if not body:
            self._send_json(400, {"error": "body required"})
            return
        pw_path = body.get("pw_toml_path") or body.get("path")
        if not pw_path:
            self._send_json(400, {"error": "pw_toml_path required"})
            return
        src = Path(pw_path)
        if not src.is_file():
            self._send_json(404, {"error": f"file not found: {src}"})
            return
        instance_str = CTX.env.get("INSTANCE", "")
        instance = Path(os.path.expanduser(instance_str)) if instance_str else None
        if not (instance and instance.is_dir()):
            self._send_json(400, {"error": "Prism instance not configured / not found"})
            return
        # Confirm the .pw.toml is under this Prism instance (not arbitrary path)
        try:
            rel = src.resolve().relative_to(instance.resolve())
        except ValueError:
            self._send_json(400, {"error": ".pw.toml is not inside the Prism instance"})
            return
        if not rel.parts or rel.parts[0] not in METADATA_KINDS:
            self._send_json(400, {"error": f"unsupported location: {rel}"})
            return

        ok, status, msg = download_pw_jar(src, instance, CTX.curseforge, kind=rel.parts[0])
        if ok:
            CTX.bump_state_version()
            self._send_json(200, {"ok": True, "status": status, "message": msg})
        else:
            self._send_json(400, {"ok": False, "status": status, "error": msg})

    def _handle_sync_pw_from_prism(self, body: dict[str, Any] | None) -> None:
        """Copy a single Prism .index/ .pw.toml into the repo. Used when a mod
        was added/changed via Prism's UI and needs to be tracked in the repo."""
        if not body or not body.get("name"):
            self._send_json(400, {"error": "name required"})
            return
        name = body["name"]
        kind = body.get("kind") or "mods"
        if not name.endswith(".pw.toml"):
            self._send_json(400, {"error": ".pw.toml name required"})
            return
        if kind not in METADATA_KINDS:
            self._send_json(400, {"error": f"unsupported kind: {kind!r}"})
            return
        instance_str = CTX.env.get("INSTANCE", "")
        instance = Path(os.path.expanduser(instance_str)) if instance_str else None
        if not (instance and instance.is_dir()):
            self._send_json(400, {"error": "Prism instance not configured / not found"})
            return
        src = instance / kind / ".index" / name
        if not src.is_file():
            self._send_json(404, {"error": f"not found: {src}"})
            return
        dst_dir = REPO / kind
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / name
        try:
            shutil.copy2(src, dst)
            self._send_json(200, {"ok": True, "wrote": str(dst)})
        except Exception as e:
            self._send_json(500, {"error": str(e)})

    def _handle_delete_pw(self, body: dict[str, Any] | None) -> None:
        if not body:
            self._send_json(400, {"error": "body required"})
            return
        path = body.get("path")
        if not path or not path.endswith(".pw.toml"):
            self._send_json(400, {"error": ".pw.toml path required"})
            return
        try:
            rp = Path(path)
            rp.unlink(missing_ok=True)
            mirrored = self._delete_matching_prism_index(rp)
            self._send_json(200, {"ok": True, "mirrored_delete": mirrored})
        except Exception as e:
            self._send_json(500, {"error": str(e)})

    def _handle_delete_from_prism(self, body: dict[str, Any] | None) -> None:
        """Delete a single .pw.toml from Prism's .index/ (orphaned, repo doesn't have it)."""
        if not body:
            self._send_json(400, {"error": "body required"})
            return
        name = body.get("name")
        kind = body.get("kind") or "mods"
        if not name or not name.endswith(".pw.toml"):
            self._send_json(400, {"error": ".pw.toml name required"})
            return
        if kind not in METADATA_KINDS:
            self._send_json(400, {"error": f"unsupported kind: {kind!r}"})
            return
        instance_str = CTX.env.get("INSTANCE", "")
        instance = Path(os.path.expanduser(instance_str)) if instance_str else None
        if not (instance and instance.is_dir()):
            self._send_json(400, {"error": "Prism instance not configured / not found"})
            return
        target = instance / kind / ".index" / name
        if not target.is_file():
            self._send_json(404, {"error": f"not found: {target}"})
            return
        try:
            target.unlink()
            self._send_json(200, {"ok": True, "deleted": str(target)})
        except Exception as e:
            self._send_json(500, {"error": str(e)})

    # --- main actions ---
    def _handle_action(self, body: dict[str, Any] | None, label: str, fn) -> None:
        try:
            job = CTX.jobs.submit(label, lambda j: fn(CTX.env, CTX.jobs, j))
        except RuntimeError as e:
            self._send_json(409, {"error": str(e)})
            return
        self._send_json(200, {"job_id": job.id, "name": job.name})

    # --- settings ---
    def _handle_get_settings(self) -> None:
        env = CTX.env
        out = {
            "INSTANCE": env.get("INSTANCE", ""),
            "PACK_URL": env.get("PACK_URL", ""),
            "CRAFTY_URL": env.get("CRAFTY_URL", ""),
            "CRAFTY_SERVER_ID": env.get("CRAFTY_SERVER_ID", ""),
            "CRAFTY_INSECURE": env.get("CRAFTY_INSECURE", "true"),
            "CRAFTY_SSH_HOST": env.get("CRAFTY_SSH_HOST", ""),
            "CRAFTY_REMOTE_ROOT": env.get("CRAFTY_REMOTE_ROOT", ""),
            "PACKWIZ_BIN": env.get("PACKWIZ_BIN", ""),
            "JAVA_BIN": env.get("JAVA_BIN", ""),
            "CURSEFORGE_API_KEY_set": bool(env.get("CURSEFORGE_API_KEY")),
            "CRAFTY_TOKEN_set": bool(env.get("CRAFTY_TOKEN")),
            "_repo_path": str(REPO),
            "_packwiz_resolved": find_binary("packwiz", env) or "",
            "_java_resolved": find_binary("java", env) or "",
        }
        self._send_json(200, out)

    def _handle_save_settings(self, body: dict[str, Any] | None) -> None:
        if not body:
            self._send_json(400, {"error": "body required"})
            return
        allowed = {
            "INSTANCE", "PACK_URL", "CRAFTY_URL", "CRAFTY_SERVER_ID",
            "CRAFTY_INSECURE", "CRAFTY_SSH_HOST", "CRAFTY_REMOTE_ROOT",
            "CURSEFORGE_API_KEY", "CRAFTY_TOKEN", "PACKWIZ_BIN", "JAVA_BIN",
        }
        updates = {k: str(v) for k, v in body.items() if k in allowed and v != ""}
        if updates:
            save_dotenv(updates)
            CTX.reload_env()
        self._send_json(200, {"ok": True, "updated": list(updates.keys())})

    def _run_packwiz_refresh(self) -> int:
        bin_ = find_binary("packwiz")
        if not bin_:
            return 127
        try:
            r = subprocess.run([bin_, "refresh"], cwd=str(REPO))
            return r.returncode
        except Exception:
            return 1


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


# --- HTML/JS UI (defined at end of file) ------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0,
                        help=f"listen port (default: pick free in {DEFAULT_PORT_RANGE[0]}-{DEFAULT_PORT_RANGE[1]})")
    parser.add_argument("--no-browser", action="store_true",
                        help="don't auto-open the browser")
    args = parser.parse_args()

    global CTX
    CTX = AppContext()
    CTX.start_watcher()

    port = args.port
    if port == 0:
        for candidate in range(DEFAULT_PORT_RANGE[0], DEFAULT_PORT_RANGE[1]):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(("127.0.0.1", candidate))
                port = candidate
                break
            except OSError:
                continue

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"packwiz-manager")
    print(f"  repo:    {REPO}")
    print(f"  packwiz: {find_binary('packwiz') or '(not found)'}")
    print(f"  github:  {github_remote_slug() or '(no origin)'}")
    print(f"  serving: {url}")

    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    return 0


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Packwiz Manager</title>
<style>
:root {
  --bg: #14161b;
  --panel: #1c1f26;
  --panel-2: #232730;
  --border: #2e333d;
  --text: #e6e9ef;
  --muted: #8a93a3;
  --accent: #5db8ff;
  --accent-hover: #7cc7ff;
  --warn: #f0b454;
  --error: #ed6f6f;
  --ok: #6dcf83;
  --info: #7ea4d8;
  --shadow: 0 6px 28px rgba(0,0,0,0.45);
  --mono: ui-monospace, "JetBrains Mono", Menlo, monospace;
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; height: 100%; }
body {
  background: var(--bg);
  color: var(--text);
  font: 14px/1.5 system-ui, -apple-system, Segoe UI, Roboto, Inter, sans-serif;
}
header {
  display: flex; align-items: center; gap: 16px;
  padding: 12px 20px;
  background: var(--panel);
  border-bottom: 1px solid var(--border);
}
header h1 {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  letter-spacing: 0.2px;
}
header .badges { display: flex; gap: 8px; flex: 1; flex-wrap: wrap; }
header .badge {
  font-size: 12px;
  padding: 4px 10px;
  border-radius: 10px;
  background: var(--panel-2);
  border: 1px solid var(--border);
}
header .badge.ok { color: var(--ok); border-color: rgba(109,207,131,0.3); }
header .badge.warn { color: var(--warn); border-color: rgba(240,180,84,0.3); }
header .badge.err { color: var(--error); border-color: rgba(237,111,111,0.3); }
header .badge .dot {
  display: inline-block; width: 8px; height: 8px;
  border-radius: 50%; margin-right: 6px; background: currentColor;
  vertical-align: middle;
}
header .actions { display: flex; gap: 8px; }
button, .btn {
  background: var(--panel-2);
  color: var(--text);
  border: 1px solid var(--border);
  padding: 6px 12px;
  border-radius: 6px;
  cursor: pointer;
  font: inherit;
}
button:hover:not([disabled]) { background: #2a2f3a; }
button:disabled { opacity: 0.5; cursor: not-allowed; }
button.primary { background: #1d4f80; border-color: #2e6aa3; color: #fff; }
button.primary:hover:not([disabled]) { background: #2768a6; }
button.danger { background: #5e2e2e; border-color: #823f3f; color: #ffd9d9; }
button.danger:hover:not([disabled]) { background: #783939; }
button.small { font-size: 12px; padding: 3px 8px; }

nav.tabs {
  display: flex; gap: 0; padding: 0 20px;
  background: var(--panel);
  border-bottom: 1px solid var(--border);
}
nav.tabs button {
  background: transparent; border: none; border-bottom: 2px solid transparent;
  border-radius: 0; padding: 12px 18px; color: var(--muted);
}
nav.tabs button.active { color: var(--text); border-bottom-color: var(--accent); }
nav.tabs button:hover { color: var(--text); }

main { padding: 16px; max-width: 1800px; margin: 0 auto; }
.tab-panel { display: none; }
.tab-panel.active { display: block; }

/* Combined Pack view: mods on the left, dashboard + logs stacked on the right */
.pack-grid {
  display: grid;
  grid-template-columns: minmax(0, 3fr) minmax(380px, 2fr);
  gap: 16px;
  align-items: start;
}
.pack-grid > .left-col {
  display: flex; flex-direction: column;
  gap: 12px;
  min-width: 0;
}
.pack-grid > .right-col {
  display: flex; flex-direction: column;
  gap: 12px;
  min-width: 0;
  position: sticky;
  top: 16px;
}
.pack-grid .right-overview { display: flex; flex-direction: column; gap: 12px; }
.pack-grid .right-logs {
  display: flex; flex-direction: column;
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
  min-height: 240px;
  max-height: calc(100vh - 230px);
}
.pack-grid .right-logs-header {
  padding: 10px 14px;
  border-bottom: 1px solid var(--border);
  background: var(--panel-2);
  display: flex; gap: 8px; align-items: center;
  font-size: 13px;
}
.pack-grid .right-logs-header select {
  flex: 1; max-width: 220px; padding: 4px 8px; font-size: 12px;
}
.pack-grid .right-logs-body {
  flex: 1; overflow-y: auto; padding: 10px 14px;
  font-family: var(--mono); font-size: 11px;
  white-space: pre-wrap; word-break: break-word; color: #c8cdd6;
  background: #0e1014;
}
@media (max-width: 1100px) {
  .pack-grid { grid-template-columns: 1fr; }
  .pack-grid > .right-col { position: static; }
  .pack-grid .right-logs { max-height: 50vh; }
}

/* Descriptive sync cards (replace the old plain-button sync row) */
.sync-cards {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px;
}
.sync-card {
  display: flex; gap: 8px; align-items: flex-start;
  background: var(--panel-2);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 8px 10px;
  cursor: pointer;
  text-align: left;
  transition: border-color 120ms, background 120ms, transform 60ms;
  width: 100%;
  font: inherit;
  color: var(--text);
  min-height: 0;
}
.sync-card:hover:not([disabled]) {
  border-color: var(--accent);
  background: rgba(93,184,255,0.06);
}
.sync-card:active:not([disabled]) { transform: translateY(1px); }
.sync-card[disabled] { opacity: 0.5; cursor: not-allowed; }
.sync-card.deploy:hover:not([disabled]) {
  border-color: var(--warn);
  background: rgba(240,180,84,0.06);
}
.sync-card .sync-icon {
  flex-shrink: 0;
  display: flex; align-items: center; gap: 2px;
  margin-top: 1px;
}
.sync-card .sync-icon .ic {
  width: 18px; height: 18px;
  background-repeat: no-repeat;
  background-position: center;
  background-size: contain;
  flex-shrink: 0;
}
.sync-card .sync-icon .ic-arrow {
  color: var(--muted);
  font-size: 11px; line-height: 1;
  width: 8px; text-align: center;
  flex-shrink: 0;
}
.sync-card .sync-info { min-width: 0; }
.sync-card .sync-title { font-weight: 600; font-size: 12.5px; margin-bottom: 1px; }
.sync-card .sync-desc {
  color: var(--muted); font-size: 11px; line-height: 1.3;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.ic-folder {
  background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'><path fill='%2392bbf0' d='M5 3a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2V8a2 2 0 00-2-2h-7l-2-2H5z'/></svg>");
}
.ic-prism {
  background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'><polygon fill='%23e8c290' points='12,3 22,20 2,20'/><polygon fill='%235d8be8' opacity='0.7' points='12,3 18,13 6,13'/></svg>");
}
.ic-server {
  background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'><rect x='3' y='4' width='18' height='5' rx='1' fill='%23f4995e'/><rect x='3' y='10.5' width='18' height='5' rx='1' fill='%23f4995e'/><rect x='3' y='17' width='18' height='3' rx='1' fill='%23f4995e' opacity='0.6'/><circle cx='6' cy='6.5' r='0.6' fill='%23000'/><circle cx='6' cy='13' r='0.6' fill='%23000'/></svg>");
}
.ic-branch {
  background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'><path fill='%235db8ff' d='M6 3a2 2 0 100 4 2 2 0 000-4zm12 0a2 2 0 100 4 2 2 0 000-4zM6 8v8a2 2 0 100 2 2 2 0 00.5-3.9C6.4 13 7 12.5 8 12a8 8 0 008-7.5V6h-2v2.5c0 1.5-1.6 3-3 3.5-1.4.5-3 1.4-3 3v.1A2 2 0 008 16V8z'/></svg>");
}
.sync-card .sync-extra { margin-top: 4px; }
.sync-card.with-extra { flex-direction: column; align-items: stretch; padding: 8px 10px; }
.sync-card.with-extra .sync-row { display: flex; gap: 8px; align-items: flex-start; }
.sync-card .branch-row {
  display: flex; gap: 4px; margin-top: 4px;
  align-items: center;
}
.sync-card .branch-row select {
  flex: 1; min-width: 0; padding: 3px 6px; font-size: 11px;
  background: var(--panel); color: var(--text);
  border: 1px solid var(--border); border-radius: 4px;
}
.sync-card .branch-row .install {
  padding: 3px 9px; font-size: 11px;
  background: #1d4f80; border: 1px solid #2e6aa3; color: #fff;
  border-radius: 4px; cursor: pointer;
  flex-shrink: 0;
}
.sync-card .branch-row .install:hover { background: #2768a6; }

.cards { display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px; margin-bottom: 0; }
.card {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px 14px;
  transition: border-color 120ms, background 120ms;
}
.card.warn {
  border-color: rgba(240,180,84,0.45);
  background: rgba(240,180,84,0.07);
  box-shadow: inset 3px 0 0 var(--warn);
}
.card.warn:hover { background: rgba(240,180,84,0.12); }
.card.err {
  border-color: rgba(237,111,111,0.45);
  background: rgba(237,111,111,0.06);
  box-shadow: inset 3px 0 0 var(--error);
}
.card h3 { margin: 0 0 4px 0; font-size: 11px; color: var(--muted); font-weight: 500; text-transform: uppercase; letter-spacing: 0.5px; }
.card .big { font-size: 22px; font-weight: 600; line-height: 1.2; }
.card .sub { font-size: 12px; color: var(--muted); margin-top: 2px; }
.card .card-tags { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 8px; }
.tag.small-tag { font-size: 10px; padding: 1px 6px; }
.tag.small-tag::before { width: 9px !important; height: 9px !important; margin-right: 3px !important; }

.section {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 16px;
  margin-bottom: 20px;
}
.section h2 { margin: 0 0 12px 0; font-size: 15px; }
.section h2 .count {
  font-size: 12px; background: var(--panel-2); color: var(--muted);
  padding: 2px 8px; border-radius: 10px; margin-left: 8px; font-weight: normal;
}

.sync-buttons { display: flex; flex-wrap: wrap; gap: 10px; }
.sync-buttons button { font-size: 13px; padding: 10px 16px; min-width: 180px; }
.sync-buttons .branch-picker { display: flex; gap: 6px; align-items: stretch; }
.sync-buttons select {
  background: var(--panel-2); color: var(--text);
  border: 1px solid var(--border); border-radius: 6px;
  padding: 0 10px; font: inherit;
}

.issues { display: flex; flex-direction: column; gap: 6px; }
.issue {
  display: flex; gap: 12px; align-items: center;
  padding: 10px 12px;
  background: var(--panel-2);
  border: 1px solid var(--border);
  border-left: 3px solid var(--border);
  border-radius: 6px;
}
.issue.warning {
  background: rgba(237,111,111,0.06);
  border-color: rgba(237,111,111,0.25);
  border-left-color: var(--error);
}
.issue.info-sev {
  background: rgba(126,164,216,0.05);
  border-left-color: var(--info);
}
.issue .icon {
  flex-shrink: 0; font-size: 18px; width: 24px; text-align: center;
}
.issue .icon.warning { color: var(--warn); }
.issue .icon.info { color: var(--info); }
.issue .body { flex: 1; min-width: 0; }
.issue .body .msg { font-weight: 500; }
.issue .body .sub { color: var(--muted); font-size: 12px; margin-top: 2px; }
.issue .actions { flex-shrink: 0; display: flex; gap: 6px; }

table { width: 100%; border-collapse: collapse; }
table th, table td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border); }
table th {
  position: sticky; top: 0;
  background: var(--panel-2); color: var(--muted);
  font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px;
}
table tr:hover td { background: rgba(93,184,255,0.05); }
.tag {
  display: inline-block; font-size: 11px;
  padding: 2px 8px; border-radius: 10px;
  background: var(--panel-2); color: var(--muted);
}
.tag.modrinth { background: rgba(0,175,0,0.15); color: #5fe080; }
.tag.curseforge { background: rgba(240,100,40,0.18); color: #f4995e; }
.tag.custom { background: rgba(125,125,200,0.18); color: #a5a5e8; }
.tag.unknown { background: rgba(180,80,80,0.18); color: #e58a8a; }
.tag.repo { background: rgba(80,140,200,0.15); color: #92bbf0; }
.tag.prism { background: rgba(200,150,80,0.15); color: #e8c290; }
.tag.synced { background: rgba(109,207,131,0.15); color: var(--ok); }
.tag.out-of-sync { background: rgba(240,180,84,0.18); color: var(--warn); cursor: help; }
.tag.only { background: rgba(180,180,180,0.12); color: var(--muted); }
.tag.loose { background: rgba(180,80,80,0.18); color: #e58a8a; }

/* Provider/source/status logos via CSS — inline SVG as data URI so they
   appear everywhere a tag is rendered, no per-callsite changes. */
.tag.modrinth::before, .tag.curseforge::before, .tag.custom::before,
.tag.repo::before, .tag.prism::before, .tag.synced::before,
.tag.out-of-sync::before, .tag.loose::before, .tag.only::before {
  content: "";
  display: inline-block;
  width: 11px; height: 11px;
  background-repeat: no-repeat;
  background-position: center;
  background-size: contain;
  margin-right: 5px;
  vertical-align: -1px;
}
.tag.modrinth::before {
  background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 512 512'><path fill='%231bd96a' d='M256 32C132 32 32 132 32 256s100 224 224 224 224-100 224-224S380 32 256 32zM150 156h32v200h-32V156zm68 0h28l78 88V156h32v200h-32v-118l-78-88v206h-28V156z'/></svg>");
}
.tag.curseforge::before {
  background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'><path fill='%23f16436' d='M12 2c-1 3-3 5-3 8 0 2 1 4 3 4s3-2 3-4c0-3-2-5-3-8zm-4 11c-2 3 0 8 4 8s6-5 4-8c-1 2-2 3-4 3s-3-1-4-3z'/></svg>");
}
.tag.custom::before {
  background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'><circle cx='12' cy='12' r='8' fill='none' stroke='%23a5a5e8' stroke-width='3'/><circle cx='12' cy='12' r='3' fill='%23a5a5e8'/></svg>");
}
.tag.repo::before {
  background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'><path fill='%2392bbf0' d='M5 3a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2V8a2 2 0 00-2-2h-7l-2-2H5z'/></svg>");
}
.tag.prism::before {
  background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'><polygon fill='%23e8c290' points='12,3 22,20 2,20'/><polygon fill='%235d8be8' opacity='0.7' points='12,3 18,13 6,13'/></svg>");
}
.tag.synced::before {
  background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'><path fill='%236dcf83' d='M9 17l-5-5 1.4-1.4L9 14.2 18.6 4.6 20 6z'/></svg>");
}
.tag.out-of-sync::before {
  background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'><path fill='%23f0b454' d='M12 2L1 22h22L12 2zm-1 7h2v7h-2zm0 9h2v2h-2z'/></svg>");
}
.tag.loose::before {
  background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'><path fill='%23e58a8a' d='M12 2L3 7v10l9 5 9-5V7l-9-5zm0 2.2l7 3.9v8l-7 3.9-7-3.9v-8l7-3.9z'/></svg>");
}
.tag.only::before {
  background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'><circle cx='12' cy='12' r='8' fill='none' stroke='%238a93a3' stroke-width='2' stroke-dasharray='3 2'/></svg>");
}

/* Side column: client / server / both */
.tag.side-client { background: rgba(125,170,230,0.15); color: #92bbf0; }
.tag.side-server { background: rgba(240,150,90,0.18); color: #f4995e; }
.tag.side-both   { background: rgba(109,207,131,0.15); color: var(--ok); }
.tag.side-client::before, .tag.side-server::before, .tag.side-both::before {
  content: ""; display: inline-block;
  width: 12px; height: 12px;
  background-repeat: no-repeat; background-position: center; background-size: contain;
  margin-right: 5px; vertical-align: -1px;
}
.tag.side-client::before {
  background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'><rect x='2' y='4' width='20' height='13' rx='1.5' fill='none' stroke='%2392bbf0' stroke-width='2'/><rect x='9' y='19' width='6' height='2' fill='%2392bbf0'/></svg>");
}
.tag.side-server::before {
  background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'><rect x='3' y='3' width='18' height='7' rx='1' fill='none' stroke='%23f4995e' stroke-width='2'/><rect x='3' y='14' width='18' height='7' rx='1' fill='none' stroke='%23f4995e' stroke-width='2'/><circle cx='7' cy='6.5' r='1' fill='%23f4995e'/><circle cx='7' cy='17.5' r='1' fill='%23f4995e'/></svg>");
}
.tag.side-both::before {
  background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'><path fill='%235fe080' d='M2 12l5-5v3h10V7l5 5-5 5v-3H7v3z'/></svg>");
}

.tag.clickable {
  cursor: pointer;
  transition: filter 120ms;
}
.tag.clickable:hover { filter: brightness(1.25); }
.tag.clickable::after {
  content: " ▾";
  font-size: 9px;
  opacity: 0.7;
  margin-left: 2px;
}

/* Side-by-side diff modal */
.diff-grid {
  display: grid;
  grid-template-columns: minmax(140px, max-content) 1fr 1fr;
  gap: 1px;
  background: var(--border);
  border-radius: 6px;
  overflow: hidden;
  margin-top: 12px;
  font-family: var(--mono);
  font-size: 12px;
}
.diff-grid > div {
  background: var(--panel-2);
  padding: 8px 10px;
}
.diff-grid .diff-header {
  background: var(--panel);
  font-weight: 600;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: var(--muted);
}
.diff-grid .diff-label {
  color: var(--muted);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.4px;
  font-weight: 500;
}
.diff-grid .diff-cell {
  word-break: break-all;
  white-space: pre-wrap;
}
.diff-grid .diff-empty { color: var(--muted); font-style: italic; }
.diff-row.differs .diff-cell { background: rgba(240,180,84,0.1); }
.diff-row.differs .diff-label { background: rgba(240,180,84,0.18); color: var(--warn); }

.diff-side-tag { float: right; }

/* Configs modal */
.configs-list {
  max-height: 60vh;
  overflow-y: auto;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg);
}
.config-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 110px auto;
  gap: 12px; align-items: center;
  padding: 8px 12px;
  border-bottom: 1px solid var(--border);
  background: var(--panel-2);
}
.config-row:last-child { border-bottom: none; }
.config-row .config-path { min-width: 0; }
.config-row .config-path code {
  display: block;
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.config-row .config-actions { display: flex; gap: 4px; flex-shrink: 0; }

/* Per-file text diff */
.text-diff {
  background: #0e1014;
  border: 1px solid var(--border);
  border-radius: 6px;
  font-family: var(--mono);
  font-size: 11.5px;
  max-height: 60vh;
  overflow: auto;
  margin-top: 12px;
}
.text-diff .td-row {
  display: grid;
  grid-template-columns: 50px minmax(0, 1fr) minmax(0, 1fr);
  gap: 1px;
  background: var(--border);
}
.text-diff .td-row > div {
  background: #15171c;
  padding: 2px 8px;
  white-space: pre-wrap;
  word-break: break-all;
  min-height: 18px;
}
.text-diff .td-num {
  color: var(--muted);
  text-align: right;
  user-select: none;
  font-size: 10.5px;
}
.text-diff .td-row.differs > .td-cell { background: rgba(240,180,84,0.08); }
.text-diff .td-row.differs .td-num { background: rgba(240,180,84,0.2); color: var(--warn); }
.text-diff .td-header > div { background: var(--panel); font-weight: 600; padding: 6px 8px; }

.merge-options {
  display: grid; grid-template-columns: 1fr 1fr;
  gap: 12px; margin-top: 16px;
}
.merge-option {
  background: var(--panel-2);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 14px;
  cursor: pointer;
  transition: border-color 120ms, background 120ms;
}
.merge-option:hover { border-color: var(--accent); background: rgba(93,184,255,0.06); }
.merge-option.selected { border-color: var(--accent); background: rgba(93,184,255,0.1); }
.merge-option .arrow { font-size: 22px; margin-bottom: 6px; }
.merge-option .title { font-weight: 600; margin-bottom: 4px; }
.merge-option .sub { color: var(--muted); font-size: 12px; }
.merge-option.danger:hover { border-color: var(--error); background: rgba(237,111,111,0.06); }
.merge-option.danger.selected { border-color: var(--error); background: rgba(237,111,111,0.1); }

.diff-layout {
  display: grid;
  grid-template-columns: minmax(0, 2fr) minmax(0, 1fr);
  gap: 16px;
  margin-top: 12px;
}
@media (max-width: 900px) {
  .diff-layout { grid-template-columns: 1fr; }
}
.help-panel {
  background: var(--panel-2);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 14px;
  font-size: 13px;
  align-self: flex-start;
  max-height: 50vh;
  overflow-y: auto;
}
.help-panel h4 {
  margin: 0 0 10px 0;
  font-size: 12px;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  font-weight: 600;
}
.help-rec {
  background: rgba(93,184,255,0.1);
  border: 1px solid rgba(93,184,255,0.3);
  border-radius: 6px;
  padding: 10px 12px;
  margin-bottom: 12px;
  font-size: 13px;
}
.help-rec-title {
  font-weight: 600;
  color: var(--accent);
  margin-bottom: 4px;
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.4px;
}
.help-card {
  border-bottom: 1px solid var(--border);
  padding: 10px 0;
}
.help-card:last-child { border-bottom: none; padding-bottom: 0; }
.help-card:first-child { padding-top: 0; }
.help-card .help-title {
  font-weight: 600;
  font-size: 13px;
  margin-bottom: 4px;
}
.help-card .help-text { color: var(--muted); margin-bottom: 6px; font-size: 12px; }
.help-card .help-value {
  background: var(--panel);
  padding: 6px 8px;
  border-radius: 4px;
  margin-top: 4px;
  font-size: 12px;
}
.help-card .help-value code {
  background: var(--bg);
  padding: 1px 5px;
  border-radius: 3px;
  font-size: 11px;
}
.help-card .help-rec-text {
  color: var(--accent);
  font-size: 12px;
  margin-top: 6px;
  font-style: italic;
}

table tr.has-issue td { background: rgba(237,111,111,0.07); }
table tr.has-issue td:first-child { box-shadow: inset 3px 0 0 var(--error); }
table tr.has-issue:hover td { background: rgba(237,111,111,0.12); }

.mod-icon {
  width: 40px; height: 40px;
  min-width: 40px; min-height: 40px;
  border-radius: 6px;
  background: var(--panel-2);
  flex-shrink: 0;
  object-fit: contain;
  padding: 3px;
  display: inline-block;
  vertical-align: middle;
  border: 1px solid var(--border);
  box-sizing: border-box;
  image-rendering: -webkit-optimize-contrast;
}
.mod-icon.placeholder {
  display: inline-flex; align-items: center; justify-content: center;
  font-size: 18px; color: var(--muted); padding: 0;
}
.mod-icon.small {
  width: 26px; height: 26px;
  min-width: 26px; min-height: 26px;
  padding: 2px; border-radius: 4px;
}
.mod-icon.small.placeholder { font-size: 13px; }
.mod-icon.large {
  width: 64px; height: 64px;
  min-width: 64px; min-height: 64px;
  padding: 4px; border-radius: 8px;
}
.mod-icon.large.placeholder { font-size: 28px; }
.mod-name-row { display: flex; gap: 10px; align-items: center; }
.mod-name-row .mod-meta { display: flex; flex-direction: column; min-width: 0; }
.mod-name-row .mod-meta > * { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

input[type=text], input[type=password], input[type=search], textarea, select {
  background: var(--panel-2); color: var(--text);
  border: 1px solid var(--border); border-radius: 6px;
  padding: 7px 10px; font: inherit; width: 100%;
}
input:focus, select:focus, textarea:focus { outline: 1px solid var(--accent); border-color: var(--accent); }

.searchbar {
  display: flex; gap: 8px; margin-bottom: 12px; align-items: center;
  width: 100%;
}
.searchbar input[type=search] {
  flex: 1 1 auto;
  min-width: 0;
  width: auto;
  padding: 8px 12px;
  font-size: 14px;
}
.searchbar select {
  flex: 0 0 auto;
  width: auto;
  min-width: 140px;
  max-width: 200px;
}
.searchbar button { flex: 0 0 auto; }

/* Mods table: keep it inside its panel even with long filenames */
#mod-table {
  table-layout: fixed;
  width: 100%;
}
#mod-table th, #mod-table td {
  vertical-align: top;
}
#mod-table th:nth-child(1), #mod-table td:nth-child(1) { width: 26%; }
#mod-table th:nth-child(2), #mod-table td:nth-child(2) { width: 30%; }
#mod-table th:nth-child(3), #mod-table td:nth-child(3) { width: 12%; }
#mod-table th:nth-child(4), #mod-table td:nth-child(4) { width: 9%; }
#mod-table th:nth-child(5), #mod-table td:nth-child(5) { width: 13%; }
#mod-table th:nth-child(6), #mod-table td:nth-child(6) { width: 10%; }
#mod-table td .tag {
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  display: inline-block;
  vertical-align: middle;
}
#mod-table td .muted {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  display: block;
}
#mod-table .mod-name-row span {
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  min-width: 0;
}
#mod-table .mod-name-row {
  flex-wrap: nowrap;
  overflow: hidden;
}

.modal-bg {
  position: fixed; inset: 0;
  background: rgba(0,0,0,0.6);
  display: none; z-index: 100;
  align-items: center; justify-content: center;
  padding: 40px 20px;
}
.modal-bg.show { display: flex; }
.modal {
  background: var(--panel); border: 1px solid var(--border);
  border-radius: 8px; box-shadow: var(--shadow);
  max-width: 700px; width: 100%;
  max-height: 90vh; display: flex; flex-direction: column;
  overflow: hidden;
}
.modal.wide { max-width: 1100px; }
.modal h2 { margin: 0; padding: 16px 20px; border-bottom: 1px solid var(--border); }
.modal .body { padding: 16px 20px; overflow-y: auto; }
.modal .footer {
  padding: 14px 20px; border-top: 1px solid var(--border);
  display: flex; gap: 10px; justify-content: flex-end;
}

.choice-grid { display: grid; gap: 10px; grid-template-columns: 1fr; }
.choice-card {
  border: 1px solid var(--border); border-radius: 6px;
  padding: 12px; background: var(--panel-2); cursor: pointer;
  display: flex; gap: 12px; align-items: center;
}
.choice-card:hover { border-color: var(--accent); }
.choice-card.selected { border-color: var(--accent); background: rgba(93,184,255,0.08); }
.choice-card .platform { font-size: 11px; padding: 3px 9px; border-radius: 10px; }
.choice-card .info { flex: 1; min-width: 0; }
.choice-card .name { font-weight: 500; }
.choice-card .meta { font-size: 12px; color: var(--muted); }

.log-panel {
  background: #0e1014; border: 1px solid var(--border); border-radius: 6px;
  padding: 12px; font-family: var(--mono); font-size: 12px;
  white-space: pre-wrap; word-break: break-word;
  max-height: 70vh; overflow-y: auto; color: #c8cdd6;
}

#toast {
  position: fixed; bottom: 20px; right: 20px;
  background: var(--panel); border: 1px solid var(--border);
  border-radius: 6px; padding: 12px 16px; box-shadow: var(--shadow);
  display: none; z-index: 200; max-width: 400px;
}
#toast.show { display: block; }
#toast.error { border-color: var(--error); }
#toast.ok { border-color: var(--ok); }

.kv { display: grid; grid-template-columns: 1fr 2fr; gap: 8px 16px; align-items: center; }
.kv label { color: var(--muted); }
.muted { color: var(--muted); }
.spinner {
  display: inline-block; width: 12px; height: 12px;
  border: 2px solid var(--muted); border-top-color: var(--accent);
  border-radius: 50%; animation: spin 0.8s linear infinite;
  vertical-align: middle;
}
@keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>

<header>
  <h1>📦 Packwiz Manager</h1>
  <div class="badges" id="badges"></div>
  <div class="actions">
    <button onclick="reload()" id="reload-btn" title="Re-scan repo + Prism">↻ Refresh</button>
  </div>
</header>

<nav class="tabs">
  <button class="active" data-tab="pack">Pack</button>
  <button data-tab="settings">Settings</button>
</nav>

<main>
  <div class="tab-panel active" id="tab-pack">
    <div class="pack-grid">
      <div class="left-col">
        <div class="searchbar">
          <input type="search" id="mod-search" placeholder="Search mods…" oninput="renderMods()">
          <select id="mod-filter" onchange="renderMods()">
            <option value="all">All sources</option>
            <option value="repo">In repo</option>
            <option value="prism">In Prism</option>
            <option value="modrinth">Modrinth</option>
            <option value="curseforge">CurseForge</option>
            <option value="custom">Custom</option>
            <option value="loose">Loose jars</option>
          </select>
        </div>
        <div class="section" style="padding: 0; overflow: hidden;">
          <div style="overflow-x:auto;">
            <table id="mod-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Slug / Filename</th>
                  <th>Source</th>
                  <th>Side</th>
                  <th>Status</th>
                  <th></th>
                </tr>
              </thead>
              <tbody id="mod-tbody"></tbody>
            </table>
          </div>
        </div>
      </div>

      <div class="right-col">
        <div class="right-overview">
          <div class="cards" id="cards"></div>

          <div class="section" style="margin-bottom:0;">
            <h2 style="margin-bottom:10px;">Sync</h2>
            <div class="sync-cards" id="sync-cards"></div>
          </div>

          <div class="section" style="margin-bottom:0;">
            <h2>Issues <span class="count" id="issue-count">0</span></h2>
            <div class="issues" id="issues"></div>
          </div>
        </div>

        <div class="right-logs">
          <div class="right-logs-header">
            <span style="font-weight:600;">Logs</span>
            <select id="job-select" onchange="loadJobLog()"></select>
            <button onclick="refreshJobs()" class="small">↻</button>
            <span class="muted" id="job-status" style="font-size:11px;"></span>
          </div>
          <div class="right-logs-body" id="log-panel">No job selected.</div>
        </div>
      </div>
    </div>
  </div>

  <div class="tab-panel" id="tab-settings">
    <div class="section">
      <h2>.env settings</h2>
      <p class="muted" style="margin-top:0;">Saved to <code>.env</code> in the repo root.</p>
      <div class="kv" id="settings-kv"></div>
      <div style="margin-top: 16px;">
        <button class="primary" onclick="saveSettings()">Save</button>
      </div>
    </div>
    <div class="section">
      <h2>System</h2>
      <div class="kv" id="system-kv"></div>
    </div>
  </div>
</main>

<div class="modal-bg" id="modal-bg" onclick="if(event.target===this)closeModal()">
  <div class="modal">
    <h2 id="modal-title"></h2>
    <div class="body" id="modal-body"></div>
    <div class="footer" id="modal-footer"></div>
  </div>
</div>

<div id="toast"></div>

<script>
let STATE = null;
let SETTINGS = null;
let LIVE_JOB_ID = null;
let LIVE_EVT_SOURCE = null;

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

// --- tabs ---
$$("nav.tabs button").forEach(btn => {
  btn.addEventListener("click", () => {
    $$("nav.tabs button").forEach(b => b.classList.remove("active"));
    $$(".tab-panel").forEach(p => p.classList.remove("active"));
    btn.classList.add("active");
    $("#tab-" + btn.dataset.tab).classList.add("active");
    if (btn.dataset.tab === "settings") loadSettings();
  });
});

// --- toast ---
function toast(msg, kind = "") {
  const t = $("#toast");
  t.className = "show " + kind;
  t.textContent = msg;
  setTimeout(() => t.className = "", 4500);
}

// --- modal ---
function showModal(title, body, footer, opts = {}) {
  $("#modal-title").textContent = title;
  $("#modal-body").innerHTML = "";
  if (typeof body === "string") $("#modal-body").innerHTML = body;
  else $("#modal-body").appendChild(body);
  $("#modal-footer").innerHTML = "";
  (footer || []).forEach(f => $("#modal-footer").appendChild(f));
  const modalEl = $("#modal-bg .modal");
  if (modalEl) modalEl.classList.toggle("wide", !!opts.wide);
  $("#modal-bg").classList.add("show");
}
function closeModal() { $("#modal-bg").classList.remove("show"); }
function btn(label, kind, fn) {
  const b = document.createElement("button");
  b.textContent = label;
  if (kind) b.className = kind;
  b.onclick = fn;
  return b;
}

// Reusable confirm modal — replaces the native browser confirm() so destructive
// actions always have a clear in-app dialog that can't be silenced by the browser.
function confirmModal({ title, body, confirmText = "Confirm", confirmKind = "primary" }) {
  return new Promise(resolve => {
    const bodyEl = document.createElement("div");
    if (typeof body === "string") bodyEl.innerHTML = body;
    else if (body) bodyEl.appendChild(body);
    const cancelBtn = btn("Cancel", "", () => { closeModal(); resolve(false); });
    const okBtn = btn(confirmText, confirmKind, () => { closeModal(); resolve(true); });
    showModal(title, bodyEl, [cancelBtn, okBtn]);
    // Focus the safer choice by default
    setTimeout(() => cancelBtn.focus(), 50);
  });
}

// --- API ---
async function api(path, opts = {}) {
  const r = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!r.ok) {
    const t = await r.text();
    throw new Error(`${r.status}: ${t}`);
  }
  return r.json();
}

// --- main reload ---
async function reload() {
  $("#reload-btn").innerHTML = '<span class="spinner"></span> Refresh';
  try {
    STATE = await api("/api/state");
    renderHeader();
    renderCards();
    renderIssues();
    renderMods();
    await refreshBranches();
    loadConfigsDiff();  // background — populates configs card
  } catch (e) {
    toast(e.message, "error");
  } finally {
    $("#reload-btn").textContent = "↻ Refresh";
  }
}

function renderHeader() {
  const m = STATE.meta;
  const r = STATE.repo;
  const p = STATE.prism;
  const s = STATE.server;
  const badges = [];
  badges.push(`<span class="badge ok"><span class="dot"></span>repo: ${r.entry_count} mods${r.loose_count ? " + " + r.loose_count + " loose" : ""}</span>`);
  if (p.ok) {
    badges.push(`<span class="badge ${p.index_count === r.entry_count ? 'ok' : 'warn'}"><span class="dot"></span>prism: ${p.jar_count}/${p.index_count}</span>`);
  } else {
    badges.push(`<span class="badge err"><span class="dot"></span>prism: not found</span>`);
  }
  if (s.configured) {
    if (!s.reachable) badges.push(`<span class="badge err"><span class="dot"></span>server: unreachable</span>`);
    else if (s.running) badges.push(`<span class="badge ok"><span class="dot"></span>server: ${s.online}/${s.max} · ${s.version}</span>`);
    else badges.push(`<span class="badge warn"><span class="dot"></span>server: stopped</span>`);
  } else {
    badges.push(`<span class="badge"><span class="dot"></span>server: not configured</span>`);
  }
  if (!m.curseforge_configured) badges.push(`<span class="badge warn"><span class="dot"></span>CurseForge API: no key</span>`);
  $("#badges").innerHTML = badges.join("");
  renderSyncCards();
}

function renderSyncCards() {
  const s = STATE.server;
  const container = $("#sync-cards");
  if (!container) return;
  container.innerHTML = "";

  function card({ iconHtml, title, desc, kind = "", onClick, disabled = false, extra = null }) {
    const b = document.createElement("button");
    b.className = "sync-card" + (kind ? " " + kind : "") + (extra ? " with-extra" : "");
    b.disabled = disabled;
    if (!extra) {
      b.innerHTML = `
        <div class="sync-icon">${iconHtml}</div>
        <div class="sync-info">
          <div class="sync-title">${escapeHtml(title)}</div>
          <div class="sync-desc">${escapeHtml(desc)}</div>
        </div>`;
    } else {
      b.innerHTML = `
        <div class="sync-row">
          <div class="sync-icon">${iconHtml}</div>
          <div class="sync-info">
            <div class="sync-title">${escapeHtml(title)}</div>
            <div class="sync-desc">${escapeHtml(desc)}</div>
          </div>
        </div>
        <div class="sync-extra"></div>`;
    }
    if (onClick) b.onclick = onClick;
    if (extra) b.querySelector(".sync-extra").appendChild(extra);
    return b;
  }

  // Composite source→destination icon helpers
  const ICON_FOLDER = '<span class="ic ic-folder" title="repo"></span>';
  const ICON_PRISM  = '<span class="ic ic-prism" title="Prism"></span>';
  const ICON_SERVER = '<span class="ic ic-server" title="server"></span>';
  const ICON_BRANCH = '<span class="ic ic-branch" title="branch"></span>';
  const ARROW = '<span class="ic-arrow">→</span>';

  // 1. Install/Update Prism from repo  (folder → prism)
  container.appendChild(card({
    iconHtml: `${ICON_FOLDER}${ARROW}${ICON_PRISM}`,
    title: "Install in Prism",
    desc: "Sync your Prism instance to match this repo.",
    onClick: () => runAction("local-to-prism", "Install in Prism", {}, {
      title: "Install repo into Prism",
      body: `<p>Update your Prism Launcher to match this repo.</p>
             <p class="muted">Runs <code>packwiz serve</code> locally, then has the bootstrapper reconcile your Prism instance against it.</p>`,
      confirmText: "Install",
      confirmKind: "primary",
    }),
  }));

  // 2. Pull from Prism  (prism → folder)
  container.appendChild(card({
    iconHtml: `${ICON_PRISM}${ARROW}${ICON_FOLDER}`,
    title: "Pull from Prism",
    desc: "Copy Prism's mods + configs back into the repo.",
    onClick: () => runAction("prism-to-local", "Pull from Prism", {}, {
      title: "Pull from Prism into repo",
      body: `<p>Copy Prism's <code>mods/.index/</code>, <code>config/</code>, <code>kubejs/</code>, and <code>defaultconfigs/</code> into the repo.</p>
             <p class="muted">Use this after adding/removing mods via Prism's mod manager. Then commit + push.</p>`,
      confirmText: "Pull",
      confirmKind: "primary",
    }),
  }));

  // 3. Deploy to Server  (folder → server)
  const deployDisabled = !s.configured;
  let deployDesc = "Stop, push, restart.";
  if (!s.configured) deployDesc = "Set CRAFTY_* in Settings.";
  else if (!s.reachable) deployDesc = "Server unreachable.";
  else if (s.running && s.online > 0) deployDesc = `⚠ ${s.online} player(s) will disconnect.`;
  else if (!s.running) deployDesc = "Server stopped — will start after sync.";
  container.appendChild(card({
    iconHtml: `${ICON_FOLDER}${ARROW}${ICON_SERVER}`,
    title: "Deploy to Server",
    desc: deployDesc,
    kind: "deploy",
    disabled: deployDisabled,
    onClick: deployDisabled ? null : confirmDeploy,
  }));

  // 4. Install a GitHub branch  (branch → prism, with picker inline)
  const branchExtra = document.createElement("div");
  branchExtra.className = "branch-row";
  branchExtra.onclick = (ev) => ev.stopPropagation();
  const sel = document.createElement("select");
  sel.id = "branch-select";
  branchExtra.appendChild(sel);
  const installBtn = document.createElement("button");
  installBtn.className = "install"; installBtn.textContent = "Install";
  installBtn.onclick = (ev) => { ev.stopPropagation(); confirmGithubToPrism(); };
  branchExtra.appendChild(installBtn);
  container.appendChild(card({
    iconHtml: `${ICON_BRANCH}${ARROW}${ICON_PRISM}`,
    title: "Install GitHub branch",
    desc: "Try a different branch in Prism.",
    extra: branchExtra,
  }));
}

function renderCards() {
  const r = STATE.repo, p = STATE.prism, s = STATE.server;
  const issues = STATE.issues || [];

  // Provider breakdown for repo
  const providers = { modrinth: 0, curseforge: 0, custom: 0, unknown: 0 };
  (r.entries || []).forEach(e => {
    providers[e.provider] = (providers[e.provider] || 0) + 1;
  });
  const providerParts = [];
  if (providers.modrinth) providerParts.push(`<span class="tag modrinth small-tag">${providers.modrinth}</span>`);
  if (providers.curseforge) providerParts.push(`<span class="tag curseforge small-tag">${providers.curseforge}</span>`);
  if (providers.custom) providerParts.push(`<span class="tag custom small-tag">${providers.custom}</span>`);
  if (providers.unknown) providerParts.push(`<span class="tag unknown small-tag">${providers.unknown}</span>`);

  // Side breakdown for repo
  const sides = { both: 0, server: 0, client: 0 };
  (r.entries || []).forEach(e => {
    const side = (e.side || "both").toLowerCase();
    sides[side] = (sides[side] || 0) + 1;
  });
  const sideParts = [];
  if (sides.both) sideParts.push(`<span class="tag side-both small-tag">${sides.both}</span>`);
  if (sides.server) sideParts.push(`<span class="tag side-server small-tag">${sides.server}</span>`);
  if (sides.client) sideParts.push(`<span class="tag side-client small-tag">${sides.client}</span>`);

  // Sync state for the diff
  let synced = 0, outOfSync = 0, repoOnly = 0, prismOnly = 0;
  if (p.ok) {
    const repoNames = new Set((r.entries || []).map(e => e.pw_toml_name));
    const prismNames = new Set((p.entries || []).map(e => e.pw_toml_name));
    const repoMap = {};
    (r.entries || []).forEach(e => { repoMap[e.pw_toml_name] = e; });
    (p.entries || []).forEach(e => {
      const re = repoMap[e.pw_toml_name];
      if (!re) prismOnly++;
      else if (re.filename === e.filename && re.download_hash === e.download_hash && re.download_url === e.download_url) synced++;
      else outOfSync++;
    });
    (r.entries || []).forEach(e => {
      if (!prismNames.has(e.pw_toml_name)) repoOnly++;
    });
  }

  const cards = [];

  // 1. Repo card
  cards.push(`<div class="card">
    <h3>Repo</h3>
    <div class="big">${r.entry_count}</div>
    <div class="sub">mods · ${r.loose_count} loose jar${r.loose_count === 1 ? "" : "s"}</div>
    ${providerParts.length ? `<div class="card-tags">${providerParts.join(" ")}</div>` : ""}
    ${sideParts.length ? `<div class="card-tags">${sideParts.join(" ")}</div>` : ""}
  </div>`);

  // 2. Prism card
  if (p.ok) {
    const notInstalled = p.not_installed_count || 0;
    const prismWarn = (outOfSync || prismOnly || repoOnly || notInstalled) ? "warn" : "";
    cards.push(`<div class="card ${prismWarn}">
      <h3>Prism</h3>
      <div class="big">${p.jar_count} <span style="font-size:13px;color:var(--muted);font-weight:normal;">/ ${p.index_count}</span></div>
      <div class="sub">jars / indexed${notInstalled ? ` · <span style="color:var(--warn);">${notInstalled} not installed</span>` : ""}</div>
      ${(synced || outOfSync || prismOnly || repoOnly || notInstalled) ? `
        <div class="card-tags" style="font-size:11px;">
          ${synced ? `<span class="tag synced small-tag">${synced} synced</span>` : ""}
          ${outOfSync ? `<span class="tag out-of-sync small-tag">${outOfSync} diff</span>` : ""}
          ${notInstalled ? `<span class="tag out-of-sync small-tag">${notInstalled} no jar</span>` : ""}
          ${repoOnly ? `<span class="tag only small-tag">${repoOnly} repo only</span>` : ""}
          ${prismOnly ? `<span class="tag only small-tag">${prismOnly} prism only</span>` : ""}
        </div>` : ""}
    </div>`);
  } else {
    cards.push(`<div class="card">
      <h3>Prism</h3>
      <div class="big" style="font-size:14px;color:var(--muted);">not found</div>
      <div class="sub">${escapeHtml(p.path || "INSTANCE not set in Settings")}</div>
    </div>`);
  }

  // 3. Server card
  if (s.configured) {
    if (s.reachable) {
      const stateColor = s.running ? "var(--ok)" : "var(--warn)";
      const stateText = s.running ? "online" : "stopped";
      const players = s.running ? `${s.online}/${s.max} players` : "";
      const cpu = s.cpu != null ? `${typeof s.cpu === "number" ? s.cpu.toFixed(1) : s.cpu}% cpu` : "";
      const world = s.world_size || "";
      const subParts = [s.version, players, cpu, world].filter(Boolean);
      cards.push(`<div class="card">
        <h3>Server</h3>
        <div class="big" style="color:${stateColor};">${stateText}</div>
        <div class="sub">${subParts.map(x => escapeHtml(String(x))).join(" · ")}</div>
      </div>`);
    } else {
      cards.push(`<div class="card">
        <h3>Server</h3>
        <div class="big" style="color:var(--error);">unreachable</div>
        <div class="sub">Check Crafty URL/token in Settings</div>
      </div>`);
    }
  } else {
    cards.push(`<div class="card">
      <h3>Server</h3>
      <div class="big" style="font-size:14px;color:var(--muted);">not configured</div>
      <div class="sub">Add CRAFTY_* in Settings</div>
    </div>`);
  }

  // 4. Configs card
  cards.push(`<div class="card" id="configs-card" style="cursor:pointer;" onclick="openConfigsModal()">
    <h3>Configs</h3>
    <div class="big" id="configs-big">…</div>
    <div class="sub" id="configs-sub">click to scan</div>
  </div>`);

  // 5. Issues card
  const warns = issues.filter(i => i.severity === "warning").length;
  const infos = issues.filter(i => i.severity === "info").length;
  const byKind = {};
  issues.forEach(i => { byKind[i.kind] = (byKind[i.kind] || 0) + 1; });
  const kindParts = Object.entries(byKind).map(([k, v]) =>
    `<span class="tag small-tag" style="background:rgba(180,180,180,0.12);color:var(--muted);">${k}: ${v}</span>`);
  const issuesClass = warns > 0 ? "err" : (issues.length > 0 ? "warn" : "");
  cards.push(`<div class="card ${issuesClass}">
    <h3>Issues</h3>
    <div class="big" style="${warns > 0 ? 'color:var(--error);' : ''}">${issues.length}</div>
    <div class="sub">${warns} warning${warns === 1 ? "" : "s"}${infos ? " · " + infos + " info" : ""}</div>
    ${kindParts.length ? `<div class="card-tags">${kindParts.join(" ")}</div>` : ""}
  </div>`);

  $("#cards").innerHTML = cards.join("");
}

function renderIssues() {
  $("#issue-count").textContent = STATE.issues.length;
  const el = $("#issues");
  el.innerHTML = "";
  if (STATE.issues.length === 0) {
    el.innerHTML = '<div class="muted" style="padding:14px;">No issues found. ✓</div>';
    return;
  }

  // Bulk action: if there are several missing-jar issues, offer one-click install for all
  const missingJars = STATE.issues.filter(i => i.kind === "missing-jar");
  if (missingJars.length > 1) {
    const bulk = document.createElement("div");
    bulk.style.cssText = "padding:8px 10px;margin-bottom:8px;background:var(--panel-2);border:1px solid var(--border);border-radius:6px;display:flex;gap:8px;align-items:center;";
    bulk.innerHTML = `<span style="flex:1;font-size:12px;">${missingJars.length} mods have metadata but no installed jar.</span>`;
    bulk.appendChild(btn(`Install all (${missingJars.length})`, "primary small",
      () => installAllMissingJars(missingJars)));
    el.appendChild(bulk);
  }
  STATE.issues.forEach((iss, idx) => {
    const div = document.createElement("div");
    div.className = "issue " + (iss.severity === "warning" ? "warning" : "info-sev");
    const iconChar = iss.severity === "warning" ? "⚠" : "ℹ";
    let modIcon = "";
    const e = (iss.data && iss.data.entry) ||
      ((iss.data && iss.data.entries && iss.data.entries[0]) || null);
    if (e && e.icon_url !== undefined) modIcon = modIconHtml(e.icon_url);
    else if (iss.kind === "loose-jar" || iss.kind === "prism-index-stale") modIcon = modIconHtml("");
    div.innerHTML = `
      <div class="icon ${iss.severity}">${iconChar}</div>
      ${modIcon}
      <div class="body">
        <div class="msg">${escapeHtml(iss.message)}</div>
        <div class="sub">${iss.kind}</div>
      </div>
      <div class="actions"></div>
    `;
    const actions = div.querySelector(".actions");
    if (iss.kind === "loose-jar") {
      actions.appendChild(btn("Identify", "primary small", () => identifyJar(iss)));
    } else if (iss.kind === "prism-index-stale") {
      actions.appendChild(btn("Sync", "primary small", () => syncPwToPrism(iss)));
    } else if (iss.kind === "missing-jar") {
      actions.appendChild(btn("Install", "primary small", () => installMissingJar(iss)));
    } else if (iss.kind === "duplicate") {
      actions.appendChild(btn("Resolve", "primary small", () => resolveDuplicate(iss)));
    } else if (iss.kind === "cf-empty-url") {
      actions.appendChild(btn("Fix URL", "primary small", () => fixCfUrl(iss)));
    } else if (iss.kind === "mismatch") {
      if (iss.data && iss.data.side === "prism-only") {
        actions.appendChild(btn("Delete from Prism", "danger small",
          () => deleteFromPrism(iss.targets[0])));
      } else {
        actions.appendChild(btn("View", "small", () => switchToMods(iss.targets[0])));
      }
    }
    el.appendChild(div);
  });
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

function modIconHtml(url, size = "") {
  const cls = "mod-icon" + (size ? " " + size : "");
  if (url) {
    return `<img class="${cls}" src="${escapeHtml(url)}" loading="lazy" alt="" onerror="this.style.display='none'">`;
  }
  return `<span class="${cls} placeholder" title="no icon">📦</span>`;
}

function fmtBytes(n) {
  if (n < 1024) return n + " B";
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KiB";
  return (n / 1024 / 1024).toFixed(1) + " MiB";
}

// --- mod identify flow ---
async function identifyJar(iss) {
  const jar = iss.data.jar;
  showModal("Identify: " + jar.filename,
    '<p><span class="spinner"></span> Hashing and querying Modrinth + CurseForge…</p>'
    + '<p class="muted">' + escapeHtml(jar.path) + ' · ' + fmtBytes(jar.size) + '</p>',
    [btn("Cancel", "", closeModal)]);
  let result;
  try {
    result = await api("/api/identify", {
      method: "POST", body: JSON.stringify({ path: jar.path }),
    });
  } catch (e) { toast(e.message, "error"); closeModal(); return; }

  const body = document.createElement("div");
  body.innerHTML = `<p class="muted">SHA1: <code>${result.hashes.sha1}</code></p>`;
  const grid = document.createElement("div");
  grid.className = "choice-grid";

  let chosen = null;
  function makeCard(label, kind, iconUrl, info, onPick) {
    const c = document.createElement("div");
    c.className = "choice-card";
    c.innerHTML = `${modIconHtml(iconUrl, "large")}
      <span class="platform tag ${kind}">${label}</span>
      <div class="info"><div class="name">${escapeHtml(info.name)}</div>
        <div class="meta">${escapeHtml(info.meta)}</div></div>`;
    c.onclick = () => {
      grid.querySelectorAll(".choice-card").forEach(x => x.classList.remove("selected"));
      c.classList.add("selected");
      chosen = onPick;
    };
    return c;
  }

  if (result.modrinth) {
    const v = result.modrinth;
    const proj = result.modrinth_project || {};
    const f = (v.files || []).find(f => f.primary) || (v.files || [])[0] || {};
    grid.appendChild(makeCard("Modrinth", "modrinth", proj.icon_url || "", {
      name: proj.title || v.name || f.filename || jar.filename,
      meta: `version ${v.version_number || v.id} · MC ${(v.game_versions || []).join(", ")}`,
    }, async () => ({ choice: "modrinth", modrinth_version: v, modrinth_project: proj })));
  } else {
    const c = document.createElement("div");
    c.className = "choice-card";
    c.style.opacity = "0.5";
    c.innerHTML = `${modIconHtml("", "large")}<span class="platform tag modrinth">Modrinth</span><div class="info"><div class="name">No match</div><div class="meta">Hash not found on Modrinth.</div></div>`;
    grid.appendChild(c);
  }

  if (result.curseforge) {
    const cfMod = result.curseforge_mod || {};
    const cfLogo = (cfMod.logo && cfMod.logo.url) || "";
    const f = result.curseforge.file || result.curseforge;
    grid.appendChild(makeCard("CurseForge", "curseforge", cfLogo, {
      name: cfMod.name || f.fileName || f.displayName || jar.filename,
      meta: `project ${f.modId || result.curseforge.id} · file ${f.id}`,
    }, async () => ({ choice: "curseforge", curseforge_match: result.curseforge })));
  } else if (!STATE.meta.curseforge_configured) {
    const c = document.createElement("div");
    c.className = "choice-card"; c.style.opacity = "0.5";
    c.innerHTML = `${modIconHtml("", "large")}<span class="platform tag curseforge">CurseForge</span><div class="info"><div class="name">API key not set</div><div class="meta">Add CURSEFORGE_API_KEY in Settings.</div></div>`;
    grid.appendChild(c);
  } else {
    const c = document.createElement("div");
    c.className = "choice-card"; c.style.opacity = "0.5";
    c.innerHTML = `${modIconHtml("", "large")}<span class="platform tag curseforge">CurseForge</span><div class="info"><div class="name">No match</div><div class="meta">Fingerprint not found on CurseForge.</div></div>`;
    grid.appendChild(c);
  }

  // Custom option
  grid.appendChild(makeCard("Custom", "custom", "", {
    name: "Mark as custom (serve from GitHub raw)",
    meta: `Generates .pw.toml with raw.githubusercontent.com URL on branch "main"`,
  }, async () => ({ choice: "custom" })));

  body.appendChild(grid);

  const slugInput = document.createElement("div");
  slugInput.style.marginTop = "12px";
  slugInput.innerHTML = `<label class="muted" style="font-size:12px;">Slug for .pw.toml filename:</label>
    <input type="text" id="modal-slug" value="${escapeHtml(jar.filename.replace(/\.jar$/, '').toLowerCase().replace(/[^a-z0-9]+/g, '-'))}">`;
  body.appendChild(slugInput);

  showModal("Identify: " + jar.filename, body, [
    btn("Cancel", "", closeModal),
    btn("Apply", "primary", async () => {
      if (!chosen) { toast("Pick an option first.", "error"); return; }
      const slug = document.getElementById("modal-slug").value.trim();
      if (!slug) { toast("Slug required.", "error"); return; }
      const sel = await chosen();
      try {
        await api("/api/resolve-jar", {
          method: "POST",
          body: JSON.stringify({ path: jar.path, slug, ...sel }),
        });
        toast("Wrote " + slug + ".pw.toml", "ok");
        closeModal();
        await reload();
      } catch (e) { toast(e.message, "error"); }
    }),
  ]);
}

// --- duplicates ---
function resolveDuplicate(iss) {
  const entries = iss.data.entries;
  const body = document.createElement("div");
  body.innerHTML = `<p>${escapeHtml(iss.message)}</p>
    <p class="muted">Pick which one to keep. The other will be deleted from <code>mods/</code>.</p>`;
  const grid = document.createElement("div");
  grid.className = "choice-grid";
  let keep = null;
  // Default: prefer Modrinth
  const modrinthEntry = entries.find(e => e.provider === "modrinth");
  entries.forEach((e, i) => {
    const c = document.createElement("div");
    c.className = "choice-card" + (e === modrinthEntry ? " selected" : "");
    if (e === modrinthEntry) keep = e.pw_toml_path;
    c.innerHTML = `${modIconHtml(e.icon_url || "", "large")}
      <span class="platform tag ${e.provider}">${e.provider}</span>
      <div class="info"><div class="name">${escapeHtml(e.pw_toml_name)}</div>
        <div class="meta">${escapeHtml(e.name)} · ${escapeHtml(e.filename)}</div></div>`;
    c.onclick = () => {
      grid.querySelectorAll(".choice-card").forEach(x => x.classList.remove("selected"));
      c.classList.add("selected");
      keep = e.pw_toml_path;
    };
    grid.appendChild(c);
  });
  body.appendChild(grid);
  showModal("Resolve duplicate", body, [
    btn("Cancel", "", closeModal),
    btn("Delete the others", "danger", async () => {
      if (!keep) return;
      const del = entries.map(e => e.pw_toml_path).filter(p => p !== keep);
      try {
        await api("/api/resolve-duplicate", {
          method: "POST", body: JSON.stringify({ keep, delete: del }),
        });
        toast("Deleted " + del.length + " file(s).", "ok");
        closeModal();
        await reload();
      } catch (e) { toast(e.message, "error"); }
    }),
  ]);
}

async function deleteFromPrism(name) {
  const ok = await confirmModal({
    title: "Delete from Prism",
    body: `<p>Delete <code>${escapeHtml(name)}</code> from Prism's <code>mods/.index/</code>?</p>
           <p class="muted">This only removes the .pw.toml metadata. The installed jar in <code>mods/</code> stays where it is.</p>`,
    confirmText: "Delete",
    confirmKind: "danger",
  });
  if (!ok) return;
  try {
    await api("/api/delete-from-prism", {
      method: "POST", body: JSON.stringify({ name, kind: "mods" }),
    });
    toast("Deleted " + name + " from Prism.", "ok");
    await reload();
  } catch (e) { toast(e.message, "error"); }
}

async function installMissingJar(iss) {
  const pw = iss.data && iss.data.pw_toml_path;
  if (!pw) { toast("No .pw.toml path on issue.", "error"); return; }
  try {
    const result = await api("/api/install-missing-jar", {
      method: "POST", body: JSON.stringify({ pw_toml_path: pw }),
    });
    toast(`Installed ${iss.data.filename}.`, "ok");
    await reload();
  } catch (e) { toast(e.message, "error"); }
}

async function installAllMissingJars(items) {
  const ok = await confirmModal({
    title: "Install missing jars",
    body: `<p>Download and install <b>${items.length}</b> jar${items.length === 1 ? "" : "s"} into Prism's <code>mods/</code>?</p>
           <p class="muted">Each one is fetched from its <code>.pw.toml</code>'s URL (or via the CurseForge API for <code>metadata:curseforge</code> entries).</p>`,
    confirmText: `Install ${items.length}`,
    confirmKind: "primary",
  });
  if (!ok) return;
  let success = 0, failed = 0;
  for (const iss of items) {
    const pw = iss.data && iss.data.pw_toml_path;
    if (!pw) { failed++; continue; }
    try {
      await api("/api/install-missing-jar", {
        method: "POST", body: JSON.stringify({ pw_toml_path: pw }),
      });
      success++;
    } catch (e) { failed++; }
  }
  toast(`Installed ${success}${failed ? `, ${failed} failed (likely CF mods needing API key)` : ""}.`,
    failed === 0 ? "ok" : "error");
  await reload();
}

async function syncPwToPrism(iss) {
  const repoPw = iss.data.repo_pw;
  if (!repoPw) { toast("No repo .pw.toml referenced.", "error"); return; }
  try {
    const result = await api("/api/sync-pw-to-prism", {
      method: "POST", body: JSON.stringify({ repo_pw: repoPw }),
    });
    let msg = "Copied " + iss.data.repo_pw_name + " into Prism";
    if (result.jar_status === "downloaded") msg += " (jar installed)";
    else if (result.jar_status === "cached") msg += " (jar already present)";
    if (result.warning) {
      toast("Metadata copied, but jar didn't install: " + result.warning, "error");
    } else {
      toast(msg + ".", "ok");
    }
    await reload();
  } catch (e) { toast(e.message, "error"); }
}

async function fixCfUrl(iss) {
  const e = iss.data.entry;
  showModal("Fix: " + e.slug,
    '<p><span class="spinner"></span> Checking Modrinth + CurseForge for fix options…</p>',
    [btn("Cancel", "", closeModal)]);
  let result;
  try {
    result = await api("/api/inspect-pw", {
      method: "POST", body: JSON.stringify({ path: e.pw_toml_path }),
    });
  } catch (err) { toast(err.message, "error"); closeModal(); return; }

  const body = document.createElement("div");
  body.innerHTML = `<p><b>${escapeHtml(e.name || e.slug)}</b> currently has <code>mode='metadata:curseforge'</code> with no usable URL.</p>`;
  const grid = document.createElement("div");
  grid.className = "choice-grid";

  let chosen = null;
  function makeCard(label, kind, iconUrl, info, onPick) {
    const c = document.createElement("div");
    c.className = "choice-card";
    c.innerHTML = `${modIconHtml(iconUrl, "large")}
      <span class="platform tag ${kind}">${label}</span>
      <div class="info"><div class="name">${escapeHtml(info.name)}</div>
        <div class="meta">${escapeHtml(info.meta)}</div></div>`;
    c.onclick = () => {
      grid.querySelectorAll(".choice-card").forEach(x => x.classList.remove("selected"));
      c.classList.add("selected");
      chosen = onPick;
    };
    return c;
  }

  // Modrinth swap (preferred when available)
  if (result.modrinth_match) {
    const v = result.modrinth_match;
    const proj = result.modrinth_project || {};
    const card = makeCard("Modrinth", "modrinth", proj.icon_url || "", {
      name: proj.title || v.name || v.version_number,
      meta: `Switch to Modrinth (same file by hash) · v${v.version_number || v.id}`,
    }, async () => "modrinth");
    card.classList.add("selected");
    chosen = async () => "modrinth";
    grid.appendChild(card);
  } else {
    const c = document.createElement("div");
    c.className = "choice-card"; c.style.opacity = "0.5";
    c.innerHTML = `${modIconHtml("", "large")}<span class="platform tag modrinth">Modrinth</span>`
      + '<div class="info"><div class="name">No hash match</div>'
      + "<div class=\"meta\">This file's hash isn't on Modrinth.</div></div>";
    grid.appendChild(c);
  }

  // CurseForge direct URL — icon comes from current entry's CF logo if cached
  const cfIcon = e.icon_url || "";
  if (result.curseforge_direct_url) {
    const url = result.curseforge_direct_url;
    grid.appendChild(makeCard("CurseForge", "curseforge", cfIcon, {
      name: "Use CurseForge direct download URL",
      meta: url.length > 80 ? url.slice(0, 80) + "…" : url,
    }, async () => "cf-direct"));
  } else if (!STATE.meta.curseforge_configured) {
    const c = document.createElement("div");
    c.className = "choice-card"; c.style.opacity = "0.5";
    c.innerHTML = `${modIconHtml(cfIcon, "large")}<span class="platform tag curseforge">CurseForge</span>`
      + '<div class="info"><div class="name">API key not set</div>'
      + '<div class="meta">Add CURSEFORGE_API_KEY in Settings to fetch a direct URL.</div></div>';
    grid.appendChild(c);
  } else {
    const c = document.createElement("div");
    c.className = "choice-card"; c.style.opacity = "0.5";
    c.innerHTML = `${modIconHtml(cfIcon, "large")}<span class="platform tag curseforge">CurseForge</span>`
      + '<div class="info"><div class="name">No direct URL</div>'
      + '<div class="meta">This project disables third-party downloads.</div></div>';
    grid.appendChild(c);
  }

  body.appendChild(grid);

  showModal("Fix: " + e.slug, body, [
    btn("Cancel", "", closeModal),
    btn("Apply", "primary", async () => {
      if (!chosen) { toast("Pick an option first.", "error"); return; }
      const action = await chosen();
      try {
        if (action === "modrinth") {
          await api("/api/swap-to-modrinth", {
            method: "POST",
            body: JSON.stringify({ path: e.pw_toml_path, modrinth_version: result.modrinth_match }),
          });
          toast("Switched " + e.pw_toml_name + " to Modrinth.", "ok");
        } else {
          await api("/api/fix-cf-url", {
            method: "POST", body: JSON.stringify({ path: e.pw_toml_path }),
          });
          toast("Updated " + e.pw_toml_name + " with CurseForge URL.", "ok");
        }
        closeModal();
        await reload();
      } catch (err) { toast(err.message, "error"); }
    }),
  ]);
}

// --- mods table ---
function switchToMods(filter) {
  // Combined view — just focus the mod search field with the filter prefilled.
  if (filter) {
    const s = $("#mod-search");
    if (s) { s.value = filter; renderMods(); s.focus(); s.scrollIntoView({behavior: "smooth", block: "start"}); }
  }
}

function renderMods() {
  if (!STATE) return;
  const q = ($("#mod-search").value || "").toLowerCase();
  const filter = $("#mod-filter").value;
  const tbody = $("#mod-tbody");
  tbody.innerHTML = "";

  // Build a map: pw_toml_name (or "jar:<filename>" for loose jars) -> {repo, prism}
  const groups = new Map();
  function group(key) {
    if (!groups.has(key)) groups.set(key, { repo: null, prism: null, isJar: false });
    return groups.get(key);
  }
  STATE.repo.entries.forEach(e => { group(e.pw_toml_name).repo = e; });
  STATE.prism.entries.forEach(e => { group(e.pw_toml_name).prism = e; });
  STATE.repo.loose_jars.forEach(j => {
    const g = group("jar:" + j.filename);
    g.repo = { name: j.filename, slug: "(loose)", filename: j.filename, side: "?",
               provider: "loose", path: j.path, pw_toml_path: j.path, isJar: true,
               pw_toml_name: j.filename };
    g.isJar = true;
  });
  STATE.prism.loose_jars.forEach(j => {
    const g = group("jar:" + j.filename);
    g.prism = { name: j.filename, slug: "(loose)", filename: j.filename, side: "?",
                provider: "loose", path: j.path, pw_toml_path: j.path, isJar: true,
                pw_toml_name: j.filename };
    g.isJar = true;
  });

  // Issues per pw_toml_name so we can mark rows with a warning indicator.
  const issuesByName = new Map();
  (STATE.issues || []).forEach(iss => {
    const names = new Set();
    if (iss.data && iss.data.entry && iss.data.entry.pw_toml_name) names.add(iss.data.entry.pw_toml_name);
    if (iss.data && iss.data.entries) iss.data.entries.forEach(e => names.add(e.pw_toml_name));
    if (iss.data && iss.data.jar && iss.data.jar.filename) names.add(iss.data.jar.filename);
    if (iss.data && iss.data.repo_pw_name) names.add(iss.data.repo_pw_name);
    if (iss.kind === "mismatch" && iss.targets) iss.targets.forEach(t => names.add(t));
    names.forEach(n => {
      if (!issuesByName.has(n)) issuesByName.set(n, []);
      issuesByName.get(n).push(iss);
    });
  });

  function diffFields(a, b) {
    if (!a || !b || a.isJar || b.isJar) return [];
    const out = [];
    const fields = ["filename", "download_hash", "download_url", "download_mode",
                    "modrinth_id", "modrinth_version", "curseforge_project", "curseforge_file"];
    fields.forEach(f => {
      if ((a[f] ?? "") !== (b[f] ?? "")) out.push(f);
    });
    return out;
  }

  // Convert groups → one row per mod. Status tag describes the relationship.
  const finalRows = [];
  for (const [key, g] of groups.entries()) {
    const base = g.repo || g.prism;
    let status = "synced";          // both sides identical
    let diffs = [];
    if (g.repo && g.prism) {
      diffs = diffFields(g.repo, g.prism);
      status = diffs.length ? "out-of-sync" : "synced";
    } else if (g.repo && !g.prism) {
      status = "repo-only";
    } else if (!g.repo && g.prism) {
      status = "prism-only";
    }
    finalRows.push({
      ...base,
      _key: key,
      _status: status,
      _diffs: diffs,
      _hasRepo: !!g.repo,
      _hasPrism: !!g.prism,
      _repoPath: g.repo ? g.repo.pw_toml_path : null,
      _prismPath: g.prism ? g.prism.pw_toml_path : null,
      _prismJarInstalled: (g.prism && "jar_installed" in g.prism) ? g.prism.jar_installed : null,
    });
  }

  const filtered = finalRows.filter(r => {
    if (filter === "repo" && !r._hasRepo) return false;
    if (filter === "prism" && !r._hasPrism) return false;
    if (filter === "modrinth" && r.provider !== "modrinth") return false;
    if (filter === "curseforge" && r.provider !== "curseforge") return false;
    if (filter === "custom" && r.provider !== "custom") return false;
    if (filter === "loose" && r.provider !== "loose") return false;
    if (q && ![r.name, r.slug, r.filename].some(x => (x || "").toLowerCase().includes(q))) return false;
    return true;
  });

  filtered.sort((a, b) => (a.name || "").localeCompare(b.name || ""));
  filtered.forEach(r => {
    const tr = document.createElement("tr");
    const icon = modIconHtml(r.icon_url || "", "small");

    let statusHtml = "";
    const sideNote = (r.side === "server")
      ? "\n\nNote: side='server', so Prism (client) won't have the jar installed — that's expected."
      : (r.side === "client")
        ? "\n\nNote: side='client' — only the player install gets this jar."
        : "";
    if (r._status === "synced") {
      statusHtml = '<span class="tag synced" title="Repo and Prism match">synced</span>';
    } else if (r._status === "out-of-sync") {
      const tooltip = "Repo and Prism .index/ have different metadata:\n" + r._diffs.map(f => "  • " + f).join("\n") + "\n\nThe jar (if any) stays installed; only the .pw.toml metadata differs." + sideNote;
      statusHtml = `<span class="tag out-of-sync" title="${escapeHtml(tooltip)}">out of sync</span>`;
    } else if (r._status === "repo-only") {
      statusHtml = `<span class="tag only" title="No matching .pw.toml in Prism's .index/${sideNote}">repo only</span>`;
    } else {
      statusHtml = '<span class="tag only" title="In Prism\'s .index/ but not in repo">prism only</span>';
    }
    // Annotate when Prism has the .pw.toml but the actual jar isn't installed
    if (r._hasPrism && r._prismJarInstalled === false) {
      statusHtml += ' <span class="tag out-of-sync" title="Prism has the .pw.toml but the jar isn\'t in mods/. Open Resolve to install.">no jar</span>';
    }

    const issues = issuesByName.get(r.pw_toml_name) || issuesByName.get(r.filename) || [];
    const issueIcon = issues.length
      ? `<span title="${escapeHtml(issues.map(i => '• ' + i.message).join('\n'))}" style="color:var(--warn);font-size:14px;margin-left:6px;">⚠</span>`
      : "";

    if (issues.length || r._status === "out-of-sync") tr.classList.add("has-issue");

    const side = (r.side || "both").toLowerCase();
    const repoPath = r._repoPath || r.pw_toml_path || "";
    const editable = !r.isJar && !!repoPath;
    const sideTag = (side === "client" || side === "server" || side === "both")
      ? `<span class="tag side-${side} ${editable ? 'clickable' : ''}" ${editable ? `data-action="side" data-path="${escapeHtml(repoPath)}" data-current="${side}"` : ''} title="${editable ? 'Click to change side' : ''}">${side}</span>`
      : `<span class="muted">${escapeHtml(r.side || "")}</span>`;
    const sourceTag = editable
      ? `<span class="tag ${r.provider} clickable" data-action="source" data-path="${escapeHtml(repoPath)}" title="Click to change source">${r.provider}</span>`
      : `<span class="tag ${r.provider}">${r.provider}</span>`;
    tr.innerHTML = `
      <td><div class="mod-name-row">${icon}<span>${escapeHtml(r.name || "")}${issueIcon}</span></div></td>
      <td><span class="tag">${escapeHtml(r.slug || r.filename)}</span><div class="muted" style="font-size:11px;margin-top:2px;">${escapeHtml(r.filename || "")}</div></td>
      <td>${sourceTag}</td>
      <td>${sideTag}</td>
      <td>${statusHtml}</td>
      <td></td>
    `;
    const acts = tr.lastElementChild;
    if (r.isJar) {
      const b = document.createElement("button");
      b.className = "small primary"; b.textContent = "Identify";
      b.onclick = () => identifyJar({ data: { jar: { path: r.path, filename: r.filename, size: 0 } } });
      acts.appendChild(b);
    } else {
      // If Prism has metadata but no jar, the most useful direct action is to install it
      if (r._hasPrism && r._prismJarInstalled === false && r._prismPath) {
        const installBtn = document.createElement("button");
        installBtn.className = "small primary";
        installBtn.textContent = "Install jar";
        installBtn.title = "Download the jar referenced by Prism's .pw.toml into mods/";
        installBtn.onclick = () => installMissingJar({
          data: { pw_toml_path: r._prismPath, filename: r.filename },
        });
        acts.appendChild(installBtn);
      }
      const needsAttention = r._status !== "synced";
      const resolve = document.createElement("button");
      resolve.className = "small " + (needsAttention ? "primary" : "");
      resolve.textContent = needsAttention ? "Resolve…" : "View…";
      resolve.title = needsAttention
        ? "See what differs between repo and Prism, and choose which side wins"
        : "Show this mod's details";
      resolve.onclick = () => openDiffModal(r);
      acts.appendChild(resolve);
    }
    tbody.appendChild(tr);
  });
}

// Event delegation: click on .clickable tags inside the mods table
document.addEventListener("click", (ev) => {
  const tag = ev.target.closest(".tag.clickable");
  if (!tag) return;
  const action = tag.dataset.action;
  const path = tag.dataset.path;
  if (!path) return;
  if (action === "side") openSidePicker(path, tag.dataset.current || "both");
  else if (action === "source") openSourcePicker(path);
});

async function openSidePicker(path, current) {
  const body = document.createElement("div");
  body.innerHTML = `<p>Pick which side this mod runs on:</p>`;
  const grid = document.createElement("div");
  grid.className = "choice-grid";
  let chosen = current;

  const opts = [
    { side: "both",   label: "both",   desc: "Loaded on client and server" },
    { side: "client", label: "client", desc: "Player-only (cosmetic, UI, optimization)" },
    { side: "server", label: "server", desc: "Server-only (game logic, world data)" },
  ];
  opts.forEach(o => {
    const c = document.createElement("div");
    c.className = "choice-card" + (o.side === current ? " selected" : "");
    c.innerHTML = `<span class="tag side-${o.side}">${o.label}</span>
      <div class="info"><div class="name">${o.label}</div><div class="meta">${escapeHtml(o.desc)}</div></div>`;
    c.onclick = () => {
      grid.querySelectorAll(".choice-card").forEach(x => x.classList.remove("selected"));
      c.classList.add("selected");
      chosen = o.side;
    };
    grid.appendChild(c);
  });
  body.appendChild(grid);

  showModal("Change side", body, [
    btn("Cancel", "", closeModal),
    btn("Save", "primary", async () => {
      if (chosen === current) { closeModal(); return; }
      try {
        await api("/api/set-side", {
          method: "POST", body: JSON.stringify({ path, side: chosen }),
        });
        toast(`Side set to "${chosen}".`, "ok");
        closeModal();
        await reload();
      } catch (e) { toast(e.message, "error"); }
    }),
  ]);
}

async function openSourcePicker(path) {
  showModal("Change source",
    '<p><span class="spinner"></span> Looking up Modrinth + CurseForge…</p>',
    [btn("Cancel", "", closeModal)]);
  let inspect;
  try {
    inspect = await api("/api/inspect-source", {
      method: "POST", body: JSON.stringify({ path }),
    });
  } catch (e) { toast(e.message, "error"); closeModal(); return; }

  const e = inspect.entry;
  const body = document.createElement("div");
  body.innerHTML = `<p><b>${escapeHtml(e.name || "")}</b><br>
    <span class="muted">currently: ${escapeHtml(inspect.current_source)}</span></p>`;

  const grid = document.createElement("div");
  grid.className = "choice-grid";
  let chosen = null; // {target, payload}

  function makeCard(target, label, kind, iconUrl, info, payload, disabled = false, reason = "") {
    const c = document.createElement("div");
    c.className = "choice-card" + (disabled ? "" : "");
    if (disabled) c.style.opacity = "0.5";
    c.innerHTML = `${modIconHtml(iconUrl, "large")}
      <span class="platform tag ${kind}">${label}</span>
      <div class="info"><div class="name">${escapeHtml(info.name)}</div>
        <div class="meta">${escapeHtml(disabled ? reason : info.meta)}</div></div>`;
    if (!disabled) {
      c.onclick = () => {
        grid.querySelectorAll(".choice-card").forEach(x => x.classList.remove("selected"));
        c.classList.add("selected");
        chosen = { target, payload };
      };
    }
    return c;
  }

  // Modrinth
  if (inspect.modrinth_match) {
    const v = inspect.modrinth_match;
    const proj = inspect.modrinth_project || {};
    const card = makeCard(
      "modrinth", "Modrinth", "modrinth", proj.icon_url || "",
      { name: proj.title || v.name, meta: `version ${v.version_number || v.id} · same file by hash` },
      { modrinth_version: v },
    );
    if (inspect.current_source === "modrinth") {
      card.style.opacity = "0.5";
      card.querySelector(".meta").textContent = "Already on Modrinth.";
      card.onclick = null;
    }
    grid.appendChild(card);
  } else {
    grid.appendChild(makeCard(
      "modrinth", "Modrinth", "modrinth", "",
      { name: "No match", meta: "" }, null, true,
      "This file's hash isn't on Modrinth.",
    ));
  }

  // CurseForge
  if (inspect.curseforge_candidates && inspect.curseforge_candidates.length) {
    inspect.curseforge_candidates.forEach((cand, i) => {
      const mod = cand.mod || {};
      const logo = (mod.logo && mod.logo.url) || "";
      const exact = cand.exact;
      const meta = exact
        ? `Exact match by hash · project ${mod.id || "?"}`
        : `Search match · project ${mod.id || "?"}`;
      grid.appendChild(makeCard(
        "curseforge", "CurseForge", "curseforge", logo,
        { name: mod.name || "(unknown)", meta },
        cand.match
          ? { curseforge_match: cand.match }
          : { project_id: mod.id },
      ));
    });
  } else if (!inspect.curseforge_configured) {
    grid.appendChild(makeCard(
      "curseforge", "CurseForge", "curseforge", "",
      { name: "API key not set", meta: "" }, null, true,
      "Add CURSEFORGE_API_KEY in Settings to enable.",
    ));
  } else {
    grid.appendChild(makeCard(
      "curseforge", "CurseForge", "curseforge", "",
      { name: "No matches", meta: "" }, null, true,
      "Couldn't find this mod on CurseForge by hash or name.",
    ));
  }

  // Custom
  if (inspect.github_slug) {
    grid.appendChild(makeCard(
      "custom", "Custom", "custom", "",
      { name: "Serve from GitHub raw",
        meta: `Rewrites .pw.toml with raw.githubusercontent.com URL on branch "main". Requires the jar to be in the repo.` },
      {},
    ));
  } else {
    grid.appendChild(makeCard(
      "custom", "Custom", "custom", "",
      { name: "No GitHub remote", meta: "" }, null, true,
      "Set a GitHub origin remote to enable Custom mode.",
    ));
  }

  body.appendChild(grid);

  showModal("Change source for: " + (e.name || e.slug), body, [
    btn("Cancel", "", closeModal),
    btn("Apply", "primary", async () => {
      if (!chosen) { toast("Pick an option first.", "error"); return; }
      const endpoint = "/api/swap-to-" + chosen.target;
      try {
        await api(endpoint, {
          method: "POST",
          body: JSON.stringify({ path, ...chosen.payload }),
        });
        toast(`Switched to ${chosen.target}.`, "ok");
        closeModal();
        await reload();
      } catch (err) { toast(err.message, "error"); }
    }),
  ]);
}

// Per-field guidance shown in the diff modal's help panel.
const FIELD_HELP = {
  download_mode: {
    title: "Download mode",
    text: "How packwiz fetches the jar at install time.",
    values: {
      "url": "Direct URL. Anyone can install — no API key needed. Fastest. Recommended when a stable URL exists.",
      "metadata:curseforge": "Looks up the file via the CurseForge API at install time. Each machine that installs needs CF_API_KEY set. Required only when the project disables third-party direct downloads.",
      "metadata:modrinth": "Looks up via Modrinth API. Rare — Modrinth files are normally directly downloadable.",
    },
    recommend: "Prefer 'url'. Fall back to 'metadata:curseforge' only when the project blocks direct downloads (then a forgecdn URL would 404).",
  },
  download_url: {
    title: "Download URL",
    text: "Where the jar comes from. Must match the mode above.",
    recommend: "An empty URL with mode='metadata:curseforge' means anyone installing this pack must have a CF API key. A working forgecdn.net or cdn.modrinth.com URL is preferable.",
  },
  download_hash: {
    title: "Hash",
    text: "Used to verify the downloaded jar matches what packwiz expects. Mismatched hashes mean someone uploaded a different file under the same URL/version.",
  },
  download_hash_format: {
    title: "Hash format",
    text: "Modrinth uses sha512; CurseForge uses sha1. Should not be changed manually.",
  },
  side: {
    title: "Side",
    values: {
      "client": "Player-only — UI, optimization, cosmetic mods. Server install ignores it.",
      "server": "Server-only — game logic, world generation. Client install ignores it.",
      "both": "Loaded on both client and server. Default for most mods.",
    },
    recommend: "If you're not sure, 'both' is the safe default. Set to 'server' for performance/admin mods, 'client' for visual mods.",
  },
  filename: {
    title: "Filename",
    text: "The actual jar name. Different filenames usually means different mod versions.",
  },
  name: {
    title: "Display name",
    text: "Cosmetic — what gets shown in the launcher.",
  },
  modrinth_id: {
    title: "Modrinth project ID",
    text: "Identifies the Modrinth project. Differing IDs mean the two sides reference completely different mods.",
  },
  modrinth_version: {
    title: "Modrinth version ID",
    text: "Identifies a specific release. Differing versions mean one side is older/newer.",
  },
  curseforge_project: {
    title: "CurseForge project ID",
    text: "Identifies the CurseForge project.",
  },
  curseforge_file: {
    title: "CurseForge file ID",
    text: "Identifies a specific upload. Differing IDs usually means different versions.",
  },
};

function recommendDirection(d, status) {
  if (status !== "out-of-sync") return null;
  // Heuristic: prefer the side with mode='url' over metadata:* (works without API keys).
  const modeDiff = d.summary.find(s => s.field === "download_mode" && s.differs);
  if (modeDiff) {
    if (modeDiff.repo === "url" && modeDiff.prism.startsWith("metadata:")) {
      return {
        text: "<b>Push repo → Prism.</b> The repo has a direct download URL (works without an API key); Prism's <code>metadata:curseforge</code> requires CurseForge API access at install time.",
      };
    }
    if (modeDiff.prism === "url" && modeDiff.repo.startsWith("metadata:")) {
      return {
        text: "<b>Pull Prism → repo.</b> Prism has a direct download URL; the repo's mode requires API access. The Prism version is more portable.",
      };
    }
  }
  // If only the URL differs and one is empty, push the non-empty one
  const urlDiff = d.summary.find(s => s.field === "download_url" && s.differs);
  if (urlDiff && (!urlDiff.repo || !urlDiff.prism)) {
    if (urlDiff.repo && !urlDiff.prism) {
      return { text: "<b>Push repo → Prism.</b> Repo has a download URL; Prism's is empty." };
    }
    if (!urlDiff.repo && urlDiff.prism) {
      return { text: "<b>Pull Prism → repo.</b> Prism has a download URL; the repo's is empty." };
    }
  }
  // If versions differ, prefer the newer one — but we can't tell direction here.
  return null;
}

function renderDiffHelp(d, status) {
  const parts = [];
  parts.push(`<h4>What this means</h4>`);

  if (status === "synced") {
    parts.push(`<p class="muted">Both sides are identical. Nothing to do.</p>`);
    return parts.join("");
  }

  const rec = recommendDirection(d, status);
  if (rec) {
    parts.push(`<div class="help-rec">
      <div class="help-rec-title">💡 Suggested fix</div>
      <div>${rec.text}</div>
    </div>`);
  }

  const differing = d.summary.filter(s => s.differs);
  if (differing.length === 0 && status !== "synced") {
    // Existence diff (one side missing entirely)
    parts.push(`<p class="muted">${
      status === "repo-only"
        ? "This .pw.toml exists in the repo but not in Prism. Add it to Prism to track the mod there too, or delete it from the repo to drop it entirely."
        : "This .pw.toml exists in Prism but not in the repo. Add it to the repo (so it's tracked in git), or delete it from Prism if it was added by mistake."
    }</p>`);
    return parts.join("");
  }

  parts.push(`<h4 style="margin-top:14px;">Differing fields</h4>`);
  differing.forEach(s => {
    const help = FIELD_HELP[s.field];
    if (!help) {
      parts.push(`<div class="help-card"><div class="help-title">${escapeHtml(s.label)}</div></div>`);
      return;
    }
    let out = `<div class="help-card"><div class="help-title">${escapeHtml(help.title)}</div>`;
    if (help.text) out += `<div class="help-text">${escapeHtml(help.text)}</div>`;
    if (help.values) {
      const used = [s.repo, s.prism].filter(v => v && help.values[v]);
      const seen = new Set();
      used.forEach(v => {
        if (seen.has(v)) return;
        seen.add(v);
        out += `<div class="help-value"><code>${escapeHtml(v)}</code> — ${escapeHtml(help.values[v])}</div>`;
      });
    }
    if (help.recommend) {
      out += `<div class="help-rec-text">${escapeHtml(help.recommend)}</div>`;
    }
    out += `</div>`;
    parts.push(out);
  });
  return parts.join("");
}

// --- Configs diff -----------------------------------------------------------

let CONFIGS_DIFF = null;
let CONFIGS_FILTER = "all";

async function loadConfigsDiff() {
  try {
    CONFIGS_DIFF = await api("/api/configs-diff", { method: "POST", body: "{}" });
    renderConfigsCard();
  } catch (e) { /* non-fatal */ }
}

function renderConfigsCard() {
  if (!CONFIGS_DIFF) return;
  const card = $("#configs-card");
  const big = $("#configs-big");
  const sub = $("#configs-sub");
  if (!big || !sub || !card) return;
  const s = CONFIGS_DIFF.summary;
  card.classList.remove("warn", "err");
  if (!s.prism_configured) {
    big.textContent = "—";
    sub.textContent = "Prism not configured";
    return;
  }
  if (s.total_diff === 0) {
    big.innerHTML = `<span style="color:var(--ok);">0</span>`;
    sub.textContent = `${s.synced} files synced`;
  } else {
    big.innerHTML = `<span style="color:var(--warn);">${s.total_diff}</span>`;
    card.classList.add("warn");
    const parts = [];
    if (s.different) parts.push(`${s.different} different`);
    if (s.repo_only) parts.push(`${s.repo_only} repo only`);
    if (s.prism_only) parts.push(`${s.prism_only} prism only`);
    sub.textContent = parts.join(" · ") + ` · ${s.synced} synced`;
  }
}

async function openConfigsModal() {
  showModal("Configs", '<p><span class="spinner"></span> Scanning configs…</p>',
    [btn("Cancel", "", closeModal)], { wide: true });
  try {
    CONFIGS_DIFF = await api("/api/configs-diff", { method: "POST", body: "{}" });
  } catch (e) { toast(e.message, "error"); closeModal(); return; }
  renderConfigsModal();
}

function renderConfigsModal() {
  const items = (CONFIGS_DIFF && CONFIGS_DIFF.items) || [];
  const summary = (CONFIGS_DIFF && CONFIGS_DIFF.summary) || {};

  const body = document.createElement("div");

  if (!summary.prism_configured) {
    body.innerHTML = '<p>Set the Prism INSTANCE in Settings before scanning configs.</p>';
    showModal("Configs", body, [btn("Close", "", closeModal)], { wide: true });
    return;
  }

  // Filter row
  const filterRow = document.createElement("div");
  filterRow.style.display = "flex";
  filterRow.style.gap = "6px";
  filterRow.style.marginBottom = "12px";
  filterRow.style.flexWrap = "wrap";
  const filters = [
    { v: "all",         label: `All (${items.length})` },
    { v: "different",   label: `Different (${summary.different})` },
    { v: "repo-only",   label: `Repo only (${summary.repo_only})` },
    { v: "prism-only",  label: `Prism only (${summary.prism_only})` },
  ];
  filters.forEach(f => {
    const b = document.createElement("button");
    b.className = "small" + (CONFIGS_FILTER === f.v ? " primary" : "");
    b.textContent = f.label;
    b.onclick = () => { CONFIGS_FILTER = f.v; renderConfigsModal(); };
    filterRow.appendChild(b);
  });
  body.appendChild(filterRow);

  const filtered = CONFIGS_FILTER === "all"
    ? items
    : items.filter(i => i.status === CONFIGS_FILTER);

  // Bulk actions
  if (filtered.length > 0) {
    const bulk = document.createElement("div");
    bulk.style.display = "flex";
    bulk.style.gap = "6px";
    bulk.style.marginBottom = "12px";
    bulk.style.alignItems = "center";
    const counter = document.createElement("span");
    counter.className = "muted";
    counter.style.fontSize = "12px";
    counter.style.marginRight = "auto";
    counter.textContent = `${filtered.length} item${filtered.length === 1 ? "" : "s"} shown`;
    bulk.appendChild(counter);
    bulk.appendChild(btn("Push visible to Prism", "small primary",
      () => bulkConfigOp(filtered, "push")));
    bulk.appendChild(btn("Pull visible from Prism", "small",
      () => bulkConfigOp(filtered, "pull")));
    body.appendChild(bulk);
  }

  // Items list
  const list = document.createElement("div");
  list.className = "configs-list";
  if (filtered.length === 0) {
    list.innerHTML = items.length === 0
      ? `<p class="muted" style="padding:20px;text-align:center;">All ${summary.synced} config files are in sync. ✓</p>`
      : `<p class="muted" style="padding:20px;text-align:center;">No items match this filter.</p>`;
  } else {
    filtered.forEach(item => list.appendChild(renderConfigRow(item)));
  }
  body.appendChild(list);

  showModal("Configs", body, [
    btn("Close", "", closeModal),
    btn("↻ Rescan", "", async () => {
      try {
        CONFIGS_DIFF = await api("/api/configs-diff", { method: "POST", body: "{}" });
        renderConfigsModal();
        renderConfigsCard();
      } catch (e) { toast(e.message, "error"); }
    }),
  ], { wide: true });
}

function renderConfigRow(item) {
  const row = document.createElement("div");
  row.className = "config-row";
  const sizeText = item.status === "different"
    ? `repo ${formatBytes(item.repo_size)} · prism ${formatBytes(item.prism_size)}`
    : item.status === "repo-only"
      ? formatBytes(item.repo_size)
      : formatBytes(item.prism_size);
  row.innerHTML = `
    <div class="config-path">
      <code>${escapeHtml(item.kind + "/" + item.relpath)}</code>
      <div class="muted" style="font-size:11px;margin-top:1px;">${escapeHtml(sizeText)}</div>
    </div>
    <div class="config-status">${configStatusTag(item.status)}</div>
    <div class="config-actions"></div>
  `;
  const actions = row.querySelector(".config-actions");
  if (item.status === "different") {
    actions.appendChild(btn("View", "small", () => openConfigDiffViewer(item)));
    actions.appendChild(btn("Push →", "small primary", () => syncConfig(item, "push")));
    actions.appendChild(btn("← Pull", "small", () => syncConfig(item, "pull")));
  } else if (item.status === "repo-only") {
    actions.appendChild(btn("View", "small", () => openConfigDiffViewer(item)));
    actions.appendChild(btn("Push →", "small primary", () => syncConfig(item, "push")));
    actions.appendChild(btn("Delete", "small danger", () => deleteConfig(item, "repo")));
  } else {
    actions.appendChild(btn("View", "small", () => openConfigDiffViewer(item)));
    actions.appendChild(btn("← Pull", "small primary", () => syncConfig(item, "pull")));
    actions.appendChild(btn("Delete", "small danger", () => deleteConfig(item, "prism")));
  }
  return row;
}

function configStatusTag(status) {
  if (status === "different") return '<span class="tag out-of-sync">different</span>';
  if (status === "repo-only") return '<span class="tag only">repo only</span>';
  if (status === "prism-only") return '<span class="tag only">prism only</span>';
  return `<span class="tag">${escapeHtml(status)}</span>`;
}

function formatBytes(n) {
  if (n == null) return "?";
  if (n < 1024) return n + " B";
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KiB";
  return (n / 1024 / 1024).toFixed(1) + " MiB";
}

async function syncConfig(item, direction) {
  try {
    await api("/api/sync-config", {
      method: "POST",
      body: JSON.stringify({ kind: item.kind, relpath: item.relpath, direction }),
    });
    toast(`${direction === "push" ? "Pushed" : "Pulled"} ${item.relpath}`, "ok");
    CONFIGS_DIFF = await api("/api/configs-diff", { method: "POST", body: "{}" });
    if ($("#modal-bg").classList.contains("show")) renderConfigsModal();
    renderConfigsCard();
  } catch (e) { toast(e.message, "error"); }
}

async function deleteConfig(item, side) {
  const ok = await confirmModal({
    title: "Delete config file",
    body: `<p>Delete <code>${escapeHtml(item.kind + "/" + item.relpath)}</code> from <b>${side}</b>?</p>
           <p class="muted">This action only removes the file from ${side === "repo" ? "the repo" : "Prism"}; the other side is untouched.</p>`,
    confirmText: "Delete",
    confirmKind: "danger",
  });
  if (!ok) return;
  try {
    await api("/api/delete-config", {
      method: "POST",
      body: JSON.stringify({ kind: item.kind, relpath: item.relpath, side }),
    });
    toast("Deleted.", "ok");
    CONFIGS_DIFF = await api("/api/configs-diff", { method: "POST", body: "{}" });
    if ($("#modal-bg").classList.contains("show")) renderConfigsModal();
    renderConfigsCard();
  } catch (e) { toast(e.message, "error"); }
}

async function bulkConfigOp(items, direction) {
  const verb = direction === "push" ? "Push" : "Pull";
  const eligible = items.filter(i =>
    direction === "push" ? i.status !== "prism-only" : i.status !== "repo-only");
  if (eligible.length === 0) {
    toast(`Nothing to ${direction} — none of the visible files are eligible.`, "error");
    return;
  }
  const ok = await confirmModal({
    title: `${verb} ${eligible.length} config file${eligible.length === 1 ? "" : "s"}`,
    body: `<p>${direction === "push"
      ? `Copy ${eligible.length} file(s) from <b>repo</b> → <b>Prism</b>.`
      : `Copy ${eligible.length} file(s) from <b>Prism</b> → <b>repo</b>.`}</p>
      ${eligible.length < items.length ? `<p class="muted">${items.length - eligible.length} skipped (only exist on the other side).</p>` : ""}`,
    confirmText: verb + " all",
    confirmKind: "primary",
  });
  if (!ok) return;
  let success = 0, failed = 0;
  for (const item of eligible) {
    try {
      await api("/api/sync-config", {
        method: "POST",
        body: JSON.stringify({ kind: item.kind, relpath: item.relpath, direction }),
      });
      success++;
    } catch (e) { failed++; }
  }
  toast(`${success} ${verb.toLowerCase()}ed${failed ? ", " + failed + " failed" : ""}`,
    failed === 0 ? "ok" : "error");
  CONFIGS_DIFF = await api("/api/configs-diff", { method: "POST", body: "{}" });
  if ($("#modal-bg").classList.contains("show")) renderConfigsModal();
  renderConfigsCard();
}

async function openConfigDiffViewer(item) {
  showModal("Loading…", '<p><span class="spinner"></span> Reading file…</p>',
    [btn("Cancel", "", closeModal)], { wide: true });
  let data;
  try {
    data = await api("/api/view-config", {
      method: "POST",
      body: JSON.stringify({ kind: item.kind, relpath: item.relpath }),
    });
  } catch (e) { toast(e.message, "error"); closeModal(); return; }

  const isBinary = (data.repo && data.repo.binary) || (data.prism && data.prism.binary);
  const tooLarge = (data.repo && data.repo.too_large) || (data.prism && data.prism.too_large);

  if (isBinary || tooLarge) {
    const reason = isBinary ? "binary content" : "the file is too large";
    const footer = [btn("Close", "", closeModal)];
    if (item.status === "different" || item.status === "repo-only")
      footer.push(btn("Push →", "primary", () => { closeModal(); syncConfig(item, "push"); }));
    if (item.status === "different" || item.status === "prism-only")
      footer.push(btn("← Pull", "", () => { closeModal(); syncConfig(item, "pull"); }));
    showModal("Can't display: " + item.relpath,
      `<p>Can't show this file inline (${reason}).</p>
       <p class="muted">repo: ${data.repo.exists ? formatBytes(data.repo.size||0) : "(absent)"}<br>
       prism: ${data.prism.exists ? formatBytes(data.prism.size||0) : "(absent)"}</p>
       <p>You can still push or pull it without viewing.</p>`,
      footer, { wide: false });
    return;
  }

  const repoText = (data.repo.text != null) ? data.repo.text : "";
  const prismText = (data.prism.text != null) ? data.prism.text : "";
  const repoExists = data.repo.exists;
  const prismExists = data.prism.exists;

  const body = document.createElement("div");
  const header = document.createElement("p");
  header.innerHTML = `<code style="font-size:13px;">${escapeHtml(item.kind + "/" + item.relpath)}</code>`;
  body.appendChild(header);

  const diffEl = document.createElement("div");
  diffEl.className = "text-diff";
  diffEl.innerHTML = renderTextDiff(repoText, prismText, repoExists, prismExists);
  body.appendChild(diffEl);

  const footer = [btn("Close", "", closeModal)];
  if (item.status === "different" || item.status === "repo-only")
    footer.push(btn("Push to Prism →", "primary",
      () => { closeModal(); syncConfig(item, "push"); }));
  if (item.status === "different" || item.status === "prism-only")
    footer.push(btn("← Pull from Prism", "",
      () => { closeModal(); syncConfig(item, "pull"); }));

  showModal("Diff: " + item.relpath, body, footer, { wide: true });
}

function renderTextDiff(a, b, aExists, bExists) {
  const aLines = aExists ? a.split(/\r?\n/) : [];
  const bLines = bExists ? b.split(/\r?\n/) : [];
  const max = Math.max(aLines.length, bLines.length);
  const rows = [];
  rows.push(`<div class="td-row td-header">
    <div class="td-num">#</div>
    <div class="td-cell"><span class="tag repo">repo</span>${!aExists ? ' <span class="diff-empty">(absent)</span>' : ""}</div>
    <div class="td-cell"><span class="tag prism">prism</span>${!bExists ? ' <span class="diff-empty">(absent)</span>' : ""}</div>
  </div>`);
  for (let i = 0; i < max; i++) {
    const av = aLines[i];
    const bv = bLines[i];
    const eq = av === bv;
    const cls = "td-row" + (eq ? "" : " differs");
    const aHtml = av === undefined ? '<span class="diff-empty">·</span>' : (av === "" ? "&nbsp;" : escapeHtml(av));
    const bHtml = bv === undefined ? '<span class="diff-empty">·</span>' : (bv === "" ? "&nbsp;" : escapeHtml(bv));
    rows.push(`<div class="${cls}">
      <div class="td-num">${i + 1}</div>
      <div class="td-cell">${aHtml}</div>
      <div class="td-cell">${bHtml}</div>
    </div>`);
  }
  return rows.join("");
}

async function openDiffModal(row) {
  showModal("Loading…", '<p><span class="spinner"></span> Reading both sides…</p>',
    [btn("Cancel", "", closeModal)], { wide: true });
  let d;
  try {
    d = await api("/api/diff-pw", {
      method: "POST",
      body: JSON.stringify({ name: row.pw_toml_name, kind: "mods" }),
    });
  } catch (e) { toast(e.message, "error"); closeModal(); return; }

  const body = document.createElement("div");

  // Header with mod name + icon
  const headerIcon = modIconHtml(row.icon_url || "", "large");
  const status = row._status;
  const statusBadge = status === "synced"
    ? '<span class="tag synced">synced</span>'
    : status === "out-of-sync"
      ? '<span class="tag out-of-sync">out of sync</span>'
      : status === "repo-only"
        ? '<span class="tag only">repo only</span>'
        : '<span class="tag only">prism only</span>';
  const header = document.createElement("div");
  header.className = "mod-name-row";
  header.style.gap = "12px";
  header.innerHTML = `${headerIcon}
    <div style="flex:1;">
      <div style="font-size:16px;font-weight:600;">${escapeHtml(row.name || row.pw_toml_name)}</div>
      <div class="muted" style="font-size:12px;">${escapeHtml(row.pw_toml_name)}</div>
    </div>
    <div>${statusBadge}</div>`;
  body.appendChild(header);

  // Two-column layout: diff grid on the left, help panel on the right
  const layout = document.createElement("div");
  layout.className = "diff-layout";

  const grid = document.createElement("div");
  grid.className = "diff-grid";
  grid.innerHTML = `
    <div class="diff-header">Field</div>
    <div class="diff-header"><span class="tag repo">repo</span> ${d.repo.exists ? "" : '<span class="diff-empty">(absent)</span>'}</div>
    <div class="diff-header"><span class="tag prism">prism</span> ${d.prism.exists ? "" : '<span class="diff-empty">(absent)</span>'}</div>
  `;
  d.summary.forEach(s => {
    const wrap = (text) => text === "" || text == null
      ? '<span class="diff-empty">—</span>'
      : escapeHtml(String(text));
    const repoCell = d.repo.exists ? wrap(s.repo) : '<span class="diff-empty">—</span>';
    const prismCell = d.prism.exists ? wrap(s.prism) : '<span class="diff-empty">—</span>';
    const cls = s.differs ? "diff-row differs" : "diff-row";
    grid.insertAdjacentHTML("beforeend", `
      <div class="${cls} diff-label">${escapeHtml(s.label)}</div>
      <div class="${cls} diff-cell">${repoCell}</div>
      <div class="${cls} diff-cell">${prismCell}</div>
    `);
  });
  layout.appendChild(grid);

  // Help panel on the right
  const help = document.createElement("div");
  help.className = "help-panel";
  help.innerHTML = renderDiffHelp(d, status);
  layout.appendChild(help);

  body.appendChild(layout);

  // Merge direction options
  const opts = document.createElement("div");
  opts.className = "merge-options";
  let chosen = null;

  function makeOpt(label, sub, kind, action) {
    const o = document.createElement("div");
    o.className = "merge-option" + (kind === "danger" ? " danger" : "");
    o.innerHTML = `<div class="arrow">${label === "left" ? "←" : (label === "right" ? "→" : "✕")}</div>
      <div class="title">${escapeHtml(sub.title)}</div>
      <div class="sub">${escapeHtml(sub.text)}</div>`;
    o.onclick = () => {
      opts.querySelectorAll(".merge-option").forEach(x => x.classList.remove("selected"));
      o.classList.add("selected");
      chosen = action;
    };
    return o;
  }

  if (d.repo.exists && d.prism.exists) {
    if (status === "out-of-sync") {
      opts.appendChild(makeOpt("right", {
        title: "Use repo version → Prism",
        text: "Push the repo's .pw.toml into Prism's .index/. Installed jar is untouched.",
      }, "primary", "push"));
      opts.appendChild(makeOpt("left", {
        title: "Use Prism version → repo",
        text: "Replace the repo's .pw.toml with Prism's version. Use if you edited via Prism.",
      }, "primary", "pull-overwrite"));
    } else {
      // synced
      opts.style.gridTemplateColumns = "1fr";
      const note = document.createElement("div");
      note.className = "muted";
      note.style.textAlign = "center";
      note.style.padding = "12px";
      note.textContent = "Both sides match — nothing to resolve.";
      opts.appendChild(note);
    }
  } else if (d.repo.exists && !d.prism.exists) {
    opts.appendChild(makeOpt("right", {
      title: "Add to Prism",
      text: "Copy this .pw.toml into Prism's .index/. Run Local→Prism after to install the jar.",
    }, "primary", "push"));
    opts.appendChild(makeOpt("x", {
      title: "Delete from repo",
      text: "Remove this .pw.toml entirely. The repo will no longer track this mod.",
    }, "danger", "delete-repo"));
  } else if (!d.repo.exists && d.prism.exists) {
    opts.appendChild(makeOpt("left", {
      title: "Add to repo",
      text: "Copy this .pw.toml from Prism's .index/ into the repo's mods/.",
    }, "primary", "pull-add"));
    opts.appendChild(makeOpt("x", {
      title: "Delete from Prism",
      text: "Remove the .pw.toml from Prism's .index/. The installed jar stays.",
    }, "danger", "delete-prism"));
  }
  body.appendChild(opts);

  showModal("Resolve: " + (row.name || row.pw_toml_name), body, [
    btn("Cancel", "", closeModal),
    btn("Apply", "primary", async () => {
      if (!chosen) {
        if (status === "synced") { closeModal(); return; }
        toast("Pick a direction first.", "error"); return;
      }
      try {
        if (chosen === "push") {
          await api("/api/sync-pw-to-prism", {
            method: "POST", body: JSON.stringify({ repo_pw: d.repo.path }),
          });
          toast("Pushed repo → Prism.", "ok");
        } else if (chosen === "pull-overwrite") {
          await api("/api/sync-pw-from-prism", {
            method: "POST", body: JSON.stringify({ name: row.pw_toml_name, kind: d.kind }),
          });
          toast("Pulled Prism → repo (overwrote).", "ok");
        } else if (chosen === "pull-add") {
          await api("/api/sync-pw-from-prism", {
            method: "POST", body: JSON.stringify({ name: row.pw_toml_name, kind: d.kind }),
          });
          toast("Added to repo.", "ok");
        } else if (chosen === "delete-repo") {
          await api("/api/delete-pw", {
            method: "POST", body: JSON.stringify({ path: d.repo.path }),
          });
          toast("Deleted from repo.", "ok");
        } else if (chosen === "delete-prism") {
          await api("/api/delete-from-prism", {
            method: "POST", body: JSON.stringify({ name: row.pw_toml_name, kind: d.kind }),
          });
          toast("Deleted from Prism.", "ok");
        }
        closeModal();
        await reload();
      } catch (e) { toast(e.message, "error"); }
    }),
  ], { wide: true });
}

async function syncRepoEntryToPrism(r) {
  if (!r._repoPath) { toast("No repo .pw.toml to sync.", "error"); return; }
  try {
    const result = await api("/api/sync-pw-to-prism", {
      method: "POST", body: JSON.stringify({ repo_pw: r._repoPath }),
    });
    let msg = "Synced " + r.pw_toml_name;
    if (result.jar_status === "downloaded") msg += " — jar installed.";
    else if (result.jar_status === "cached") msg += " — jar already present.";
    if (result.warning) {
      // .pw.toml copied but jar didn't install
      toast("Metadata synced, but jar didn't install: " + result.warning, "error");
    } else {
      toast(msg, "ok");
    }
    await reload();
  } catch (e) { toast(e.message, "error"); }
}

async function pullPwFromPrism(name, overwrite) {
  const ok = await confirmModal({
    title: overwrite ? "Pull from Prism (overwrite repo)" : "Add to repo",
    body: overwrite
      ? `<p>Replace the repo's <code>${escapeHtml(name)}</code> with Prism's version?</p>
         <p class="muted">Use this when you've edited the mod via Prism's UI and want the repo to match. The Prism .index/ version becomes the new source of truth.</p>`
      : `<p>Copy <code>${escapeHtml(name)}</code> from Prism's <code>mods/.index/</code> into the repo's <code>mods/</code>?</p>
         <p class="muted">After this both sides will be in sync. Don't forget to <code>git add</code>, commit, and push if you want others to get this mod.</p>`,
    confirmText: overwrite ? "Overwrite" : "Add",
    confirmKind: overwrite ? "danger" : "primary",
  });
  if (!ok) return;
  try {
    await api("/api/sync-pw-from-prism", {
      method: "POST", body: JSON.stringify({ name, kind: "mods" }),
    });
    toast(overwrite ? `Pulled ${name} from Prism.` : `Added ${name} to repo.`, "ok");
    await reload();
  } catch (e) { toast(e.message, "error"); }
}

async function deletePw(r) {
  const ok = await confirmModal({
    title: "Delete from repo",
    body: `<p>Delete <code>${escapeHtml(r.pw_toml_name)}</code> from the repo's <code>mods/</code>?</p>
           <p class="muted">If a matching entry exists in Prism's <code>.index/</code>, it will also be removed so the two stay in sync.</p>`,
    confirmText: "Delete",
    confirmKind: "danger",
  });
  if (!ok) return;
  try {
    await api("/api/delete-pw", { method: "POST", body: JSON.stringify({ path: r.pw_toml_path }) });
    toast("Deleted.", "ok");
    await reload();
  } catch (e) { toast(e.message, "error"); }
}

// --- branches ---
async function refreshBranches() {
  try {
    const branches = await api("/api/branches");
    const sel = $("#branch-select");
    const cur = sel.value;
    sel.innerHTML = "";
    branches.forEach(b => {
      const o = document.createElement("option"); o.value = b; o.textContent = b;
      sel.appendChild(o);
    });
    if (branches.includes(cur)) sel.value = cur;
    else if (branches.includes("main")) sel.value = "main";
  } catch (e) { /* non-fatal */ }
}

// --- actions ---
async function runAction(path, label, body = {}, confirmOpts = null) {
  const ok = await confirmModal(confirmOpts || {
    title: `Run: ${label}`,
    body: `<p>Run "<b>${escapeHtml(label)}</b>"?</p>
           <p class="muted">Logs will stream live in the Logs panel.</p>`,
    confirmText: "Run",
    confirmKind: "primary",
  });
  if (!ok) return;
  try {
    const r = await api("/api/action/" + path, { method: "POST", body: JSON.stringify(body) });
    toast(`Started: ${r.name}`, "ok");
    LIVE_JOB_ID = r.job_id;
    await refreshJobs();
    const sel = document.getElementById("job-select");
    if (sel) sel.value = r.job_id;
    loadJobLog();
  } catch (e) { toast(e.message, "error"); }
}

async function confirmDeploy() {
  const s = STATE.server;
  let warn = "";
  if (!s.reachable) warn = '<p class="muted">Note: server is currently unreachable.</p>';
  else if (s.running && s.online > 0) warn = `<p style="color:var(--warn)"><b>⚠ ${s.online} player(s)</b> currently online — they'll be disconnected.</p>`;
  await runAction("local-to-server", "Local → Server", {}, {
    title: "Deploy to server",
    body: `<p>This will:</p>
           <ol style="margin:0 0 8px 18px;padding:0;">
             <li>Start <code>packwiz serve</code> locally</li>
             <li>Stop the server</li>
             <li>rsync the pack to the server</li>
             <li>Start the server</li>
           </ol>${warn}`,
    confirmText: "Deploy",
    confirmKind: "danger",
  });
}

async function confirmGithubToPrism() {
  const branch = $("#branch-select").value;
  if (!branch) { toast("No branch selected.", "error"); return; }
  await runAction("github-to-prism", `GitHub(${branch}) → Prism`, { branch }, {
    title: `Install branch "${branch}" into Prism`,
    body: `<p>Fetch <code>pack.toml</code> from branch <b>${escapeHtml(branch)}</b> on GitHub and install into Prism.</p>
           <p class="muted">Prism's <code>mods/</code> will be reconciled with whatever is on that branch.</p>`,
    confirmText: "Install",
    confirmKind: "primary",
  });
}

// --- jobs / logs ---
async function refreshJobs() {
  try {
    const jobs = await api("/api/jobs");
    const sel = $("#job-select");
    const cur = sel.value;
    sel.innerHTML = '<option value="">(no job)</option>';
    jobs.sort((a, b) => b.started - a.started);
    jobs.forEach(j => {
      const o = document.createElement("option");
      o.value = j.id;
      const status = j.status === "running" ? "▶" : (j.status === "done" ? "✓" : (j.status === "error" ? "✗" : "•"));
      o.textContent = `${status} ${j.name} — ${j.status}`;
      sel.appendChild(o);
    });
    if (LIVE_JOB_ID) sel.value = LIVE_JOB_ID;
    else if (cur && jobs.find(j => j.id === cur)) sel.value = cur;
    else if (jobs[0]) sel.value = jobs[0].id;
    if (sel.value) loadJobLog();
  } catch (e) { toast(e.message, "error"); }
}

function loadJobLog() {
  const id = $("#job-select").value;
  if (LIVE_EVT_SOURCE) { LIVE_EVT_SOURCE.close(); LIVE_EVT_SOURCE = null; }
  if (!id) { $("#log-panel").textContent = "No job selected."; return; }
  $("#log-panel").textContent = "";
  $("#job-status").textContent = "streaming…";
  const es = new EventSource("/api/jobs/" + id + "/stream");
  LIVE_EVT_SOURCE = es;
  es.onmessage = (ev) => {
    if (ev.data.startsWith("__END__")) {
      const [, status, rc] = ev.data.split(":");
      $("#job-status").textContent = `${status} (rc=${rc})`;
      es.close(); LIVE_EVT_SOURCE = null;
      LIVE_JOB_ID = null;
      reload();
      return;
    }
    const panel = $("#log-panel");
    panel.textContent += ev.data + "\n";
    panel.scrollTop = panel.scrollHeight;
  };
  es.onerror = () => {
    $("#job-status").textContent = "(connection lost)";
    es.close();
    LIVE_EVT_SOURCE = null;
  };
}

// --- settings ---
async function loadSettings() {
  SETTINGS = await api("/api/settings");
  const groups = [
    {
      title: "Paths & binaries",
      fields: [
        { k: "INSTANCE", label: "Prism instance (.minecraft path)",
          hint: "e.g. ~/.var/app/.../instances/MyPack/.minecraft" },
        { k: "PACKWIZ_BIN", label: "packwiz binary (override)",
          hint: SETTINGS._packwiz_resolved
            ? `Resolved: ${SETTINGS._packwiz_resolved}`
            : "Auto-detected from PATH and ~/.local/bin. Leave blank to use default." },
        { k: "JAVA_BIN", label: "java binary (override)",
          hint: SETTINGS._java_resolved
            ? `Resolved: ${SETTINGS._java_resolved}`
            : "Auto-detected from PATH. Leave blank to use default." },
      ],
    },
    {
      title: "External services",
      fields: [
        { k: "PACK_URL", label: "Production PACK_URL",
          hint: "GitHub Pages URL of pack.toml — used by GitHub→Prism" },
        { k: "CURSEFORGE_API_KEY", label: "CurseForge API key", secret: true,
          hint: "Free key from console.curseforge.com — required for CF lookups + downloads" },
      ],
    },
    {
      title: "Server (Crafty Controller)",
      fields: [
        { k: "CRAFTY_URL", label: "Crafty URL",
          hint: "e.g. https://10.10.10.1:8443" },
        { k: "CRAFTY_TOKEN", label: "Bearer token", secret: true,
          hint: "Persistent API key from Crafty's user settings" },
        { k: "CRAFTY_SERVER_ID", label: "Server UUID",
          hint: "From Crafty's server settings" },
        { k: "CRAFTY_INSECURE", label: "Skip TLS verification",
          hint: "true / false — set true for self-signed certs" },
        { k: "CRAFTY_SSH_HOST", label: "SSH host alias",
          hint: "Used for rsync deploy. e.g. an entry in ~/.ssh/config" },
        { k: "CRAFTY_REMOTE_ROOT", label: "Remote server-pack root",
          hint: "e.g. /opt/crafty/servers" },
      ],
    },
  ];

  const kv = $("#settings-kv");
  kv.innerHTML = "";
  groups.forEach(g => {
    const heading = document.createElement("div");
    heading.style.gridColumn = "1 / -1";
    heading.style.marginTop = "10px";
    heading.style.fontWeight = "600";
    heading.style.fontSize = "13px";
    heading.style.color = "var(--muted)";
    heading.style.textTransform = "uppercase";
    heading.style.letterSpacing = "0.5px";
    heading.textContent = g.title;
    kv.appendChild(heading);
    g.fields.forEach(f => {
      const lbl = document.createElement("label");
      lbl.innerHTML = escapeHtml(f.label) +
        (f.hint ? `<div class="muted" style="font-size:11px;margin-top:2px;font-weight:normal;">${escapeHtml(f.hint)}</div>` : "");
      const ip = document.createElement("input");
      ip.type = f.secret ? "password" : "text";
      ip.id = "set-" + f.k;
      if (f.secret) {
        ip.placeholder = SETTINGS[f.k + "_set"] ? "(set — leave blank to keep)" : "(not set)";
      } else {
        ip.value = SETTINGS[f.k] || "";
      }
      kv.appendChild(lbl);
      kv.appendChild(ip);
    });
  });

  // System info (read-only)
  const sys = $("#system-kv");
  sys.innerHTML = "";
  const m = (STATE && STATE.meta) || {};
  const sysRows = [
    ["Repo path", m.repo || SETTINGS._repo_path || "(unknown)",
      "To use a different repo, copy packwiz-manager.py into that directory and run it from there."],
    ["GitHub remote", m.github_slug || "(none)"],
    ["packwiz", m.packwiz || "(not found)"],
    ["java", m.java || "(not found)"],
    ["Default branch", m.default_branch || "main"],
    ["CurseForge API", m.curseforge_configured ? "configured ✓" : "no key set"],
  ];
  sysRows.forEach(([k, v, hint]) => {
    const a = document.createElement("label"); a.textContent = k;
    const b = document.createElement("div");
    b.textContent = v;
    b.style.fontFamily = "var(--mono)";
    b.style.fontSize = "12px";
    if (hint) {
      const h = document.createElement("div");
      h.className = "muted";
      h.style.fontSize = "11px";
      h.style.marginTop = "2px";
      h.style.fontFamily = "inherit";
      h.textContent = hint;
      b.appendChild(h);
    }
    sys.appendChild(a); sys.appendChild(b);
  });
}

async function saveSettings() {
  const fields = ["INSTANCE", "PACK_URL", "CURSEFORGE_API_KEY", "CRAFTY_URL", "CRAFTY_TOKEN",
                  "CRAFTY_SERVER_ID", "CRAFTY_INSECURE", "CRAFTY_SSH_HOST", "CRAFTY_REMOTE_ROOT",
                  "PACKWIZ_BIN", "JAVA_BIN"];
  const body = {};
  fields.forEach(k => {
    const el = document.getElementById("set-" + k);
    if (!el) return;
    const v = el.value;
    if (v) body[k] = v;
  });
  try {
    await api("/api/settings", { method: "POST", body: JSON.stringify(body) });
    toast("Settings saved.", "ok");
    await loadSettings();
    await reload();
  } catch (e) { toast(e.message, "error"); }
}

// --- init ---
let LAST_STATE_VERSION = -1;
async function pollStateVersion() {
  try {
    const r = await fetch("/api/state-version", { cache: "no-store" });
    if (!r.ok) return;
    const { version } = await r.json();
    if (version !== LAST_STATE_VERSION) {
      LAST_STATE_VERSION = version;
      // Skip the very first call (initial reload runs separately)
      if (LAST_STATE_VERSION > 0) await reload();
    }
  } catch (e) { /* network blip; ignore */ }
}

reload().then(() => refreshJobs()).then(pollStateVersion);
setInterval(pollStateVersion, 1500);
// Background refresh of server status (Crafty isn't covered by fs watcher)
setInterval(() => { if (!LIVE_EVT_SOURCE) reload(); }, 60000);
</script>
</body>
</html>
"""


if __name__ == "__main__":
    sys.exit(main())
