#!/usr/bin/env python3

# Copyright (c) 2026 Alibaba Cloud
#
# SPDX-License-Identifier: Apache-2.0

import pathlib
import re

try:
    import yaml
except ImportError as exc:
    raise SystemExit(
        "Missing dependency: PyYAML. Install with `pip install -r requirements.txt`."
    ) from exc

_SHA256_RE = re.compile(r"^sha256:[0-9a-fA-F]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_KATA_KEYS = (
    "oci",
    "revision",
    "source_repository",
    "workflow_digest",
    "workflow_trigger",
)


class ConfigError(Exception):
    """Raised when versions.yaml or an included arch file is invalid."""


def _read_yaml(path: pathlib.Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: expected a YAML mapping at the top level.")
    return data


def _resolve_include(config_file: pathlib.Path, include_path: str) -> pathlib.Path:
    if not include_path or not isinstance(include_path, str):
        raise ConfigError(f"{config_file}: reference_values_files entries must be strings.")
    pure = pathlib.PurePosixPath(include_path)
    if pure.is_absolute() or ".." in pure.parts:
        raise ConfigError(f"{config_file}: unsafe include path '{include_path}'.")
    include_file = (config_file.parent / include_path).resolve()
    try:
        include_file.relative_to(config_file.parent.resolve())
    except ValueError as exc:
        raise ConfigError(
            f"{config_file}: include path '{include_path}' resolves outside the config directory."
        ) from exc
    if not include_file.is_file():
        raise ConfigError(f"{config_file}: include file not found: {include_file}")
    return include_file


def _require_str(obj: dict, key: str, context: str) -> str:
    if key not in obj:
        raise ConfigError(f"{context}: missing required field '{key}'.")
    value = obj[key]
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{context}: '{key}' must be a non-empty string.")
    return value.strip()


def _normalize_sha256(digest: object, context: str) -> str:
    raw = str(digest).strip()
    if not raw:
        raise ConfigError(f"{context}: missing or empty digest.")
    if not raw.startswith("sha256:"):
        raw = f"sha256:{raw}"
    if not _SHA256_RE.match(raw):
        raise ConfigError(f"{context}: digest must be sha256:<64 hex chars>, got '{raw}'.")
    return raw.lower()


def _validate_kata(kata: object, context: str) -> None:
    if not isinstance(kata, dict):
        raise ConfigError(f"{context}: 'kata' must be a mapping.")
    for key in _KATA_KEYS:
        _require_str(kata, key, f"{context}.kata")


def _validate_git(git: object, config_file: pathlib.Path) -> None:
    if not isinstance(git, dict) or not git:
        raise ConfigError(f"{config_file}: 'git' must be a non-empty mapping.")
    for repo_key, entry in git.items():
        ctx = f"{config_file} git[{repo_key!r}]"
        if not isinstance(entry, dict):
            raise ConfigError(f"{ctx}: must be a mapping.")
        _require_str(entry, "url", ctx)
        if not _COMMIT_RE.match(_require_str(entry, "digest", ctx)):
            raise ConfigError(f"{ctx}: digest must be a 40-character git commit SHA.")


def _validate_rv(rv: object, source: pathlib.Path) -> None:
    if not isinstance(rv, dict):
        raise ConfigError(f"{source}: each reference_values entry must be a mapping.")
    name = _require_str(rv, "name", f"{source} reference_values[]")
    ctx = f"{source} reference_values[{name!r}]"
    if not _require_str(rv, "reference_value_uri", ctx).startswith("rvps:///"):
        raise ConfigError(f"{ctx}: reference_value_uri must start with 'rvps:///'.")
    artifacts = rv.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ConfigError(f"{ctx}: 'artifacts' must be a non-empty list.")
    if "arch" in rv:
        _require_str(rv, "arch", ctx)
    _require_str(rv, "measurement_script", ctx)
    for artifact in artifacts:
        actx = f"{ctx}.artifacts[]"
        if not isinstance(artifact, dict):
            raise ConfigError(f"{actx}: each artifact must be a mapping.")
        aname = _require_str(artifact, "name", actx)
        artifact["oci_sha256"] = _normalize_sha256(
            artifact["oci_sha256"], f"{actx} '{aname}'"
        )


def _validate_rv_list(items: object, source: pathlib.Path) -> list[dict]:
    if items is None:
        return []
    if not isinstance(items, list):
        raise ConfigError(f"{source}: 'reference_values' must be a list.")
    seen: set[str] = set()
    for rv in items:
        _validate_rv(rv, source)
        if rv["name"] in seen:
            raise ConfigError(f"{source}: duplicate reference value name '{rv['name']}'.")
        seen.add(rv["name"])
    return items


def load_config_tree(
    config_path: str | pathlib.Path,
) -> tuple[dict, list[tuple[pathlib.Path, dict]]]:
    config_file = pathlib.Path(config_path).resolve()
    config = _read_yaml(config_file)
    _require_str(config, "version", str(config_file))
    _validate_kata(config.get("kata"), str(config_file))
    _validate_git(config.get("git"), config_file)

    include_items: list[tuple[pathlib.Path, dict]] = []
    reference_values: list[dict] = []
    seen_global: dict[str, pathlib.Path] = {}

    def merge_rvs(items: list[dict], source: pathlib.Path) -> None:
        for rv in items:
            if rv["name"] in seen_global:
                raise ConfigError(
                    f"Duplicate reference value name '{rv['name']}' in "
                    f"{seen_global[rv['name']]} and {source}."
                )
            seen_global[rv["name"]] = source
        reference_values.extend(items)

    merge_rvs(_validate_rv_list(config.get("reference_values", []), config_file), config_file)

    includes = config.get("reference_values_files") or []
    if not isinstance(includes, list):
        raise ConfigError(f"{config_file}: 'reference_values_files' must be a list.")

    for include_path in includes:
        include_file = _resolve_include(config_file, include_path)
        include_config = _read_yaml(include_file)
        include_items.append((include_file, include_config))
        merge_rvs(
            _validate_rv_list(include_config.get("reference_values", []), include_file),
            include_file,
        )

    if not reference_values:
        raise ConfigError(f"{config_file}: no reference_values configured.")

    config["reference_values"] = reference_values
    return config, include_items


def load_config(config_path: str | pathlib.Path) -> dict:
    config, _ = load_config_tree(config_path)
    return config


def write_config_tree(
    config_file: pathlib.Path,
    config: dict,
    include_items: list[tuple[pathlib.Path, dict]],
) -> None:
    with open(config_file, "w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False, allow_unicode=True, indent=2)
        f.write("\n")
    for path, doc in include_items:
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(doc, f, default_flow_style=False, sort_keys=False, allow_unicode=True, indent=2)
            f.write("\n")
