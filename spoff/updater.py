import os
import sys
import json
import shutil
import logging
import subprocess
import urllib.request
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

logger = logging.getLogger("updater")

GITHUB_REPO = "vrdq/spoff"
API_COMMITS_URL = f"https://api.github.com/repos/{GITHUB_REPO}/commits/main"

_SAFE_SUBPROCESS_ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}

def get_local_commit() -> Optional[str]:
    """Retrieves the local git commit SHA if running in a git clone or PEP 610 direct_url metadata."""
    repo_root = Path(__file__).resolve().parent.parent
    # 1. Direct git repository check
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            stdin=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=_SAFE_SUBPROCESS_ENV,
            timeout=3
        ).decode().strip()
        if out:
            return out
    except Exception:
        pass

    # 2. PEP 610 direct_url.json metadata (pipx / pip git installs)
    try:
        import importlib.metadata
        dist = importlib.metadata.distribution("spoff")
        raw = dist.read_text("direct_url.json")
        if raw:
            data = json.loads(raw)
            commit = data.get("vcs_info", {}).get("commit_id")
            if commit:
                return str(commit)
    except Exception:
        pass

    return None

def get_remote_commit_sha() -> Optional[str]:
    """Retrieves the latest commit SHA from remote GitHub repo via git ls-remote or GitHub API."""
    repo_root = Path(__file__).resolve().parent.parent

    # 1. Try git ls-remote using local git config (fast, zero rate limits)
    try:
        out = subprocess.check_output(
            ["git", "ls-remote", "origin", "refs/heads/main"],
            cwd=str(repo_root),
            stdin=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=_SAFE_SUBPROCESS_ENV,
            timeout=4
        ).decode().strip()
        if out:
            sha = out.split()[0]
            if len(sha) >= 7:
                return sha
    except Exception:
        pass

    # 2. Try git ls-remote directly against the public repo URL
    try:
        out = subprocess.check_output(
            ["git", "ls-remote", f"https://github.com/{GITHUB_REPO}.git", "refs/heads/main"],
            stdin=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=_SAFE_SUBPROCESS_ENV,
            timeout=4
        ).decode().strip()
        if out:
            sha = out.split()[0]
            if len(sha) >= 7:
                return sha
    except Exception:
        pass

    # 3. Fallback to GitHub REST API
    try:
        req = urllib.request.Request(
            API_COMMITS_URL,
            headers={
                "User-Agent": "spoff-updater",
                "Accept": "application/vnd.github.v3+json"
            }
        )
        with urllib.request.urlopen(req, timeout=4) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                sha = data.get("sha")
                if sha:
                    return str(sha)
    except Exception as e:
        logger.debug(f"API commit fetch failed: {e}")

    return None

