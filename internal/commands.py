#!/usr/bin/env python3

# Copyright (c) 2026 Alibaba Cloud
#
# SPDX-License-Identifier: Apache-2.0

import json
import logging
import os
import pathlib
import subprocess

from internal.config import _read_yaml, load_config, load_config_tree, write_config_tree
from internal.workspace import (
    plugin_script,
    prepare_git_repos,
    prepare_rv_extract,
    repo_root_from_config,
    reset_dir,
    run_measurement,
    work_paths,
)

LOG = logging.getLogger("reference-values")


def verify(config_path: str | pathlib.Path) -> int:
    config_file = pathlib.Path(config_path).resolve()
    repo_root = repo_root_from_config(config_file)
    config = load_config(config_file)

    script = repo_root / "scripts" / "verify-provenance.sh"
    if not script.is_file() or not os.access(script, os.R_OK | os.X_OK):
        LOG.error("Missing or non-executable: %s", script)
        return 1

    kata = config["kata"]
    oci_base = kata["oci"].rstrip("/")
    failures = 0
    seen: set[str] = set()

    for rv in config["reference_values"]:
        for artifact in rv["artifacts"]:
            key = f"{artifact['name']}@{artifact['oci_sha256']}"
            if key in seen:
                continue
            seen.add(key)
            oci = f"{oci_base}/{artifact['name']}@{artifact['oci_sha256']}"
            LOG.info("Verifying %s", oci)
            proc = subprocess.run(
                [
                    str(script),
                    "-a", oci,
                    "-s", kata["revision"],
                    "-w", kata["workflow_digest"],
                    "-t", kata["workflow_trigger"],
                    "-r", kata["source_repository"],
                ],
                cwd=repo_root,
            )
            if proc.returncode != 0:
                failures += 1

    if failures:
        LOG.error("Attestation verification failed for %d artifact(s).", failures)
        return 1
    LOG.info("All attestations OK (%d unique artifacts).", len(seen))
    return 0


def build(config_path: str | pathlib.Path, output_path: str | pathlib.Path) -> None:
    config_file = pathlib.Path(config_path).resolve()
    repo_root = repo_root_from_config(config_file)
    config = load_config(config_file)
    version = str(config["version"])
    oci_base = config["kata"]["oci"].rstrip("/")

    paths = work_paths(repo_root)
    reset_dir(paths["pulls"])
    reset_dir(paths["extracts"])
    if not (repo_root / "measurements").is_dir():
        raise RuntimeError(f"Missing measurements/ under {repo_root}")

    env = prepare_git_repos(config["git"], paths["git"])
    env["COCO_VERSION"] = version
    env["REPO_ROOT"] = str(repo_root)

    result = {}
    for rv in config["reference_values"]:
        LOG.info("Processing %s", rv["name"])
        extract_dir = prepare_rv_extract(rv, oci_base, paths["pulls"], paths["extracts"])
        value = run_measurement(plugin_script(rv, repo_root), extract_dir, env)
        key = f"{rv['reference_value_uri']}:{version}"
        result[key] = value
        LOG.info("Collected %s", key)

    out = pathlib.Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write("\n")
    LOG.info("Wrote %s (%d entries)", out, len(result))
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _fetch_artifact_digest(oci_base: str, artifact_name: str, kata_tag: str, arch: str) -> str:
    ref = f"{oci_base}/{artifact_name}:{kata_tag}-{arch}"
    proc = subprocess.run(
        ["oras", "manifest", "fetch", "--descriptor", ref],
        capture_output=True,
        text=True,
        check=True,
    )
    digest = json.loads(proc.stdout).get("digest", "")
    if not digest.startswith("sha256:"):
        raise RuntimeError(f"Unexpected digest for {ref}: {digest}")
    LOG.info("%s -> %s", ref, digest)
    return digest.lower()


def update_digests(config_path: str | pathlib.Path, kata_tag: str) -> int:
    config_file = pathlib.Path(config_path).resolve()
    # Root YAML as stored on disk (no merged reference_values from includes).
    root_doc = _read_yaml(config_file)
    config, includes = load_config_tree(config_file)
    oci_base = config["kata"]["oci"].rstrip("/")

    docs_to_update: list[tuple[pathlib.Path, dict]] = list(includes)
    if root_doc.get("reference_values"):
        docs_to_update.append((config_file, root_doc))

    for path, doc in docs_to_update:
        for rv in doc.get("reference_values", []):
            for artifact in rv["artifacts"]:
                arch = artifact.get("arch", rv.get("arch", "x86_64"))
                artifact["oci_sha256"] = _fetch_artifact_digest(
                    oci_base, artifact["name"], kata_tag, arch
                )

    write_config_tree(config_file, root_doc, includes)
    LOG.info("Updated OCI digests under %s", config_file.parent)
    return 0
