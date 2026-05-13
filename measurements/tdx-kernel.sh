#!/usr/bin/env bash
# Copyright (c) 2026 Alibaba Cloud
#
# SPDX-License-Identifier: Apache-2.0
#
# Preconditions (set by reference_values.py build):
#   GIT_TD_SHIM_ROOT  — cloned td-shim at versions.yaml git.td-shim.digest
#   cwd / RV_EXTRACT_DIR — extracted Kata payload tree

set -euo pipefail

CALCULATOR="${GIT_TD_SHIM_ROOT}/td-shim-tools/src/bin/td-payload-reference-calculator/td_payload_qemu_hash.py"
python3 "${CALCULATOR}" -k "./opt/kata/share/kata-containers/vmlinuz.container"
