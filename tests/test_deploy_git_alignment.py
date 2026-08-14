"""Regression tests for scripts/deploy.sh git alignment.

Reproduces the old rsync-then-checkout collision in a temporary repository,
then proves the fixed script deploys old HEAD → new commit without manual
recovery, preserves runtime files, and aborts on unexpected tracked drift
before docker build/restart.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

DEPLOY_SH = Path(__file__).resolve().parents[1] / "scripts" / "deploy.sh"
GIT_ENV = {
    "GIT_AUTHOR_NAME": "Deploy Test",
    "GIT_AUTHOR_EMAIL": "deploy-test@example.com",
    "GIT_COMMITTER_NAME": "Deploy Test",
    "GIT_COMMITTER_EMAIL": "deploy-test@example.com",
}


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, **GIT_ENV}
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=check,
    )


def _write_executable(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _build_repos(tmp_path: Path) -> tuple[Path, Path, Path, str, str]:
    """Create origin + local (new SHA) + remote checkout (old SHA) with runtime files."""
    origin = tmp_path / "origin.git"
    local = tmp_path / "local"
    remote = tmp_path / "remote"
    _git(tmp_path, "init", "--bare", str(origin))

    work = tmp_path / "seed"
    _git(tmp_path, "clone", str(origin), str(work))
    _git(work, "checkout", "-b", "main")
    (work / "app.txt").write_text("old\n", encoding="utf-8")
    (work / ".gitignore").write_text(
        ".env\nlogs/\ndata/\nresults/\n.deploy-sha\n.staging/\n.ops-backups/\n.venv/\n",
        encoding="utf-8",
    )
    _git(work, "add", "app.txt", ".gitignore")
    _git(work, "commit", "-m", "old commit")
    old_sha = _git(work, "rev-parse", "HEAD").stdout.strip()
    _git(work, "push", "-u", "origin", "main")

    (work / "app.txt").write_text("new\n", encoding="utf-8")
    (work / "added.txt").write_text("added\n", encoding="utf-8")
    _git(work, "add", "app.txt", "added.txt")
    _git(work, "commit", "-m", "new commit")
    new_sha = _git(work, "rev-parse", "HEAD").stdout.strip()
    _git(work, "push", "origin", "main")

    _git(tmp_path, "clone", str(origin), str(local))
    _git(local, "checkout", "main")
    assert _git(local, "rev-parse", "HEAD").stdout.strip() == new_sha

    _git(tmp_path, "clone", str(origin), str(remote))
    _git(remote, "checkout", old_sha)
    (remote / ".env").write_text("SECRET=keep-me\n", encoding="utf-8")
    (remote / "logs").mkdir()
    (remote / "logs" / "scan.log").write_text("prior-scan\n", encoding="utf-8")
    (remote / "data").mkdir()
    (remote / "data" / "store.sqlite").write_text("db\n", encoding="utf-8")
    (remote / "results").mkdir()
    (remote / "results" / "out.txt").write_text("kept-result\n", encoding="utf-8")
    (remote / ".ops-backups").mkdir()
    (remote / ".ops-backups" / "note").write_text("backup\n", encoding="utf-8")
    (remote / ".deploy-sha").write_text(old_sha + "\n", encoding="utf-8")
    return local, remote, tmp_path / "bin", old_sha, new_sha


def _install_fakes(bin_dir: Path, remote: Path, docker_log: Path) -> None:
    _write_executable(
        bin_dir / "ssh",
        f"""#!/usr/bin/env bash
set -euo pipefail
while [[ $# -gt 0 ]]; do
  case "$1" in
    -i|-o|-l|-p|-F|-W) shift 2 ;;
    -*) shift ;;
    *) break ;;
  esac
