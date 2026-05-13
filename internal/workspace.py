#!/usr/bin/env python3

# Copyright (c) 2026 Alibaba Cloud
#
# SPDX-License-Identifier: Apache-2.0

"""Artifact pull/extract, git tool clones, and measurement shell plugins."""

import logging
import os
import pathlib
import shutil
import subprocess

LOG = logging.getLogger("reference-values")


def repo_root_from_config(config_path: pathlib.Path) -> pathlib.Path:
    return config_path.resolve().parent


def work_paths(repo_root: pathlib.Path) -> dict[str, pathlib.Path]:
    work = repo_root / ".work"
    return {
        "work": work,
        "pulls": work / "pulls",
        "extracts": work / "extracts",
        "git": work / "git",
    }


def reset_dir(path: pathlib.Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)


def prepare_git_repos(git_config: dict, clone_dir: pathlib.Path) -> dict[str, str]:
    clone_dir.mkdir(parents=True, exist_ok=True)
    env: dict[str, str] = {"GIT_REPOS_ROOT": str(clone_dir.resolve())}
    for repo_key, entry in git_config.items():
        dest = clone_dir / repo_key
        url, digest = entry["url"], entry["digest"]
        if not (dest / ".git").is_dir():
            LOG.info("Cloning %s -> %s", url, dest)
            subprocess.run(
                ["git", "clone", url, str(dest)],
                check=True,
                capture_output=True,
                text=True,
            )
        head = subprocess.run(
            ["git", "-C", str(dest), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if head != digest:
            LOG.info("Checking out %s at %s", repo_key, digest)
            subprocess.run(
                ["git", "-C", str(dest), "fetch", "origin"],
                check=False,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["git", "-C", str(dest), "checkout", "--force", digest],
                check=True,
                capture_output=True,
                text=True,
            )
        if (
            subprocess.run(
                ["git", "-C", str(dest), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            != digest
        ):
            raise RuntimeError(f"Git repo '{repo_key}' at {dest} is not at {digest}.")
        env[f"GIT_{repo_key.upper().replace('-', '_')}_ROOT"] = str(dest.resolve())
    LOG.info("Prepared %d git repo(s) under %s", len(git_config), clone_dir)
    return env


def prepare_rv_extract(
    rv: dict,
    oci_base: str,
    pulls_dir: pathlib.Path,
    extracts_dir: pathlib.Path,
) -> pathlib.Path:
    rv_name = rv["name"]
    rv_pulls = pulls_dir / rv_name
    rv_extracts = extracts_dir / rv_name
    reset_dir(rv_pulls)
    reset_dir(rv_extracts)

    merge = len(rv["artifacts"]) > 1
    cwd: pathlib.Path | None = None

    for artifact in rv["artifacts"]:
        name = artifact["name"]
        LOG.info("  Pulling %s for %s (arch=%s)", name, rv_name, artifact.get("arch", rv.get("arch", "x86_64")))
        ref = f"{oci_base}/{name}@{artifact['oci_sha256']}"
        pulled = rv_pulls / name
        reset_dir(pulled)
        proc = subprocess.run(
            ["oras", "pull", "--output", str(pulled), ref],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"oras pull failed for '{ref}': {proc.stderr.strip()}")

        archive = pulled / f"kata-static-{name}.tar.zst"
        if not archive.exists():
            raise FileNotFoundError(f"Expected archive '{archive.name}' under '{pulled}'.")

        extract_to = rv_extracts if merge else rv_extracts / name
        if not merge:
            reset_dir(extract_to)

        LOG.info("Extracting %s -> %s", archive, extract_to)
        listed = subprocess.run(["tar", "-tf", str(archive)], capture_output=True, text=True, check=True)
        for member in listed.stdout.splitlines():
            p = pathlib.PurePosixPath(member)
            if p.is_absolute() or ".." in p.parts:
                raise RuntimeError(f"Unsafe path in '{archive}': {member}")
        subprocess.run(["tar", "-xf", str(archive), "-C", str(extract_to)], check=True, capture_output=True, text=True)
        cwd = extract_to

    if cwd is None:
        raise RuntimeError(f"Reference value '{rv_name}' has no artifacts.")
    return cwd


def plugin_script(rv: dict, repo_root: pathlib.Path) -> pathlib.Path:
    path = pathlib.Path(rv["measurement_script"])
    return path if path.is_absolute() else repo_root / "measurements" / path


def run_measurement(script: pathlib.Path, extract_dir: pathlib.Path, env: dict[str, str]) -> str:
    if not script.is_file():
        raise FileNotFoundError(f"Measurement plugin not found: {script}")
    if not os.access(script, os.X_OK):
        raise RuntimeError(f"Measurement plugin is not executable: {script}")

    run_env = os.environ.copy()
    run_env.update(env)
    run_env["RV_EXTRACT_DIR"] = str(extract_dir.resolve())
    LOG.info("Running plugin (cwd=%s): %s", extract_dir, script.name)
    proc = subprocess.run([str(script)], capture_output=True, text=True, cwd=extract_dir, env=run_env)
    out = proc.stdout.strip()
    if proc.returncode != 0:
        raise RuntimeError(
            f"Plugin '{script.name}' failed (exit {proc.returncode}): {proc.stderr.strip() or out}"
        )
    if not out:
        raise RuntimeError(f"Plugin '{script.name}' produced empty stdout.")
    return out
