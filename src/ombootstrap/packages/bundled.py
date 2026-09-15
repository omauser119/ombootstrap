"""Install versioned Ombootstrap recipes without overwriting local edits."""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

BUNDLED = Path(__file__).resolve().parents[1] / "data" / "pkgbuilds"
MANIFEST = ".ombs-bundle.json"


def _digest(path: Path) -> str | None:
    return (
        hashlib.sha256(path.read_bytes()).hexdigest()
        if path.is_file()
        else None
    )


def sync_bundled(destination: str, source: Path = BUNDLED) -> None:
    target = Path(destination).resolve()
    if target == source.resolve():
        raise ValueError(
            "The build cache must not be the bundled source directory"
        )
    files = {
        str(p.relative_to(source)): p
        for p in source.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
    }
    if "repos.yml" not in files:
        raise RuntimeError(
            "Ombootstrap package recipes are missing from this installation"
        )
    marker = target / MANIFEST
    previous = json.loads(marker.read_text()) if marker.exists() else {}
    current = {name: _digest(path) for name, path in files.items()}
    changes = {
        name
        for name in previous.keys() | current.keys()
        if previous.get(name) != current.get(name)
    }
    conflicts = [
        name
        for name in changes
        if _digest(target / name)
        not in (previous.get(name), current.get(name))
    ]
    if conflicts:
        raise RuntimeError(
            "Bundle update conflicts with local recipes: "
            + ", ".join(sorted(conflicts))
        )
    target.mkdir(parents=True, exist_ok=True)
    for name in sorted(changes):
        dest = target / name
        if name in files:
            dest.parent.mkdir(parents=True, exist_ok=True)
            # Materialize source symlinks: wheels and caches must be self-contained.
            shutil.copy2(files[name], dest)
        elif dest.exists():
            dest.unlink()
    marker.write_text(json.dumps(current, indent=2) + "\n")
    if not (target / ".git").exists():
        subprocess.run(
            ["git", "init", "-b", "ombootstrap", str(target)],
            check=True,
            capture_output=True,
        )
        subprocess.run(["git", "-C", str(target), "add", "."], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(target),
                "-c",
                "user.name=Ombootstrap",
                "-c",
                "user.email=ombs@localhost",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "-m",
                "Initialize bundled Ombootstrap recipes",
            ],
            check=True,
            capture_output=True,
        )