done
shift
export PATH="{bin_dir}:$PATH"
if [[ $# -eq 0 ]]; then
  exec /bin/bash
fi
exec /bin/bash -c "$*"
""",
    )
    _write_executable(
        bin_dir / "docker",
        f"""#!/usr/bin/env bash
echo "docker $*" >> "{docker_log}"
if [[ "$*" == *Health.Status* ]]; then
  echo healthy
  exit 0
fi
if [[ "$*" == *State.Running* ]]; then
  echo true
  exit 0
fi
exit 0
""",
    )
    # deploy.sh calls docker on the remote via ssh; keep the same stub on PATH.
    _ = remote


def _run_deploy(
    local: Path, remote: Path, bin_dir: Path, ref: str
) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        **GIT_ENV,
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
        "SSH_HOST": "fake-host",
        "REMOTE_PATH": str(remote),
        "SERVICE_NAME": "manual-trading-agent",
    }
    return subprocess.run(
        ["bash", str(DEPLOY_SH), ref],
        cwd=local,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )


def test_old_rsync_then_checkout_collides(tmp_path: Path) -> None:
    """Historical failure: rsync onto old HEAD, then checkout aborts."""
    local, remote, _bin, old_sha, new_sha = _build_repos(tmp_path)
    staging = remote / ".staging"
    staging.mkdir()
    archive = subprocess.run(
        ["git", "archive", new_sha],
        cwd=local,
        check=True,
        stdout=subprocess.PIPE,
    ).stdout
    subprocess.run(["tar", "-xf", "-", "-C", str(staging)], input=archive, check=True)
    subprocess.run(
        [
            "rsync",
            "-a",
            "--delete",
            "--exclude=.git",
            "--exclude=.git/",
            "--exclude=.env",
            "--exclude=logs",
            "--exclude=data",
            "--exclude=results",
            "--exclude=.staging",
            "--exclude=.staging/",
            f"{staging}/",
            f"{remote}/",
        ],
        check=True,
    )
    checkout = _git(remote, "checkout", "-B", "main", new_sha, check=False)
    assert checkout.returncode != 0
    assert _git(remote, "rev-parse", "HEAD").stdout.strip() == old_sha
    combined = checkout.stderr + checkout.stdout
    assert "would be overwritten" in combined or "untracked working tree files" in combined


def test_clean_old_commit_deploys_to_newer_commit(tmp_path: Path) -> None:
    local, remote, bin_dir, old_sha, new_sha = _build_repos(tmp_path)
    docker_log = tmp_path / "docker.log"
    _install_fakes(bin_dir, remote, docker_log)

    result = _run_deploy(local, remote, bin_dir, new_sha)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _git(remote, "rev-parse", "HEAD").stdout.strip() == new_sha
    assert (remote / ".deploy-sha").read_text(encoding="utf-8").strip() == new_sha
    assert (remote / "app.txt").read_text(encoding="utf-8") == "new\n"
    assert (remote / "added.txt").read_text(encoding="utf-8") == "added\n"
    assert "docker compose build" in docker_log.read_text(encoding="utf-8")
    assert "docker compose up" in docker_log.read_text(encoding="utf-8")
    assert not (remote / ".staging").exists()
    assert old_sha != new_sha


def test_runtime_files_survive_unchanged(tmp_path: Path) -> None:
    local, remote, bin_dir, _old_sha, new_sha = _build_repos(tmp_path)
    docker_log = tmp_path / "docker.log"
    _install_fakes(bin_dir, remote, docker_log)

    result = _run_deploy(local, remote, bin_dir, new_sha)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (remote / ".env").read_text(encoding="utf-8") == "SECRET=keep-me\n"
    assert (remote / "logs" / "scan.log").read_text(encoding="utf-8") == "prior-scan\n"
    assert (remote / "data" / "store.sqlite").read_text(encoding="utf-8") == "db\n"
    assert (remote / "results" / "out.txt").read_text(encoding="utf-8") == "kept-result\n"
    assert (remote / ".ops-backups" / "note").read_text(encoding="utf-8") == "backup\n"


def test_unexpected_tracked_drift_fails_before_build(tmp_path: Path) -> None:
    local, remote, bin_dir, old_sha, new_sha = _build_repos(tmp_path)
    docker_log = tmp_path / "docker.log"
    _install_fakes(bin_dir, remote, docker_log)
    (remote / "app.txt").write_text("operator-edit\n", encoding="utf-8")

    result = _run_deploy(local, remote, bin_dir, new_sha)
    assert result.returncode != 0
    assert "unexpected tracked drift" in result.stderr
    assert _git(remote, "rev-parse", "HEAD").stdout.strip() == old_sha
    assert (remote / "app.txt").read_text(encoding="utf-8") == "operator-edit\n"
    assert (remote / ".env").read_text(encoding="utf-8") == "SECRET=keep-me\n"
    assert (remote / ".deploy-sha").read_text(encoding="utf-8").strip() == old_sha
    assert not docker_log.exists() or docker_log.read_text(encoding="utf-8") == ""
    assert not (remote / ".staging").exists()
    assert not (remote / "added.txt").exists()


def test_head_and_deploy_sha_match_after_success(tmp_path: Path) -> None:
    local, remote, bin_dir, _old_sha, new_sha = _build_repos(tmp_path)
    docker_log = tmp_path / "docker.log"
    _install_fakes(bin_dir, remote, docker_log)

    result = _run_deploy(local, remote, bin_dir, new_sha)
    assert result.returncode == 0, result.stdout + result.stderr
    head = _git(remote, "rev-parse", "HEAD").stdout.strip()
    deployed = (remote / ".deploy-sha").read_text(encoding="utf-8").strip()
    assert head == new_sha
    assert deployed == new_sha
    assert head == deployed
