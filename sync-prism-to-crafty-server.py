#!/usr/bin/env python3
"""Sync the LogisticalPack repo state to the Crafty-managed Minecraft server.

This is the single entry point for all server-side operations. It replaces
four earlier scripts: crafty-power.py, crafty-sync.py, crafty-deploy.py,
and test-crafty-api.py.

Subcommands:
    status                show server state (running, version, players, etc.)
    test                  connectivity + auth smoke test (lists visible servers)
    start  [--timeout N]  start the server, wait until running
    stop   [--timeout N]  stop the server, wait until stopped
    restart [--timeout N] restart and wait until running
    sync   [--apply]      build the server pack and rsync to the server
                          (dry-run by default; pack source-of-truth)
    deploy [--apply]      full pipeline: stop -> sync -> start
                          (dry-run by default; user-facing entry point)

Reads .env in this directory:
    PACK_URL            packwiz pack URL (same one apply-to-prism.py uses)
    CRAFTY_URL          e.g. https://10.10.10.1:8443
    CRAFTY_TOKEN        bearer token (persistent API key recommended)
    CRAFTY_SERVER_ID    target server UUID
    CRAFTY_INSECURE     skip TLS verification (default true; self-signed)
    CRAFTY_SSH_HOST     SSH alias for rsync (default server-direct)
    CRAFTY_REMOTE_ROOT  base dir on the host (default /opt/crafty/servers)

Reads .crafty-sync-exclude for runtime-managed paths the server creates
that we must not upload from staging or delete on the server.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import ssl
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from urllib.error import HTTPError, URLError


# --- paths / constants ------------------------------------------------------

REPO = Path(__file__).resolve().parent
CACHE_DIR = REPO / ".cache"
STAGING_DIR = CACHE_DIR / "server-pack"
BOOTSTRAP_JAR = CACHE_DIR / "packwiz-installer-bootstrap.jar"
LOCK_FILE = CACHE_DIR / "sync.lock"
EXCLUDE_FILE_NAME = ".crafty-sync-exclude"
BOOTSTRAP_URL = (
    "https://github.com/packwiz/packwiz-installer-bootstrap/"
    "releases/latest/download/packwiz-installer-bootstrap.jar"
)

# Only these top-level dirs are ever touched on the server. Anything else
# (world/, logs/, server.properties, eula.txt, ops.json, …) is invisible.
SYNCABLE_DIRS = ["mods", "config", "kubejs", "defaultconfigs", "resourcepacks"]

POLL_INTERVAL = 2.0
DEFAULT_STOP_TIMEOUT = 120.0
DEFAULT_START_TIMEOUT = 240.0


# --- env / utility ----------------------------------------------------------

def load_dotenv(path: Path) -> dict[str, str]:
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


def get_setting(env: dict[str, str], key: str, default: str | None = None) -> str | None:
    return env.get(key) or os.environ.get(key) or default


def truthy(s: str | None) -> bool:
    return (s or "").strip().lower() in {"1", "true", "yes", "on"}


def fail(msg: str) -> "NoReturn":
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


# --- lock -------------------------------------------------------------------

def acquire_lock() -> int:
    """Take an exclusive non-blocking flock on LOCK_FILE. Returns the open fd;
    keep it open for the lifetime of the operation."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    fd = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        fail(f"another sync/deploy is already running (lock held: {LOCK_FILE})")
    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode())
    return fd


# --- Crafty API client ------------------------------------------------------

