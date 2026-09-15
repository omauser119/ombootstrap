from copy import deepcopy
import logging
import os
import tarfile

from typing import Generic, TypeVar

from ombootstrap.config.state import config
from ombootstrap.exec.file import get_temp_dir
from ombootstrap.utils import download_file

from .package import BinaryPackage, LocalPackage, RemotePackage

BinaryPackageType = TypeVar("BinaryPackageType", bound=BinaryPackage)


def resolve_url(url_template, repo_name: str, arch: str):
    result = url_template
    for template, replacement in {"$repo": repo_name, "$arch": arch}.items():
        result = result.replace(template, replacement)
    return result


class RepoInfo:
    options: dict[str, str]
    url_template: str

    def __init__(self, url_template: str, options: dict[str, str] = {}):
        self.url_template = url_template
        self.options = {} | options


class Repo(RepoInfo, Generic[BinaryPackageType]):
    name: str
    resolved_url: str
    arch: str
    packages: dict[str, BinaryPackageType]
    remote: bool
    scanned: bool = False

    def resolve_url(self) -> str:
        return resolve_url(
            self.url_template, repo_name=self.name, arch=self.arch
        )

    def scan(self, allow_failure: bool = False) -> bool:
        failed = 0
        parsed = 0
        self.resolved_url = self.resolve_url()
        self.remote = not self.resolved_url.startswith("file://")
        try:
            path = self.acquire_db_file()
            index = tarfile.open(path)
        except Exception as ex:
            if not allow_failure:
                raise ex
            logging.error(
                f"Repo {self.name}, {self.arch}: Error acquiring repo DB: {ex!r}"
            )
            return False
        logging.debug(f"Parsing repo file at {path}")
        for node in index.getmembers():
            if os.path.basename(node.name) == "desc":
                pkgname = os.path.dirname(node.name)
                logging.debug(f"Parsing desc file for {pkgname}")
                fd = index.extractfile(node)
                assert fd
                try:
                    contents = fd.read().decode()
                    pkg = self._parse_desc(contents)
                except Exception as ex:
                    if not allow_failure:
                        raise ex
                    logging.error(
                        f'Repo {self.name}, {self.arch}: Error parsing desc for "{pkgname}": {ex!r}'
                    )
                    failed += 1
                    continue
                self.packages[pkg.name] = pkg
                parsed += 1
        index.close()
        if failed:
            logging.warning(
                f"Repo {self.name}, {self.arch}: skipped {failed} damaged descriptions; "
                f"keeping {parsed} valid packages"
            )
            if not parsed:
                return False
        self.scanned = True
        return True

    def _parse_desc(
        self, desc_text: str
    ):  # can't annotate the type properly :(
        raise NotImplementedError()

    def parse_desc(self, desc_text: str) -> BinaryPackageType:
        return self._parse_desc(desc_text)

    def acquire_db_file(self) -> str:
        raise NotImplementedError

    def __init__(
        self, name: str, url_template: str, arch: str, options={}, scan=False
    ):
        self.packages = {}
        self.name = name
        self.url_template = url_template
        self.arch = arch
        self.options = deepcopy(options)
        if scan:
            self.scan()

    def __repr__(self):
        return f"<Repo:{self.name}:{self.arch}:{self.url_template}>"

    def config_snippet(self) -> str:
        options = {"Server": self.url_template} | self.options
        return ("[%s]\n" % self.name) + "\n".join(
            [f"{key} = {value}" for key, value in options.items()]
        )

    def get_RepoInfo(self):
        return RepoInfo(url_template=self.url_template, options=self.options)


class LocalRepo(Repo[LocalPackage]):
    def _parse_desc(self, desc_text: str) -> LocalPackage:
        return LocalPackage.parse_desc(
            desc_text, resolved_repo_url=self.resolved_url
        )

    def acquire_db_file(self) -> str:
        return f"{self.resolved_url}/{self.name}.db".split("file://")[1]


class RemoteRepo(Repo[RemotePackage]):
    cache_repo_db: bool

    def __init__(self, *kargs, cache_repo_db: bool = False, **kwargs):
        self.cache_repo_db = cache_repo_db
        super().__init__(*kargs, **kwargs)

    def _parse_desc(self, desc_text: str) -> RemotePackage:
        return RemotePackage.parse_desc(
            desc_text, resolved_repo_url=self.resolved_url
        )

    def acquire_db_file(self) -> str:
        uri = f"{self.resolved_url}/{self.name}.db"
        logging.info(f"Downloading repo file from {uri}")
        assert self.arch and self.name, (
            f"repo has incomplete information: {self.name=}, {self.arch=}"
        )
        path = (
            get_temp_dir()
            if not self.cache_repo_db
            else os.path.join(config.get_path("pacman"), "repo_dbs", self.arch)
        )
        os.makedirs(path, exist_ok=True)
        repo_file = f"{path}/{self.name}.tar.gz"
        download_file(repo_file, uri, update=True)
        return repo_file
