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

def get_local_commit() -> Optional[str]:
    """Retrieves the local git commit SHA if running in a git clone."""
    try:
        repo_root = Path(__file__).resolve().parent.parent
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            stderr=subprocess.DEVNULL
        ).decode().strip()
        return out
    except Exception:
        return None

def check_for_updates() -> Optional[Dict[str, Any]]:
    """
    Checks GitHub for the latest push to the main branch.
    Returns details if a new commit exists, otherwise None.
    """
    local_sha = get_local_commit()
    if not local_sha:
        return None

    try:
        req = urllib.request.Request(
            API_COMMITS_URL,
            headers={
                "User-Agent": "spoff-updater",
                "Accept": "application/vnd.github.v3+json"
            }
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status != 200:
                return None
            data = json.loads(resp.read().decode("utf-8"))

        remote_sha = data.get("sha", "")
        if not remote_sha or remote_sha.lower() == local_sha.lower():
            return None

        repo_root = Path(__file__).resolve().parent.parent
        is_already_contained = subprocess.call(
            ["git", "merge-base", "--is-ancestor", remote_sha, "HEAD"],
            cwd=str(repo_root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        ) == 0
        if is_already_contained:
            return None

        commit = data.get("commit", {})
        message = commit.get("message", "").split("\n")[0]
        author = commit.get("author", {}).get("name", "vrdq")
        date_str = commit.get("author", {}).get("date", "")

        return {
            "has_update": True,
            "local_sha": local_sha[:7],
            "remote_sha": remote_sha[:7],
            "full_remote_sha": remote_sha,
            "message": message,
            "author": author,
            "date": date_str
        }
    except Exception as e:
        logger.debug(f"Update check failed: {e}")
        return None

def perform_update() -> Tuple[bool, str]:
    """
    Pulls the latest code from GitHub and reinstalls editable package.
    """
    repo_root = Path(__file__).resolve().parent.parent
    try:
        # Check if git working directory is clean or inside work tree
        is_git = subprocess.call(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(repo_root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        ) == 0

        if not is_git:
            return False, "Not running from a git repository. Update via your package manager."

        # Fetch and pull
        pull_res = subprocess.run(
            ["git", "pull", "--ff-only", "origin", "main"],
            cwd=str(repo_root),
            capture_output=True,
            text=True
        )
        if pull_res.returncode != 0:
            return False, f"Git pull failed: {pull_res.stderr.strip()}"

        # Reinstall package in virtualenv if uv is present, else pip
        python_bin = sys.executable
        uv_bin = shutil.which("uv")
        if uv_bin:
            install_cmd = [uv_bin, "pip", "install", "-e", "."]
        else:
            install_cmd = [python_bin, "-m", "pip", "install", "-e", "."]
        subprocess.run(
            install_cmd,
            cwd=str(repo_root),
            capture_output=True,
            text=True
        )

        new_sha = get_local_commit() or "latest"
        return True, f"Successfully updated to commit {new_sha[:7]}."
    except Exception as e:
        logger.error(f"Error executing update: {e}")
        return False, str(e)

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
