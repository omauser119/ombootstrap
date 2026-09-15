#!/bin/bash

ruff_args=()

if [[ "$1" == "--check" ]]; then
    ruff_args+=('--diff')
fi

set -x
ruff format "${ruff_args[@]}" "$@"
