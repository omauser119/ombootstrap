import logging
import os

from shutil import copyfileobj
from typing import Optional, Union
from urllib.request import urlopen

from ombootstrap.exec.file import get_temp_dir, makedir


class PackageInfo:
    name: str
    version: str


class BinaryPackage(PackageInfo):
    arch: str
    filename: str
    resolved_url: Optional[str]
    _desc: Optional[dict[str, Union[str, list[str]]]]

    def __init__(
        self,
        name: str,
        version: str,
        arch: str,
        filename: str,
        resolved_url: Optional[str] = None,
    ):
        self.name = name
        self.version = version
        self.arch = arch
        self.filename = filename
        self.resolved_url = resolved_url

    def __repr__(self):
        return f"{self.name}@{self.version}"

    @classmethod
    def parse_desc(clss, desc_str: str, resolved_repo_url=None):
        """Parses a desc file, returning a PackageInfo"""
        if "\x00" in desc_str:
            raise ValueError("Package description contains NUL bytes")
        desc: dict[str, Union[str, list[str]]] = {}
        for segment in f"\n{desc_str}".split("\n%"):
            if not segment.strip():
                continue
            key, separator, elements = segment.partition("%\n")
            if not separator:
                raise ValueError("Malformed package description field")
            key, elements = key.strip(), elements.strip()
            elements_split = elements.split("\n")
            desc[key] = (
                elements if len(elements_split) == 1 else elements_split
            )
        validated: dict[str, str] = {}
        for key in ["NAME", "VERSION", "ARCH", "FILENAME"]:
            assert key in desc
            value = desc[key]
            assert isinstance(value, str)
            validated[key] = value
        p = clss(
            name=validated["NAME"],
            version=validated["VERSION"],
            arch=validated["ARCH"],
            filename=validated["FILENAME"],
            resolved_url="/".join([resolved_repo_url, validated["FILENAME"]]),
        )
        p._desc = desc
        return p

    def acquire(self) -> str:
        raise NotImplementedError()


class LocalPackage(BinaryPackage):
    def acquire(self) -> str:
        assert (
            self.resolved_url
            and self.filename
            and self.filename in self.resolved_url
        )
        path = f"{self.resolved_url.split('file://')[1]}"
        assert os.path.exists(path) or print(path)
        return path


class RemotePackage(BinaryPackage):
    def acquire(self, dest_dir: Optional[str] = None) -> str:
        assert self.resolved_url and ".pkg.tar." in self.resolved_url
        url = f"{self.resolved_url}"
        assert url

        dest_dir = dest_dir or get_temp_dir()
        makedir(dest_dir)
        dest_file_path = os.path.join(dest_dir, self.filename)

        logging.info(f"Trying to download package {url}")
        with urlopen(url) as fsrc, open(dest_file_path, "wb") as fdst:
            copyfileobj(fsrc, fdst)
        logging.info(f"{self.filename} downloaded from repos")
        return dest_file_path
