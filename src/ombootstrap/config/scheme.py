from __future__ import annotations

from munch import Munch
from typing import Any, Optional, Mapping, Union

from ombootstrap.dictscheme import DictScheme
from ombootstrap.constants import Arch


class SparseProfile(DictScheme):
    parent: Optional[str]
    device: Optional[str]
    flavour: Optional[str]
    pkgs_include: Optional[list[str]]
    pkgs_exclude: Optional[list[str]]
    hostname: Optional[str]
    username: Optional[str]
    password: Optional[str]
    size_extra_mb: Optional[Union[str, int]]

    def __repr__(self):
        return f"{type(self)}{dict.__repr__(self.toDict())}"


class Profile(SparseProfile):
    parent: Optional[str]
    device: str
    flavour: str
    pkgs_include: list[str]
    pkgs_exclude: list[str]
    hostname: str
    username: str
    password: Optional[str]
    size_extra_mb: Union[str, int]


class WrapperSection(DictScheme):
    type: str  # NOTE: rename to 'wrapper_type' if this causes problems


class BuildSection(DictScheme):
    ccache: bool
    clean_mode: bool
    crosscompile: bool
    crossdirect: bool
    threads: int


class PkgbuildsSection(DictScheme):
    git_repo: str
    git_branch: str


class PacmanSection(DictScheme):
    parallel_downloads: int
    check_space: bool
    repo_branch: str


class PathsSection(DictScheme):
    cache_dir: str
    chroots: str
    pacman: str
    packages: str
    pkgbuilds: str
    jumpdrive: str
    images: str
    ccache: str
    rust: str


class ProfilesSection(DictScheme):
    current: str
    default: SparseProfile

    def __init__(self, *kargs, allow_extra: bool = True, **kwargs):
        super().__init__(*kargs, allow_extra=allow_extra, **kwargs)  # type: ignore[misc]

    @classmethod
    def transform(
        cls,
        values: Mapping[str, Any],
        validate: bool = True,
        allow_extra: bool = True,
        type_hints: Optional[dict[str, Any]] = None,
    ):
        results = {}
        for k, v in values.items():
            if k == "current":
                results[k] = v
                continue
            if not allow_extra and k != "default":
                raise Exception(
                    f"Unknown key {k} in profiles section (Hint: extra_keys not allowed for some reason)"
                )
            if not isinstance(v, dict):
                raise Exception(f"profile {v} is not a dict!")
            results[k] = SparseProfile.fromDict(
                v, validate=True, allow_extra=allow_extra
            )
        return results

    def update(self, d, validate: bool = True, **kwargs):  # type: ignore[override]
        Munch.update(
            self, self.transform(values=d, validate=validate, **kwargs)
        )

    def __repr__(self):
        return f"{type(self)}{dict.__repr__(self.toDict())}"


class Config(DictScheme):
    wrapper: WrapperSection
    build: BuildSection
    pkgbuilds: PkgbuildsSection
    pacman: PacmanSection
    paths: PathsSection
    profiles: ProfilesSection

    @classmethod
    def fromDict(  # type: ignore[override]
        cls,
        values: Mapping[str, Any],
        validate: bool = True,
        allow_extra: bool = False,
        allow_incomplete: bool = False,
    ):
        values = dict(values)  # copy for later modification
        _vals = {}
        for name, _class in cls._type_hints.items():
            if name not in values:
                if not allow_incomplete:
                    raise Exception(
                        f'Config key "{name}" not in input dictionary'
                    )
                continue
            value = values.pop(name)
            if not isinstance(value, _class):
                value = _class(value, validate=validate)
            _vals[name] = value

        if values:
            if validate:
                raise Exception(
                    f"values contained unknown keys: {list(values.keys())}"
                )
            _vals |= values

        return Config(_vals, validate=validate)


class RuntimeConfiguration(DictScheme):
    verbose: bool
    no_wrap: bool
    error_shell: bool
    config_file: Optional[str]
    script_source_dir: Optional[str]
    arch: Optional[Arch]
    uid: Optional[int]
    progress_bars: Optional[bool]
    colors: Optional[bool]


class ConfigLoadState(DictScheme):
    load_finished: bool
    exception: Optional[Exception]

    def __init__(self, d: dict = {}):
        self.load_finished = False
        self.exception = None
        self.update(d)