class CraftyClient:
    def __init__(self, base_url: str, token: str, server_id: str, *, insecure: bool):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.server_id = server_id
        ctx = ssl.create_default_context()
        if insecure:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        self.ssl_ctx = ctx

    def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        body: dict | None = None,
        timeout: float = 10.0,
    ) -> tuple[int, dict | str]:
        url = self.base_url + path
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, context=self.ssl_ctx, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                status = resp.status
        except HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            status = e.code
        except URLError as e:
            fail(f"connection to {url} failed: {e.reason}")
        try:
            return status, json.loads(raw)
        except json.JSONDecodeError:
            return status, raw

    def list_servers(self) -> list[dict]:
        s, p = self._request("/api/v2/servers")
        if s != 200 or not isinstance(p, dict) or p.get("status") != "ok":
            fail(f"servers list failed: HTTP {s} body={p!r}")
        return p.get("data") or []

    def stats(self) -> dict:
        s, p = self._request(f"/api/v2/servers/{self.server_id}/stats")
        if s != 200 or not isinstance(p, dict) or p.get("status") != "ok":
            fail(f"stats failed: HTTP {s} body={p!r}")
        return p.get("data") or {}

    def is_running(self) -> bool:
        return bool(self.stats().get("running"))

    def action(self, action: str) -> None:
        s, p = self._request(f"/api/v2/servers/{self.server_id}/action/{action}", method="POST")
        if s != 200 or not isinstance(p, dict) or p.get("status") != "ok":
            fail(f"action {action} failed: HTTP {s} body={p!r}")

    def wait_until(self, want_running: bool, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        last: bool | None = None
        while time.monotonic() < deadline:
            running = self.is_running()
            if running != last:
                print(f"  …state: {'RUNNING' if running else 'stopped'}")
                last = running
            if running == want_running:
                return True
            time.sleep(POLL_INTERVAL)
        return False


def make_client(env: dict[str, str]) -> CraftyClient:
    base_url = get_setting(env, "CRAFTY_URL")
    token = get_setting(env, "CRAFTY_TOKEN")
    server_id = get_setting(env, "CRAFTY_SERVER_ID")
    insecure = truthy(get_setting(env, "CRAFTY_INSECURE", "true"))
    if not base_url:
        fail("CRAFTY_URL not set in .env")
    if not token:
        fail("CRAFTY_TOKEN not set in .env")
    if not server_id:
        fail("CRAFTY_SERVER_ID not set in .env")
    if insecure:
        print("warning: TLS verification disabled (CRAFTY_INSECURE=true)", file=sys.stderr)
    return CraftyClient(base_url, token, server_id, insecure=insecure)


# --- packwiz staging --------------------------------------------------------

def ensure_bootstrap() -> None:
    if BOOTSTRAP_JAR.is_file() and BOOTSTRAP_JAR.stat().st_size > 0:
        return
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"→ downloading bootstrap jar from {BOOTSTRAP_URL}")
    tmp = BOOTSTRAP_JAR.with_suffix(".jar.partial")
    try:
        with urllib.request.urlopen(BOOTSTRAP_URL, timeout=60) as resp:
            tmp.write_bytes(resp.read())
        tmp.rename(BOOTSTRAP_JAR)
        print(f"  saved {BOOTSTRAP_JAR.stat().st_size:,} bytes to {BOOTSTRAP_JAR}")
    except Exception as e:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        fail(f"failed to download bootstrap jar: {e}")


def run_packwiz_installer(pack_url: str) -> None:
    if shutil.which("java") is None:
        fail("'java' not found in PATH — install a JRE")
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    cmd = ["java", "-jar", str(BOOTSTRAP_JAR), "-s", "server", "-g", pack_url]
    print(f"→ packwiz-installer-bootstrap -s server")
    print(f"  pack: {pack_url}")
    print(f"  cwd:  {STAGING_DIR}")
    res = subprocess.run(cmd, cwd=STAGING_DIR)
    if res.returncode != 0:
        fail(f"packwiz-installer-bootstrap exited {res.returncode}")


# --- rsync ------------------------------------------------------------------

def load_excludes(repo: Path) -> dict[str, list[str]]:
    """Read .crafty-sync-exclude and bucket per syncable dir."""
    f = repo / EXCLUDE_FILE_NAME
    if not f.is_file():
        return {}
    out: dict[str, list[str]] = {}
    for lineno, raw in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        head, sep, rest = line.partition("/")
        if not sep or not rest:
            print(f"warning: {f.name}:{lineno} has no syncable-dir prefix, ignored: {line!r}",
                  file=sys.stderr)
            continue
        if head not in SYNCABLE_DIRS:
            print(f"warning: {f.name}:{lineno} top-level dir {head!r} isn't syncable, ignored",
                  file=sys.stderr)
            continue
        out.setdefault(head, []).append(rest)
    return out


def parse_itemize(stdout: str) -> tuple[list[str], int]:
    """Returns (all itemize lines, meaningful change count).
    Cosmetic mtime/perm-only updates don't count toward meaningful changes."""
    all_lines: list[str] = []
    meaningful = 0
    for line in stdout.splitlines():
        if len(line) < 12:
            continue
        c0, c1 = line[0], line[1]
        if c0 not in "<>ch*. " or c1 not in "fdLDS":
            continue
        all_lines.append(line)
        flags = line[:11]
        # Directory: only count substantive ops (create/delete/content change).
        if c1 == "d":
            if any(c not in (".", "t", "p", " ") for c in flags[2:]):
                meaningful += 1
            continue
        # File where only mtime differs: rsync may "transfer" but no bytes
        # actually move (size+content match). Cosmetic.
        if c0 in (".", "<", ">"):
            if all(c in (".", "t", " ") for c in flags[2:]):
                continue
        meaningful += 1
    return all_lines, meaningful


