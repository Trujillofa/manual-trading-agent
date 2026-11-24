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


def _install_fakes(
    bin_dir: Path,
    remote: Path,
    docker_log: Path,
    *,
    revision: str = "deployed-sha",
    free_gb: int = 100,
) -> None:
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
        bin_dir / "df",
        f"""#!/usr/bin/env bash
# Report a fixed amount of free space so the disk-headroom guard is deterministic
# and does not depend on the machine running the tests.
echo "Filesystem 1B-blocks Used Available Capacity Mounted on"
echo "/dev/fake 107374182400 0 {free_gb * 1073741824} 0% /"
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
if [[ "$*" == *opencontainers.image.revision* ]]; then
  # deploy.sh has already checked out the target SHA in this cwd, so mirroring
  # HEAD emulates a correctly labelled image. A fixed string emulates staleness.
  if [[ "{revision}" == "from-git" ]]; then
    git rev-parse HEAD
  else
    echo "{revision}"
  fi
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
    _install_fakes(bin_dir, remote, docker_log, revision="from-git")

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
    _install_fakes(bin_dir, remote, docker_log, revision="from-git")

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
    _install_fakes(bin_dir, remote, docker_log, revision="from-git")
    (remote / "app.txt").write_text("operator-edit\n", encoding="utf-8")

    result = _run_deploy(local, remote, bin_dir, new_sha)
    assert result.returncode != 0
    assert "unexpected tracked drift" in result.stderr
    assert _git(remote, "rev-parse", "HEAD").stdout.strip() == old_sha
    assert (remote / "app.txt").read_text(encoding="utf-8") == "operator-edit\n"
    assert (remote / ".env").read_text(encoding="utf-8") == "SECRET=keep-me\n"
    assert (remote / ".deploy-sha").read_text(encoding="utf-8").strip() == old_sha
    # The disk probe reads `docker info`; nothing may build, start or stop.
    if docker_log.exists():
        calls = docker_log.read_text(encoding="utf-8")
        for mutating in ("compose build", "compose up", "compose down"):
            assert mutating not in calls, calls
    assert not (remote / ".staging").exists()
    assert not (remote / "added.txt").exists()


def test_head_and_deploy_sha_match_after_success(tmp_path: Path) -> None:
    local, remote, bin_dir, _old_sha, new_sha = _build_repos(tmp_path)
    docker_log = tmp_path / "docker.log"
    _install_fakes(bin_dir, remote, docker_log, revision="from-git")

    result = _run_deploy(local, remote, bin_dir, new_sha)
    assert result.returncode == 0, result.stdout + result.stderr
    head = _git(remote, "rev-parse", "HEAD").stdout.strip()
    deployed = (remote / ".deploy-sha").read_text(encoding="utf-8").strip()
    assert head == new_sha
    assert deployed == new_sha
    assert head == deployed


def test_rebuilds_every_service_built_from_this_repo(tmp_path: Path) -> None:
    """The sidecar is built from this repo, so it must be rebuilt and health-checked.

    PR #75 merged a 447-line change to deploy/etr-renderer/app.py, yet deploy.sh
    built only SERVICE_NAME: the renderer kept running a pre-#75 image and its
    wedged-browser deadline and degrading /health never reached production.
    """
    local, remote, bin_dir, _old_sha, new_sha = _build_repos(tmp_path)
    docker_log = tmp_path / "docker.log"
    _install_fakes(bin_dir, remote, docker_log, revision="from-git")

    result = _run_deploy(local, remote, bin_dir, new_sha)
    assert result.returncode == 0, result.stdout + result.stderr

    calls = docker_log.read_text(encoding="utf-8").splitlines()
    build = [c for c in calls if "compose build" in c]
    up = [c for c in calls if "compose up" in c]
    assert len(build) == 1 and len(up) == 1, calls
    for service in ("manual-trading-agent", "etr-renderer"):
        assert service in build[0].split(), build[0]
        assert service in up[0].split(), up[0]
        assert any(
            "Health.Status" in c and c.split()[-1] == service for c in calls
        ), f"{service} was not health-checked: {calls}"


def _compose_builds() -> dict[str, tuple[str, str]]:
    """Map each service built from this repo to (dockerfile path, compose block)."""
    import re

    root = DEPLOY_SH.parents[1]
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    blocks: dict[str, list[str]] = {}
    current: str | None = None
    for line in compose.splitlines():
        top = re.match(r"^  ([a-z0-9][a-z0-9_-]*):\s*$", line)
        if top:
            current = top.group(1)
            blocks[current] = []
            continue
        if current:
            blocks[current].append(line)

    builds: dict[str, tuple[str, str]] = {}
    for service, lines in blocks.items():
        try:
            idx = lines.index("    build:")
        except ValueError:
            continue
        context = "."
        dockerfile = "Dockerfile"
        for line in lines[idx + 1 :]:
            if line and not line.startswith("      "):
                break
            m = re.match(r"^\s+context:\s*(\S+)\s*$", line)
            if m:
                context = m.group(1)
                continue
            m = re.match(r"^\s+dockerfile:\s*(\S+)\s*$", line)
            if m:
                dockerfile = m.group(1)
        path = (root / context.lstrip("./") / dockerfile).resolve()
        assert path.exists(), f"{service}: compose points at missing {path}"
        builds[service] = (str(path), "\n".join(lines))
    return builds


def test_every_built_image_reports_the_deployed_commit() -> None:
    """Each repo-built image must carry the revision label deploy.sh verifies.

    The operator guide requires a deployed image's revision to match the target
    SHA. etr-renderer had no ARG/LABEL and no build args, so it reported nothing
    and a stale sidecar was indistinguishable from a fresh one — exactly how
    PR #75's renderer changes went unshipped.
    """
    import re

    builds = _compose_builds()
    assert {"manual-trading-agent", "etr-renderer"} <= set(builds), builds

    for service, (path_str, block) in sorted(builds.items()):
        text = Path(path_str).read_text(encoding="utf-8")
        assert "org.opencontainers.image.revision=${GIT_SHA}" in text, (
            f"{service}: {path_str} does not label the image revision"
        )
        assert re.search(r"^ARG GIT_SHA", text, re.MULTILINE), (
            f"{service}: {path_str} has no GIT_SHA build arg for the label"
        )
        assert "GIT_SHA:" in block, f"{service} build block passes no GIT_SHA arg"


def test_stale_image_revision_aborts_after_health(tmp_path: Path) -> None:
    """A service whose image reports the wrong commit fails the deploy.

    This is the check that would have flagged the etr-renderer still running its
    pre-#75 image while health said healthy.
    """
    local, remote, bin_dir, _old_sha, new_sha = _build_repos(tmp_path)
    docker_log = tmp_path / "docker.log"
    _install_fakes(bin_dir, remote, docker_log, revision="stale-sha")

    result = _run_deploy(local, remote, bin_dir, new_sha)

    assert result.returncode != 0
    assert "runs revision 'stale-sha'" in result.stderr, result.stderr
    assert (remote / ".deploy-sha").read_text(encoding="utf-8").strip() == new_sha


def test_low_disk_aborts_before_touching_the_remote(tmp_path: Path) -> None:
    """A full docker filesystem must stop the deploy before anything changes.

    On 2026-11-24 a deploy died at renderer layer export with the host at 100%,
    after the containers had already been recreated. The guard makes that a
    pre-flight failure with the remediation in the message.
    """
    local, remote, bin_dir, old_sha, new_sha = _build_repos(tmp_path)
    docker_log = tmp_path / "docker.log"
    _install_fakes(bin_dir, remote, docker_log, revision="from-git", free_gb=2)

    result = _run_deploy(local, remote, bin_dir, new_sha)

    assert result.returncode != 0
    assert "2 GB free" in result.stderr, result.stderr
    assert "docker builder prune" in result.stderr
    assert _git(remote, "rev-parse", "HEAD").stdout.strip() == old_sha
    assert (remote / ".deploy-sha").read_text(encoding="utf-8").strip() == old_sha
    assert not (remote / "added.txt").exists()
    if docker_log.exists():
        assert "compose build" not in docker_log.read_text(encoding="utf-8")


def test_thin_headroom_after_deploy_reclaims_build_cache(tmp_path: Path) -> None:
    """A successful deploy must not leave the host one build from full.

    Build cache is what grows across deploys, so it is reclaimed only after every
    service is healthy and labelled — never before the deploy has succeeded.
    """
    local, remote, bin_dir, _old_sha, new_sha = _build_repos(tmp_path)
    docker_log = tmp_path / "docker.log"
    _install_fakes(bin_dir, remote, docker_log, revision="from-git", free_gb=6)

    result = _run_deploy(local, remote, bin_dir, new_sha)

    assert result.returncode == 0, result.stdout + result.stderr
    calls = docker_log.read_text(encoding="utf-8")
    assert "builder prune" in calls, calls
    # Prune happens after the revision gate, not in place of it.
    assert calls.index("opencontainers.image.revision") < calls.index("builder prune")
