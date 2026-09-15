from __future__ import annotations

import logging
import os
import toml
import yaml

from copy import deepcopy
from typing import ClassVar, Optional, Mapping, Union

from ..config.state import config
from ..constants import (
    Arch,
    BASE_DISTROS,
    KUPFER_HTTPS,
    REPOS_CONFIG_FILE,
    REPOS_CONFIG_FILE_USER,
    REPOSITORIES,
)
from ..dictscheme import (
    DictScheme,
    toml_inline_dicts,
    TomlPreserveInlineDictEncoder,
)
from ..utils import sha256sum

REPOS_KEY = "repos"
REMOTEURL_KEY = "remote_url"
LOCALONLY_KEY = "local_only"
OPTIONS_KEY = "options"
BASEDISTROS_KEY = "base_distros"

_current_config: Optional[ReposConfigFile]


class AbstrRepoConfig(DictScheme):
    options: Optional[dict[str, str]]
    _strip_hidden: ClassVar[bool] = True
    _sparse: ClassVar[bool] = True


class BaseDistroRepo(AbstrRepoConfig):
    remote_url: Optional[str]


class RepoConfig(AbstrRepoConfig):
    remote_url: Optional[Union[str, dict[Arch, str]]]
    local_only: Optional[bool]


class BaseDistro(DictScheme):
    remote_url: Optional[str]
    repos: dict[str, BaseDistroRepo]


class ReposConfigFile(DictScheme):
    ombs_min_version: Optional[str]
    ombs_ci_version: Optional[str]
    remote_url: Optional[str]
    repos: dict[str, RepoConfig]
    base_distros: dict[Arch, BaseDistro]
    _path: Optional[str]
    _checksum: Optional[str]
    _strip_hidden: ClassVar[bool] = True
    _sparse: ClassVar[bool] = True

    def __init__(self, d, *, allow_extra: bool = True, **kwargs):
        super().__init__(d=d, allow_extra=allow_extra, **kwargs)
        self[REPOS_KEY] = self.get(REPOS_KEY, {})
        for repo_cls, defaults, repos, remote_url in [
            (
                RepoConfig,
                REPO_DEFAULTS,
                self.get(REPOS_KEY),
                d.get(REMOTEURL_KEY, None),
            ),
            *[
                (
                    BaseDistroRepo,
                    BASE_DISTRO_DEFAULTS,
                    _distro.repos,
                    _distro.get(REMOTEURL_KEY, None),
                )
                for _distro in self.base_distros.values()
            ],
        ]:
            if repos is None:
                continue
            for name, repo in repos.items():
                _repo = dict(defaults | (repo or {}))  # type: ignore[operator]
                if REMOTEURL_KEY not in repo and not repo.get(
                    LOCALONLY_KEY, None
                ):
                    _repo[REMOTEURL_KEY] = remote_url
                repos[name] = repo_cls(_repo, **kwargs)

    def update(self, *kargs, allow_extra: bool = True, **kwargs):
        return super().update(*kargs, allow_extra=allow_extra, **kwargs)  # type: ignore[misc]

    @staticmethod
    def parse_config(path: str) -> ReposConfigFile:
        try:
            with open(path, "r") as fd:
                data = yaml.safe_load(fd)
            data["_path"] = path
            data["_checksum"] = sha256sum(path)
            return ReposConfigFile(data, validate=True)
        except Exception as ex:
            logging.error(f'Error parsing repos config at "{path}":\n{ex}')
            raise ex

    def toToml(
        self,
        strip_hidden=None,
        sparse=None,
        encoder=TomlPreserveInlineDictEncoder(),
    ):
        d = self.toDict(strip_hidden=strip_hidden, sparse=sparse)
        for key in [REPOS_KEY]:
            if key not in d or not isinstance(d[key], Mapping):
                continue
            inline = {
                name: {k: toml_inline_dicts(v) for k, v in value.items()}
                for name, value in d[key].items()
            }
            logging.info(f"Inlined {key}: {inline}")
            d[key] = inline
        return toml.dumps(d, encoder=encoder)


REPO_DEFAULTS = {
    LOCALONLY_KEY: None,
    REMOTEURL_KEY: None,
    OPTIONS_KEY: {"SigLevel": "Never"},
}

BASE_DISTRO_DEFAULTS = {
    REMOTEURL_KEY: None,
    OPTIONS_KEY: None,
}

REPOS_CONFIG_DEFAULT = ReposConfigFile(
    {
        "_path": "__DEFAULTS__",
        "_checksum": None,
        REMOTEURL_KEY: KUPFER_HTTPS,
        REPOS_KEY: {
            "ombootstrap_local": REPO_DEFAULTS | {LOCALONLY_KEY: True},
            **{
                r: deepcopy(REPO_DEFAULTS) | {LOCALONLY_KEY: True}
                for r in REPOSITORIES
            },
        },
        BASEDISTROS_KEY: {
            arch: {
                REMOTEURL_KEY: None,
                "repos": {
                    k: {"remote_url": v} for k, v in arch_def["repos"].items()
                },
            }
            for arch, arch_def in BASE_DISTROS.items()
        },
    }
)

_current_config = None


def get_repo_config(
    initialize_pkgbuilds: bool = False,
    repo_config_file: Optional[str] = None,
) -> tuple[ReposConfigFile, bool]:
    global _current_config
    user_repo_config = os.path.join(
        config.get_path("pkgbuilds"), REPOS_CONFIG_FILE_USER
    )
    if repo_config_file is None:
        repo_config_file_path = os.path.join(
            config.get_path("pkgbuilds"), REPOS_CONFIG_FILE
        )
        if os.path.exists(user_repo_config):
            repo_config_file_path = user_repo_config
    else:
        repo_config_file_path = repo_config_file
    config_exists = os.path.exists(repo_config_file_path)
    if not config_exists and _current_config is None:
        if initialize_pkgbuilds:
            from ..packages.pkgbuild import init_pkgbuilds

            init_pkgbuilds(update=False)
            return get_repo_config(
                initialize_pkgbuilds=False, repo_config_file=repo_config_file
            )
        if repo_config_file is not None:
            raise Exception(
                f"Requested repo config {repo_config_file} doesn't exist"
            )
        logging.warning(
            f"{repo_config_file_path} doesn't exist, using built-in repo config defaults"
        )
        _current_config = deepcopy(REPOS_CONFIG_DEFAULT)
        return _current_config, False
    if os.path.exists(user_repo_config):
        repo_config_file_path = user_repo_config
        config_exists = True
    changed = False
    if (not _current_config) or (
        config_exists
        and _current_config._checksum != sha256sum(repo_config_file_path)
    ):
        if config_exists:
            conf = ReposConfigFile.parse_config(repo_config_file_path)
        else:
            conf = REPOS_CONFIG_DEFAULT
        changed = conf != (_current_config or {})
        if changed:
            _current_config = deepcopy(conf)
    else:
        logging.debug("Repo config: Cache hit!")
    assert _current_config
    return _current_config, changed


def get_repos(**kwargs) -> list[RepoConfig]:
    config, _ = get_repo_config(**kwargs)
    return list(config.repos.values())