def rsync_dir(
    local_dir: Path,
    ssh_host: str,
    remote_dir: str,
    *,
    apply: bool,
    excludes: list[str],
) -> tuple[list[str], int]:
    cmd = [
        "rsync",
        "-a",
        "--no-group",  # let setgid on the parent dir control group ownership
        "--delete",
        "--itemize-changes",
        "--human-readable",
        "--exclude=.*",
    ]
    for pat in excludes:
        cmd.append(f"--exclude={pat}")
    if not apply:
        cmd.append("--dry-run")
    cmd.append(f"{local_dir}/")
    cmd.append(f"{ssh_host}:{remote_dir}/")

    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode not in (0, 24):  # 24 = source files vanished, harmless
        sys.stdout.write(res.stdout)
        sys.stderr.write(res.stderr)
        fail(f"rsync failed (exit {res.returncode}): {local_dir.name}/")
    return parse_itemize(res.stdout)


def do_rsync(
    remote_path: str,
    ssh_host: str,
    *,
    apply: bool,
    excludes_by_dir: dict[str, list[str]],
) -> dict[str, int]:
    if shutil.which("rsync") is None:
        fail("'rsync' not found in PATH")
    counts: dict[str, int] = {}
    for d in SYNCABLE_DIRS:
        local = STAGING_DIR / d
        if not local.is_dir():
            continue
        remote = f"{remote_path.rstrip('/')}/{d}"
        prefix = "(dry-run) " if not apply else ""
        excludes = excludes_by_dir.get(d, [])
        ex_note = f"  [excludes: {', '.join(excludes)}]" if excludes else ""
        print(f"→ {prefix}rsync {d}/  →  {ssh_host}:{remote}/{ex_note}")
        items, count = rsync_dir(local, ssh_host, remote, apply=apply, excludes=excludes)
        counts[d] = count
        if not items:
            print("  (no changes)")
        else:
            for line in items[:50]:
                print(f"  {line}")
            if len(items) > 50:
                print(f"  ... and {len(items) - 50} more")
            if count == 0 and items:
                print("  (all entries above are cosmetic mtime/perm updates — no content transferred)")
    return counts


# --- subcommand handlers ----------------------------------------------------

def fmt_bytes(n) -> str:
    try:
        n = float(n)
    except (TypeError, ValueError):
        return str(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024.0:
            return f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} PiB"


def cmd_status(env: dict[str, str], _args) -> int:
    client = make_client(env)
    s = client.stats()
    print(f"server:     {client.server_id}")
    print(f"running:    {'RUNNING' if s.get('running') else 'stopped'}")
    print(f"version:    {s.get('version', '?')}")
    print(f"players:    {s.get('online', '?')} / {s.get('max', '?')}")
    print(f"cpu:        {s.get('cpu', '?')}%")
    print(f"mem:        {fmt_bytes(s.get('mem'))}")
    print(f"world_size: {s.get('world_size', '?')}")
    return 0


def cmd_test(env: dict[str, str], _args) -> int:
    """Smoke test: list visible servers, then stats for the configured one."""
    base_url = get_setting(env, "CRAFTY_URL")
    token = get_setting(env, "CRAFTY_TOKEN")
    server_id = get_setting(env, "CRAFTY_SERVER_ID")
    insecure = truthy(get_setting(env, "CRAFTY_INSECURE", "true"))
    if not base_url or not token:
        fail("CRAFTY_URL and CRAFTY_TOKEN must be set in .env")
    if insecure:
        print("warning: TLS verification disabled (CRAFTY_INSECURE=true)", file=sys.stderr)

    print(f"target:   {base_url}")
    print(f"token:    {token[:12]}...{token[-6:]}  (len={len(token)})")
    print()

    # No server_id needed for /servers; build a temporary client with a
    # placeholder so the helper still works.
    client = CraftyClient(base_url, token, server_id or "00000000-0000-0000-0000-000000000000", insecure=insecure)
    print("→ GET /api/v2/servers")
    servers = client.list_servers()
    if not servers:
        fail("token has no visible servers — check API key permissions in Crafty")
    print(f"  found {len(servers)} server(s):")
    for s in servers:
        sid = s.get("server_id") or s.get("server_uuid") or "?"
        name = s.get("server_name", "?")
        type_ = s.get("type", "?")
        marker = "  ← CRAFTY_SERVER_ID" if str(sid) == str(server_id) else ""
        print(f"    - {sid}  {name!r}  type={type_}{marker}")
    print()

    if server_id:
        print(f"→ GET /api/v2/servers/{server_id}/stats")
        s = client.stats()
        print(f"  running:    {'RUNNING' if s.get('running') else 'stopped'}")
        print(f"  version:    {s.get('version', '?')}")
        print(f"  players:    {s.get('online', '?')} / {s.get('max', '?')}")
    print()
    print("ok — connection and auth working.")
    return 0


