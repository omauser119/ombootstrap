#!/bin/bash
mypy --pretty --show-error-codes --check-untyped-defs --install-types --ignore-missing-imports -p ombootstrap "$@"