def check_for_updates() -> Optional[Dict[str, Any]]:
    """
    Checks GitHub for the latest push to the main branch.
    Returns details if a new commit exists, otherwise None.
    """
    local_sha = get_local_commit()
    if not local_sha:
        return None

    remote_sha = get_remote_commit_sha()
    if not remote_sha or remote_sha.lower() == local_sha.lower() or remote_sha.lower().startswith(local_sha.lower()) or local_sha.lower().startswith(remote_sha.lower()):
        return None

    # Verify remote_sha is not already an ancestor of local HEAD (e.g. local is ahead of remote)
    repo_root = Path(__file__).resolve().parent.parent
    try:
        is_already_contained = subprocess.call(
            ["git", "merge-base", "--is-ancestor", remote_sha, "HEAD"],
            cwd=str(repo_root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=_SAFE_SUBPROCESS_ENV
        ) == 0
        if is_already_contained:
            return None
    except Exception:
        pass

    # Fetch commit metadata (commit message, author, date)
    message = "Latest updates from GitHub"
    author = "vrdq"
    date_str = ""
    try:
        req = urllib.request.Request(
            API_COMMITS_URL,
            headers={
                "User-Agent": "spoff-updater",
                "Accept": "application/vnd.github.v3+json"
            }
        )
        with urllib.request.urlopen(req, timeout=4) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                commit = data.get("commit", {})
                message = commit.get("message", "").split("\n")[0] or message
                author = commit.get("author", {}).get("name", "vrdq")
                date_str = commit.get("author", {}).get("date", "")
    except Exception:
        pass

    return {
        "has_update": True,
        "local_sha": local_sha[:7],
        "remote_sha": remote_sha[:7],
        "full_remote_sha": remote_sha,
        "message": message,
        "author": author,
        "date": date_str
    }

def perform_update() -> Tuple[bool, str]:
    """
    Pulls latest code from GitHub and synchronizes the running installation.
    Supports git working clones (with autostash), pipx, uv tool, and pip.
    """
    repo_root = Path(__file__).resolve().parent.parent
    python_bin = sys.executable

    # 1. Git repository update flow
    is_git = False
    try:
        is_git = subprocess.call(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(repo_root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=_SAFE_SUBPROCESS_ENV
        ) == 0
    except Exception:
        is_git = False

    if is_git:
        # Pull latest commits with autostash and rebase to preserve uncommitted local tweaks
        pull_res = subprocess.run(
            ["git", "pull", "--autostash", "--rebase", "origin", "main"],
            cwd=str(repo_root),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            env=_SAFE_SUBPROCESS_ENV
        )
        if pull_res.returncode != 0:
            # Fallback to fetch + fast-forward merge
            subprocess.run(
                ["git", "fetch", "origin", "main"],
                cwd=str(repo_root),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                env=_SAFE_SUBPROCESS_ENV
            )
            merge_res = subprocess.run(
                ["git", "merge", "--ff-only", "FETCH_HEAD"],
                cwd=str(repo_root),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                env=_SAFE_SUBPROCESS_ENV
            )
            if merge_res.returncode != 0:
                err = pull_res.stderr.strip() or merge_res.stderr.strip()
                return False, f"Git pull failed: {err}"

        # Reinstall package in virtualenv
        uv_bin = shutil.which("uv")
        installed = False
        if uv_bin:
            res = subprocess.run(
                [uv_bin, "pip", "install", "--python", python_bin, "-e", "."],
                cwd=str(repo_root),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                env=_SAFE_SUBPROCESS_ENV
            )
            installed = (res.returncode == 0)

        if not installed:
            res = subprocess.run(
                [python_bin, "-m", "pip", "install", "-e", "."],
                cwd=str(repo_root),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                env=_SAFE_SUBPROCESS_ENV
            )
            if res.returncode != 0:
                return False, f"Reinstallation failed: {res.stderr.strip()}"

        new_sha = get_local_commit() or "latest"
        return True, f"Successfully updated to commit {new_sha[:7]}."

    # 2. Non-git installs: Check pipx, uv tool, and pip
    pipx_bin = shutil.which("pipx")
    if pipx_bin:
        res = subprocess.run(
            [pipx_bin, "install", "--force", f"git+https://github.com/{GITHUB_REPO}.git"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            env=_SAFE_SUBPROCESS_ENV
        )
        if res.returncode == 0:
            return True, "Successfully updated Spoff via pipx."

    uv_bin = shutil.which("uv")
    if uv_bin:
        res = subprocess.run(
            [uv_bin, "tool", "install", "--force", f"git+https://github.com/{GITHUB_REPO}.git"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            env=_SAFE_SUBPROCESS_ENV
        )
        if res.returncode == 0:
            return True, "Successfully updated Spoff via uv."

    pip_res = subprocess.run(
        [python_bin, "-m", "pip", "install", "--upgrade", f"git+https://github.com/{GITHUB_REPO}.git"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        env=_SAFE_SUBPROCESS_ENV
    )
    if pip_res.returncode == 0:
        return True, "Successfully updated Spoff via pip."

    return False, "Could not determine package manager to execute update."

def run_cli_update():
    """Runs the update process from the CLI."""
    print("Checking for updates on github.com/vrdq/spoff...")
    info = check_for_updates()
    if not info:
        print("Spoff is already up to date on the latest GitHub commit.")
        return

    print("\nNew update found:")
    print(f"  Current commit: {info['local_sha']}")
    print(f"  Latest commit:  {info['remote_sha']} ({info['message']}) by {info['author']}")
    print("\nPulling updates...")

    ok, msg = perform_update()
    if ok:
        print(f"\n[OK] {msg}")
        print("Restart Spoff to use the updated version.")
    else:
        print(f"\n[FAILED] {msg}")