def cmd_start(env: dict[str, str], args) -> int:
    client = make_client(env)
    if client.is_running():
        print("server is already running.")
        return 0
    print(f"→ starting server {client.server_id}…")
    client.action("start_server")
    if args.no_wait:
        print("queued; not waiting.")
        return 0
    if client.wait_until(want_running=True, timeout=args.timeout):
        print("running.")
        return 0
    print(f"error: server not running after {args.timeout:.0f}s", file=sys.stderr)
    return 2


def cmd_stop(env: dict[str, str], args) -> int:
    client = make_client(env)
    if not client.is_running():
        print("server is already stopped.")
        return 0
    print(f"→ stopping server {client.server_id}…")
    client.action("stop_server")
    if args.no_wait:
        print("queued; not waiting.")
        return 0
    if client.wait_until(want_running=False, timeout=args.timeout):
        print("stopped.")
        return 0
    print(f"error: server still running after {args.timeout:.0f}s", file=sys.stderr)
    return 2


def cmd_restart(env: dict[str, str], args) -> int:
    client = make_client(env)
    print(f"→ restarting server {client.server_id}…")
    was_running = client.is_running()
    client.action("restart_server")
    if was_running:
        if not client.wait_until(want_running=False, timeout=args.timeout / 2):
            print("warning: didn't observe stopped state during restart", file=sys.stderr)
    if client.wait_until(want_running=True, timeout=args.timeout):
        print("running.")
        return 0
    print(f"error: server not running after {args.timeout:.0f}s", file=sys.stderr)
    return 2


def do_sync(env: dict[str, str], *, apply: bool, skip_build: bool) -> int:
    """Build the staging dir + rsync. Caller is responsible for the lock."""
    pack_url = get_setting(env, "PACK_URL")
    server_id = get_setting(env, "CRAFTY_SERVER_ID")
    ssh_host = get_setting(env, "CRAFTY_SSH_HOST", "server-direct")
    remote_root = get_setting(env, "CRAFTY_REMOTE_ROOT", "/opt/crafty/servers")

    if not pack_url:
        fail("PACK_URL not set in .env")
    if not server_id:
        fail("CRAFTY_SERVER_ID not set in .env")
    remote_path = f"{remote_root.rstrip('/')}/{server_id}"

    if not skip_build:
        ensure_bootstrap()
        run_packwiz_installer(pack_url)
    else:
        if not STAGING_DIR.is_dir():
            fail("--skip-build given but staging dir doesn't exist; remove the flag")

    excludes_by_dir = load_excludes(REPO)

    print()
    mode = "APPLY" if apply else "DRY-RUN"
    print(f"=== {mode} ===")
    print(f"local:  {STAGING_DIR}")
    print(f"remote: {ssh_host}:{remote_path}")
    if excludes_by_dir:
        ex_count = sum(len(v) for v in excludes_by_dir.values())
        print(f"loaded {ex_count} exclude pattern(s) from {EXCLUDE_FILE_NAME}")
    print()

    counts = do_rsync(remote_path, ssh_host, apply=apply, excludes_by_dir=excludes_by_dir)

    print()
    total = sum(counts.values())
    if total == 0:
        print("no changes — server is already in sync.")
    else:
        print("=== summary ===")
        for d, n in counts.items():
            if n > 0:
                print(f"  {d:<16} {n} change(s)")
        print()
        if not apply:
            print("dry-run; pass --apply to push.")
        else:
            print(f"applied {total} change(s).")
    return 0


def cmd_sync(env: dict[str, str], args) -> int:
    lock_fd = acquire_lock()
    try:
        return do_sync(env, apply=args.apply, skip_build=args.skip_build)
    finally:
        os.close(lock_fd)


