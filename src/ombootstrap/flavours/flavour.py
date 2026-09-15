from __future__ import annotations

import json
import logging
import os

from typing import Optional

from ombootstrap.config.state import config
from ombootstrap.constants import (
    FLAVOUR_DESCRIPTION_PREFIX,
    FLAVOUR_INFO_FILE,
)
from ombootstrap.dictscheme import DictScheme
from ombootstrap.packages.pkgbuild import (
    discover_pkgbuilds,
    get_pkgbuild_by_name,
    init_pkgbuilds,
    Pkgbuild,
)
from ombootstrap.utils import color_str


class FlavourInfo(DictScheme):
    rootfs_size: int  # rootfs size in GB
    description: Optional[str]

    def __repr__(self):
        return f"rootfs_size: {self.rootfs_size}"


class Flavour(DictScheme):
    name: str
    pkgbuild: Pkgbuild
    description: str
    flavour_info: Optional[FlavourInfo]

    @staticmethod
    def from_pkgbuild(pkgbuild: Pkgbuild) -> Flavour:
        name = pkgbuild.name
        if not name.startswith("flavour-"):
            raise Exception(
                f'Flavour package "{name}" doesn\'t start with "flavour-": "{name}"'
            )
        if name.endswith("-common"):
            raise Exception(
                f'Flavour package "{name}" ends with "-common": "{name}"'
            )
        name = name[8:]  # split off 'flavour-'
        description = pkgbuild.description
        # cut off FLAVOUR_DESCRIPTION_PREFIX
        if description.lower().startswith(FLAVOUR_DESCRIPTION_PREFIX.lower()):
            description = description[len(FLAVOUR_DESCRIPTION_PREFIX) :]
        return Flavour(
            name=name,
            pkgbuild=pkgbuild,
            description=description.strip(),
            flavour_info=None,
        )

    def __repr__(self):
        return f'Flavour<"{self.name}": "{self.description}", package: {self.pkgbuild.name if self.pkgbuild else "??? PROBABLY A BUG!"}{f", {self.flavour_info}" if self.flavour_info else ""}>'

    def __str__(self):
        return self.nice_str()

    def nice_str(self, newlines: bool = False, colors: bool = False) -> str:
        separator = "\n" if newlines else ", "

        def get_lines(k, v, key_prefix=""):
            results = []
            full_k = f"{key_prefix}.{k}" if key_prefix else k
            if not isinstance(v, (dict, DictScheme)):
                results = [f"{color_str(full_k, bold=True)}: {v}"]
            else:
                for _k, _v in v.items():
                    if _k.startswith("_"):
                        continue
                    results += get_lines(_k, _v, key_prefix=full_k)
            return results

        return separator.join(get_lines(None, self))

    def parse_flavourinfo(self, lazy: bool = True):
        if lazy and self.flavour_info is not None:
            return self.flavour_info
        infopath = os.path.join(
            config.get_path("pkgbuilds"), self.pkgbuild.path, FLAVOUR_INFO_FILE
        )
        if not os.path.exists(infopath):
            raise Exception(
                f"Error parsing flavour info for flavour {self.name}: file doesn't exist: {infopath}"
            )
        try:
            defaults = {"description": None}
            with open(infopath, "r") as fd:
                infodict = json.load(fd)
            i = FlavourInfo(**(defaults | infodict))
        except Exception as ex:
            raise Exception(
                f"Error parsing {FLAVOUR_INFO_FILE} for flavour {self.name}: {ex}"
            )
        self.flavour_info = i
        if i.description:
            self.description = i.description
        return i


_flavours_discovered: bool = False
_flavours_cache: dict[str, Flavour] = {}


def get_flavours(lazy: bool = True):
    global _flavours_cache, _flavours_discovered
    if lazy and _flavours_discovered:
        return _flavours_cache
    logging.info("Searching PKGBUILDs for flavour packages")
    flavours: dict[str, Flavour] = {}
    pkgbuilds: dict[str, Pkgbuild] = discover_pkgbuilds(
        lazy=(lazy or not _flavours_discovered)
    )
    for pkg in pkgbuilds.values():
        name = pkg.name
        if not name.startswith("flavour-") or name.endswith("-common"):
            continue
        name = name[8:]  # split off 'flavour-'
        logging.info(f"Found flavour package {name}")
        flavours[name] = Flavour.from_pkgbuild(pkg)
    _flavours_cache.clear()
    _flavours_cache.update(flavours)
    _flavours_discovered = True
    return flavours


def get_flavour(name: str, lazy: bool = True):
    global _flavours_cache
    pkg_name = f"flavour-{name}"
    if lazy and name in _flavours_cache:
        return _flavours_cache[name]
    try:
        logging.info(f"Trying to find PKGBUILD for flavour {name}")
        init_pkgbuilds()
        pkg = get_pkgbuild_by_name(pkg_name)
    except Exception as ex:
        raise Exception(
            f"Error parsing PKGBUILD for flavour package {pkg_name}:\n{ex}"
        )
    assert pkg and pkg.name == pkg_name
    flavour = Flavour.from_pkgbuild(pkg)
    _flavours_cache[name] = flavour
    return flavour


def get_profile_flavour(profile_name: Optional[str] = None) -> Flavour:
    profile = config.enforce_profile_flavour_set(profile_name=profile_name)
    return get_flavour(profile.flavour)
