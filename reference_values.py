#!/usr/bin/env python3

# Copyright (c) 2026 Alibaba Cloud
#
# SPDX-License-Identifier: Apache-2.0

"""
CoCo reference values — single CLI entry point.

  verify         Upstream Kata artifact attestation checks
  build          Generate results/reference-values.json
  update-digests Refresh oci_sha256 for a Kata release tag

Library code lives in internal/; per-RV logic in measurements/*.sh
"""

import argparse
import logging

from internal.commands import build, update_digests, verify
from internal.config import ConfigError


def _setup_logging() -> logging.Logger:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s - %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers = [handler]
    return logging.getLogger("reference-values")


def main(argv: list[str] | None = None) -> int:
    log = _setup_logging()
    parser = argparse.ArgumentParser(description="CoCo release reference values.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("verify", help="Verify upstream Kata attestations")
    p.add_argument("config", nargs="?", default="versions.yaml")
    p.set_defaults(func="verify")

    p = sub.add_parser("build", help="Build reference-values.json")
    p.add_argument("config", nargs="?", default="versions.yaml")
    p.add_argument("output", nargs="?", default="results/reference-values.json")
    p.set_defaults(func="build")

    p = sub.add_parser(
        "update-digests",
        help="Update pinned OCI digests using a Kata release tag",
    )
    p.add_argument("config")
    p.add_argument("kata_tag")
    p.set_defaults(func="update-digests")

    args = parser.parse_args(argv)
    try:
        if args.func == "verify":
            return verify(args.config)
        if args.func == "build":
            build(args.config, args.output)
            return 0
        return update_digests(args.config, args.kata_tag)
    except ConfigError as exc:
        log.error("%s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
