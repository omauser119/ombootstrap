#!/usr/bin/env python3
"""Apply the formatting rules enforced by ``ombs packages check``.

The formatter is deliberately conservative: it only touches the metadata
section before the first PKGBUILD function.  It expands simple one-line
arrays, removes metadata blank lines that confuse the checker, and changes
tabs or inconsistent array indentation to four spaces.  It leaves shell
functions and arrays containing source-name ``::`` syntax untouched.
"""

from __future__ import annotations

import argparse
import re
import shlex
from pathlib import Path


DEFAULT_ROOT = (
    Path(__file__).resolve().parents[1]
    / "src/ombootstrap/data/pkgbuilds/omarchy"
)
FUNCTION = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\s*\(\)\s*\{")
ARRAY = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=\((.*)\)$")


def _expand_array(line: str) -> list[str] | None:
    match = ARRAY.match(line.strip())
    if match is None:
        return None
    key, contents = match.groups()
    if "::" in contents:
        return None
    try:
        values = shlex.split(contents, posix=False)
    except ValueError:
        return None
    if len(values) < 2:
        return None
    return [f"{key}=(", *(f"    {value}" for value in values), ")"]


def format_text(text: str) -> str:
    lines = text.splitlines()
    function_index = next(
        (
            index
            for index, line in enumerate(lines)
            if FUNCTION.match(line.strip())
        ),
        len(lines),
    )
    metadata = lines[:function_index]
    body = lines[function_index:]

    result: list[str] = []
    in_array = False
    for original in metadata:
        line = original.replace("\t", "    ")
        if not line.strip():
            continue
        if in_array:
            if line.strip() == ")":
                result.append(")")
                in_array = False
            else:
                result.append(f"    {line.strip()}")
            continue

        stripped = line.strip()
        expanded = _expand_array(stripped)
        if expanded is not None:
            result.extend(expanded)
        else:
            result.append(
                stripped if not stripped.startswith("#") else stripped
            )
            if stripped.endswith("=("):
                in_array = True

    if body:
        result.append("")
        result.extend(body)
    return "\n".join(result).rstrip() + "\n"


def iter_paths(arguments: list[str]) -> list[Path]:
    if not arguments:
        return sorted(DEFAULT_ROOT.rglob("PKGBUILD"))
    paths: list[Path] = []
    for argument in arguments:
        path = Path(argument)
        if path.is_dir():
            paths.extend(sorted(path.rglob("PKGBUILD")))
        else:
            paths.append(path)
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths", nargs="*", help="PKGBUILD files or directories"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="report files that would change without writing them",
    )
    args = parser.parse_args()

    changed = False
    for path in iter_paths(args.paths):
        before = path.read_text()
        after = format_text(before)
        if before == after:
            continue
        changed = True
        print(path)
        if not args.check:
            path.write_text(after)
    return int(changed and args.check)


if __name__ == "__main__":
    raise SystemExit(main())
