# Development Guide

This document covers repository layout, configuration, and how to add or change measurement plugins.

## Repository layout

```text
reference_values.py    # CLI: verify | build | update-digests
internal/              # Python helpers (not a published package)
  config.py            # Load / validate / write YAML
  workspace.py         # Git clone, oras pull, run measurements/*.sh
  commands.py          # verify, build, update-digests implementations
measurements/          # One shell plugin per reference value
scripts/               # verify-provenance.sh
arch/                  # Per-platform OCI digests + RV URIs
versions.yaml          # CoCo version, Kata pins, git tool pins
```

YAML pins *what* to use (versions, digests, URIs). Shell plugins in `measurements/` define *how* to measure.

## Configuration

### `versions.yaml`

- `version` — CoCo release version (used in output JSON keys)
- `kata` — OCI registry and attestation metadata for upstream verification (`revision` is the authoritative pin; release tag can be kept as a comment)
- `git` — external tool repos (`url` + `digest`); cloned to `.work/git/<key>/` before plugins run
- `reference_values_files` — list of arch-specific YAML files to merge

### `arch/*.yaml`

Each `reference_values` entry includes:

- `name` — stable identifier for logs and extract paths (need not match the script filename)
- `measurement_script` — filename under `measurements/` (e.g. `tdx-kernel.sh`)
- `reference_value_uri` — RVPS URI prefix for the output key
- `artifacts` — list of the materials to derive this reference value `{ name, oci_sha256 }`
- optional `description`, `arch`

## Adding a reference value

1. Add an entry in `arch/<platform>.yaml` with `name`, `measurement_script`, `reference_value_uri`, and pinned `artifacts`.
2. Add an executable `measurements/<measurement_script>` whose **stdout** is the reference value as hex only: no trailing newline, no `0x` prefix, and no other text. Send logs and diagnostics to **stderr** (they are not stored).
3. If the measurement needs a new external repo, add it under `git:` in `versions.yaml` (`url` + commit `digest`).

The build step reads plugin stdout and applies `.strip()` before writing JSON, so a trailing newline is tolerated but must not be relied on—plugins should emit exactly the hex string.

Example plugin (see also `measurements/tdx-kernel.sh`):

```bash
#!/usr/bin/env bash
set -euo pipefail

CALC="${GIT_TD_SHIM_ROOT}/td-shim-tools/src/bin/td-payload-reference-calculator/td_payload_qemu_hash.py"
python3 "${CALC}" -k "./opt/kata/share/kata-containers/vmlinuz.container"
```

Use paths **inside the cloned git repo** in the plugin; do not list tool file paths in `versions.yaml`.

Example arch entry:

```yaml
- name: tdx-kernel
  measurement_script: tdx-kernel.sh
  reference_value_uri: "rvps:///github.com/confidential-containers/tdx/kernel"
  artifacts:
    - name: kernel
      oci_sha256: "sha256:..."
```

`name` and `measurement_script` may differ when reusing a script or renaming an RV without renaming the plugin file.

## Plugin environment

Set by `reference_values.py build` before each plugin runs:

| Variable | Meaning |
|----------|---------|
| `GIT_<KEY>_ROOT` | Clone root for `git.<key>` (e.g. `GIT_TD_SHIM_ROOT`) |
| `GIT_REPOS_ROOT` | `.work/git` |
| `RV_EXTRACT_DIR` | Extracted Kata payload tree (same as plugin cwd) |
| `COCO_VERSION` | `versions.yaml` → `version` |
| `REPO_ROOT` | Root of this repository |

Plugins must be executable (`chmod +x measurements/*.sh`).

## Extract layout

| Artifacts per RV | Extract target | Plugin cwd |
|------------------|----------------|------------|
| **One** | `.work/extracts/<rv>/<artifact>/` | That directory |
| **Two or more** | All into `.work/extracts/<rv>/` (shared root) | Shared root |

Kata archives unpack with paths such as `opt/kata/share/...` at the archive root. Plugin paths like `./opt/kata/...` are resolved relative to cwd.

For multi-artifact entries, archives are extracted in list order; colliding paths are overwritten (last wins).

## Updating OCI digests

When Kata is bumped, every artifact digest under `reference_values` usually changes. Manually finding and updating each `oci_sha256` across arch files is error-prone and time-consuming, especially when multiple artifacts are listed per entry.

The `update-digests` subcommand exists to automate that repetitive work: given one Kata release tag, it resolves the latest digest for each configured artifact and updates all related YAML files in one pass.

After a Kata release bump:

```bash
python3 reference_values.py update-digests versions.yaml <kata-tag>
```

This updates `oci_sha256` in each file listed in `reference_values_files` (not a full merge into `versions.yaml`). Re-run `verify` and `build` afterward.

> [NOTE!]
> Files are rewritten with PyYAML, so quoting/indentation may change; review the diff before committing. It does not update `kata.revision` or other attestation fields—only the digests.

To bump an external tool, update `git.<key>.digest` in `versions.yaml` manually when changing the pinned commit.

## Local workflow

```bash
pip install -r requirements.txt
python3 reference_values.py verify versions.yaml
python3 reference_values.py build versions.yaml results/reference-values.json
```
