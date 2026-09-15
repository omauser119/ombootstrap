import logging

from enum import IntFlag
from typing import Generic, Mapping, Optional, TypeVar

from ombootstrap.constants import (
    Arch,
    ARCHES,
    REPOSITORIES,
    KUPFER_BRANCH_MARKER,
    KUPFER_HTTPS,
    CHROOT_PATHS,
)
from ombootstrap.generator import generate_pacman_conf_body
from ombootstrap.config.state import config

from .repo import BinaryPackageType, RepoInfo, Repo, LocalRepo, RemoteRepo
from .repo_config import (
    AbstrRepoConfig,
    BaseDistro,
    ReposConfigFile,
    REPOS_CONFIG_DEFAULT,
    get_repo_config as _get_repo_config,
)


class DistroLocation(IntFlag):
    REMOTE = 0
    LOCAL = 1
    CHROOT = 3


RepoType = TypeVar("RepoType", bound=Repo)


class Distro(Generic[RepoType]):
    repos: Mapping[str, RepoType]
    arch: str

    def __init__(
        self, arch: Arch, repo_infos: dict[str, RepoInfo], scan=False
    ):
        assert arch in ARCHES
        self.arch = arch
        self.repos = dict[str, RepoType]()
        for repo_name, repo_info in repo_infos.items():
            self.repos[repo_name] = self._create_repo(
                name=repo_name,
                arch=arch,
                url_template=repo_info.url_template,
                options=repo_info.options,
                scan=scan,
            )

    def _create_repo(self, **kwargs) -> RepoType:
        raise NotImplementedError()
        Repo(**kwargs)

    def get_packages(self) -> dict[str, BinaryPackageType]:
        """get packages from all repos, semantically overlaying them"""
        results = dict[str, BinaryPackageType]()
        for repo in list(self.repos.values())[::-1]:
            assert repo.packages is not None
            results.update(repo.packages)
        return results

    def repos_config_snippet(
        self, extra_repos: Mapping[str, RepoInfo] = {}
    ) -> str:
        extras: list[Repo] = [
            Repo(
                name,
                url_template=info.url_template,
                arch=self.arch,
                options=info.options,
                scan=False,
            )
            for name, info in extra_repos.items()
        ]
        return "\n\n".join(
            repo.config_snippet()
            for repo in (extras + list(self.repos.values()))
        )

    def get_pacman_conf(
        self,
        extra_repos: Mapping[str, RepoInfo] = {},
        check_space: bool = True,
        in_chroot: bool = True,
    ):
        body = generate_pacman_conf_body(self.arch, check_space=check_space)
        return body + self.repos_config_snippet(extra_repos)

    def scan(self, lazy=True):
        for repo in self.repos.values():
            if not (lazy and repo.scanned):
                repo.scan()

    def is_scanned(self):
        for repo in self.repos.values():
            if not repo.scanned:
                return False
        return True


class LocalDistro(Distro[LocalRepo]):
    def _create_repo(self, **kwargs) -> LocalRepo:
        return LocalRepo(**kwargs)


class RemoteDistro(Distro[RemoteRepo]):
    def _create_repo(self, **kwargs) -> RemoteRepo:
        return RemoteRepo(**kwargs)


def get_kupfer(arch: str, url_template: str, scan: bool = False) -> Distro:
    repos = {
        name: RepoInfo(
            url_template=url_template, options={"SigLevel": "Never"}
        )
        for name in REPOSITORIES
    }
    remote = not url_template.startswith("file://")
    clss = RemoteDistro if remote else LocalDistro
    distro = clss(
        arch=arch,
        repo_infos=repos,
        scan=scan,
    )
    assert isinstance(distro, (LocalDistro, RemoteDistro))
    if remote:
        assert isinstance(distro, RemoteDistro)
        for repo in distro.repos.values():
            repo.cache_repo_db = True

    return distro


_kupfer_https: dict[Arch, RemoteDistro] = {}
_kupfer_local: dict[Arch, LocalDistro] = {}
_kupfer_local_chroots: dict[Arch, LocalDistro] = {}


def reset_distro_caches():
    global _kupfer_https, _kupfer_local, _kupfer_local_chroots
    for cache in _kupfer_https, _kupfer_local, _kupfer_local_chroots:
        assert isinstance(cache, dict)
        cache.clear()


def get_kupfer_url(
    url: str = KUPFER_HTTPS, branch: Optional[str] = None
) -> str:
    """gets the repo URL for `branch`, getting branch from config if `None` is passed."""
    branch = config.file.pacman.repo_branch if branch is None else branch
    return url.replace(KUPFER_BRANCH_MARKER, branch)


