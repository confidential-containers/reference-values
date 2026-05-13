# CoCo Official Release Reference Values

This repository is to calculate and publish the official reference values that correspond to **CoCo (Confidential Containers)** community releases.

## Repository Purpose

- Maintain a declarative mapping of official CoCo release targets in `versions.yaml` + per-arch files under `arch/`
- Verify Kata upstream artifact attestations before building release reference values
- Reproducibly calculate reference values from Kata payloads for each official CoCo release
- Publish generated reference values as workflow/release artifacts for RVPS consumers
- Generate SLSA build provenance attestation metadata for the published output JSON

## How It Works

1. Read `versions.yaml` and load referenced files from `reference_values_files` (for example `arch/x86_64-tdx.yaml`).
2. Verify each configured artifact attestation with `reference_values.py verify`.
   - Checks each artifact digest against the expected Kata source repository, source revision, workflow digest, workflow trigger, and main branch workflow ref.
   - Tooling: `gh attestation verify`.
3. Run `reference_values.py build`, which for each `reference_values` entry:
   - Pulls Kata OCI artifacts (`<kata.oci>/<name>@<oci_sha256>`) and extracts `kata-static-<name>.tar.zst`
   - Clones pinned external tool repos from `versions.yaml` → `git`
   - Runs the shell plugin named by `measurement_script` under `measurements/` and collects stdout as the reference value
   - Tooling: `oras`, `tar`, `git`.
4. Write final output JSON to `results/reference-values.json`.

Measurement command lines live in `measurements/` (not in YAML). See [DEVELOPMENT.md](DEVELOPMENT.md) for how to add or change plugins.

## Local Run

Prerequisites: `python3`, `git`, `oras`, `gh`, `jq`, `tar`, and Python deps from `requirements.txt`.

```bash
pip install -r requirements.txt

# Verify upstream kata provenance attestation
python3 reference_values.py verify versions.yaml

# Generate reference value manifest
python3 reference_values.py build versions.yaml results/reference-values.json

# (Optional) Update all OCI artifact digests in *.yamls using given kata-version
python3 reference_values.py update-digests versions.yaml 3.30.0
```

> [!NOTE]
> If local `gh attestation ...` commands fail with `unknown command "attestation" for "gh"`, upgrade GitHub CLI to a version that includes the attestation subcommand.

## GitHub Actions

Workflow: `.github/workflows/reference-values.yml`

- **pull_request**: verify upstream attestations and build JSON (no release upload)
- **release** (`published`): build, upload `reference-values.json`, attach to the GitHub Release, and run `actions/attest`

## Verify Release Attestation (gh CLI)

When `reference-values.json` is published as a GitHub Release asset, you can verify its artifact attestation with `gh attestation verify`.

```bash
TAG="v0.21.0"

gh release download "$TAG" \
  --repo confidential-containers/reference-values \
  --pattern "reference-values.json"

gh attestation verify reference-values.json \
  --repo confidential-containers/reference-values

# Stricter: pin workflow to the release tag
gh attestation verify reference-values.json \
  --repo confidential-containers/reference-values \
  --signer-workflow "confidential-containers/reference-values/.github/workflows/reference-values.yml@refs/tags/${TAG}"
```

## Development

To add a reference value, update OCI digests, or write a measurement plugin, see [DEVELOPMENT.md](DEVELOPMENT.md).