def cmd_deploy(env: dict[str, str], args) -> int:
    """Full pipeline: stop -> sync -> start. Always restarts the server in
    finally so a sync error can't leave it down (unless --leave-stopped)."""
    client = make_client(env)

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"=== deploy ({mode}) ===")
    print(f"server: {client.server_id}")
    print()

    if not args.apply:
        running = client.is_running()
        print(f"current state: {'RUNNING' if running else 'stopped'}")
        if running:
            print("→ would: stop server, run sync, start server")
        else:
            print("→ would: skip stop (already stopped), run sync, start server")
        print()
        do_sync(env, apply=False, skip_build=False)
        print()
        print("dry-run; pass --apply to execute.")
        return 0

    lock_fd = acquire_lock()
    sync_failed = False
    try:
        was_running = client.is_running()
        print(f"initial state: {'RUNNING' if was_running else 'stopped'}")

        # stop
        if was_running:
            print("→ stopping server")
            client.action("stop_server")
            if not client.wait_until(want_running=False, timeout=args.stop_timeout):
                fail(f"server still running after {args.stop_timeout:.0f}s — aborting before sync")
            print("  stopped.")
        else:
            print("→ server already stopped")

        # sync
        try:
            do_sync(env, apply=True, skip_build=False)
        except SystemExit as e:
            sync_failed = True
            print(f"warning: sync raised exit code {e.code} — will still restart server", file=sys.stderr)
        except Exception as e:
            sync_failed = True
            print(f"warning: sync raised {e!r} — will still restart server", file=sys.stderr)
    finally:
        # start (always — unless --leave-stopped)
        if args.leave_stopped:
            print("→ --leave-stopped given; not starting server")
        else:
            try:
                if not client.is_running():
                    print("→ starting server")
                    client.action("start_server")
                    if not client.wait_until(want_running=True, timeout=args.start_timeout):
                        sync_failed = True
                        print(f"error: server not running after {args.start_timeout:.0f}s",
                              file=sys.stderr)
                    else:
                        print("  running.")
                else:
                    print("→ server already running (unexpected, but ok)")
            except SystemExit:
                os.close(lock_fd)
                raise
        os.close(lock_fd)

    if sync_failed:
        print()
        print("DEPLOY HAD ERRORS — see above.", file=sys.stderr)
        return 2
    print()
    print("deploy complete.")
    return 0


# --- main -------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="print current server state")
    sub.add_parser("test", help="connectivity + auth smoke test")

    p_start = sub.add_parser("start", help="start the server and wait")
    p_start.add_argument("--timeout", type=float, default=DEFAULT_START_TIMEOUT)
    p_start.add_argument("--no-wait", action="store_true")

    p_stop = sub.add_parser("stop", help="stop the server and wait")
    p_stop.add_argument("--timeout", type=float, default=DEFAULT_STOP_TIMEOUT)
    p_stop.add_argument("--no-wait", action="store_true")

    p_restart = sub.add_parser("restart", help="restart and wait")
    p_restart.add_argument("--timeout", type=float, default=DEFAULT_STOP_TIMEOUT + DEFAULT_START_TIMEOUT)

    p_sync = sub.add_parser("sync", help="rsync the pack onto the server (no stop/start)")
    p_sync.add_argument("--apply", action="store_true",
                        help="actually push (default: dry-run)")
    p_sync.add_argument("--skip-build", action="store_true",
                        help="skip packwiz-installer-bootstrap; use existing staging dir")

    p_deploy = sub.add_parser("deploy", help="stop → sync → start (full pipeline)")
    p_deploy.add_argument("--apply", action="store_true",
                          help="actually execute (default: dry-run)")
    p_deploy.add_argument("--stop-timeout", type=float, default=DEFAULT_STOP_TIMEOUT)
    p_deploy.add_argument("--start-timeout", type=float, default=DEFAULT_START_TIMEOUT)
    p_deploy.add_argument("--leave-stopped", action="store_true",
                          help="don't restart after sync (e.g. for offline maintenance)")

    args = parser.parse_args()
    env = load_dotenv(REPO / ".env")

    handlers = {
        "status": cmd_status,
        "test": cmd_test,
        "start": cmd_start,
        "stop": cmd_stop,
        "restart": cmd_restart,
        "sync": cmd_sync,
        "deploy": cmd_deploy,
    }
    return handlers[args.cmd](env, args)


if __name__ == "__main__":
    sys.exit(main())