def get_repo_config(*args, **kwargs) -> ReposConfigFile:
    repo_config, changed = _get_repo_config(*args, **kwargs)
    if changed:
        logging.debug("Repo configs changed, resetting caches")
        reset_distro_caches()
    return repo_config


def get_kupfer_repo_names(local) -> list[str]:
    configs = get_repo_config()
    results = []
    for repo, repo_config in configs.repos.items():
        if not local and repo_config.local_only:
            continue
        results.append(repo)
    return results


def get_RepoInfo(
    arch: Arch, repo_config: AbstrRepoConfig, default_url: Optional[str]
) -> RepoInfo:
    url = repo_config.remote_url or default_url
    if isinstance(url, dict):
        if arch not in url and not default_url:
            raise Exception(
                f"Invalid repo config: Architecture {arch} not in remote_url mapping: {url}"
            )
        url = url.get(arch, default_url)
    assert url
    return RepoInfo(
        url_template=get_kupfer_url(url),
        options=repo_config.get("options", None) or {},
    )


def get_base_distro(
    arch: Arch,
    scan: bool = False,
    unsigned: bool = True,
    cache_db: bool = True,
) -> RemoteDistro:
    base_distros = get_repo_config().base_distros
    if base_distros is None or arch not in base_distros:
        base_distros = REPOS_CONFIG_DEFAULT.base_distros
        assert base_distros
    distro_config: BaseDistro
    distro_config = base_distros.get(arch)  # type: ignore[assignment]
    repos = {}
    for repo, repo_config in distro_config.repos.items():
        if unsigned:
            repo_config["options"] = (
                repo_config.get("options", None) or {}
            ) | {"SigLevel": "Never"}
        repos[repo] = get_RepoInfo(
            arch, repo_config, default_url=distro_config.remote_url
        )

    distro = RemoteDistro(arch=arch, repo_infos=repos, scan=False)
    if cache_db:
        for r in distro.repos.values():
            assert isinstance(r, RemoteRepo)
            r.cache_repo_db = True
    if scan:
        distro.scan()
    return distro


def get_kupfer_distro(
    arch: Arch,
    location: DistroLocation,
    scan: bool = False,
    cache_db: bool = True,
) -> Distro:
    global _kupfer_https, _kupfer_local, _kupfer_local_chroots
    cls: type[Distro]
    cache: Mapping[str, Distro]
    repo_config = get_repo_config()
    remote = False
    if location == DistroLocation.REMOTE:
        remote = True
        cache = _kupfer_https
        default_url = repo_config.remote_url or KUPFER_HTTPS
        repos = {
            repo: get_RepoInfo(arch, conf, default_url)
            for repo, conf in repo_config.repos.items()
            if not conf.local_only
        }
        cls = RemoteDistro
    elif location in [DistroLocation.CHROOT, DistroLocation.LOCAL]:
        if location == DistroLocation.CHROOT:
            cache = _kupfer_local_chroots
            pkgdir = CHROOT_PATHS["packages"]
        else:
            assert location == DistroLocation.LOCAL
            cache = _kupfer_local
            pkgdir = config.get_path("packages")
        default_url = f"file://{pkgdir}/$arch/$repo"
        cls = LocalDistro
        repos = {}
        for name, repo in repo_config.repos.items():
            repo = repo.copy()
            repo.remote_url = default_url
            repos[name] = get_RepoInfo(arch, repo, default_url)
    else:
        raise Exception(f"Unknown distro location {location}")
    if cache is None:
        cache = {}
    assert arch
    assert isinstance(cache, dict)
    if arch not in cache or not cache[arch]:
        distro = cls(
            arch=arch,
            repo_infos=repos,
            scan=False,
        )
        assert isinstance(distro, (LocalDistro, RemoteDistro))
        cache[arch] = distro
        if remote and cache_db:
            assert isinstance(distro, RemoteDistro)
            for r in distro.repos.values():
                r.cache_repo_db = True
        if scan:
            distro.scan()
        return distro
    item: Distro = cache[arch]
    if scan and not item.is_scanned():
        item.scan()
    return item


def get_kupfer_https(
    arch: Arch, scan: bool = False, cache_db: bool = True
) -> RemoteDistro:
    d = get_kupfer_distro(
        arch, location=DistroLocation.REMOTE, scan=scan, cache_db=cache_db
    )
    assert isinstance(d, RemoteDistro)
    return d


def get_kupfer_local(
    arch: Optional[Arch] = None, scan: bool = False, in_chroot: bool = True
) -> LocalDistro:
    arch = arch or config.runtime.arch
    assert arch
    location = DistroLocation.CHROOT if in_chroot else DistroLocation.LOCAL
    d = get_kupfer_distro(arch, location=location, scan=scan)
    assert isinstance(d, LocalDistro)
    return d
