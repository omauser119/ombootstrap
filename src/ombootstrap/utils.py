import atexit
import click
import datetime
import grp
import hashlib
import logging
import os
import pwd
import requests
import subprocess
import tarfile

from dateutil.parser import parse as parsedate
from shutil import which
from typing import Any, Generator, IO, Optional, Union, Sequence

from .exec.cmd import run_cmd, run_root_cmd

_programs_available = dict[str, bool]()


def programs_available(
    programs: Union[str, Sequence[str]], lazy: bool = True
) -> bool:
    global _programs_available
    if type(programs) is str:
        programs = [programs]
    for program in programs:
        if program not in _programs_available or not lazy:
            avail = bool(which(program))
            _programs_available[program] = avail
        if not _programs_available[program]:
            return False
    return True


def umount(dest: str, lazy=False) -> subprocess.CompletedProcess:
    return run_root_cmd(
        [
            "umount",
            "-c" + ("l" if lazy else ""),
            dest,
        ],
        capture_output=True,
    )


def mount(
    src: str,
    dest: str,
    options: list[str] = ["bind"],
    fs_type: Optional[str] = None,
    register_unmount=True,
) -> subprocess.CompletedProcess:
    opts = []
    for opt in options:
        opts += ["-o", opt]

    if fs_type:
        opts += ["-t", fs_type]

    result = run_root_cmd(
        ["mount"]
        + opts
        + [
            src,
            dest,
        ],
        capture_output=False,
    )
    if result.returncode == 0 and register_unmount:
        atexit.register(umount, dest)
    return result


def check_findmnt(path: str) -> subprocess.CompletedProcess:
    result = run_root_cmd(
        [
            "findmnt",
            "-n",
            "-o",
            "source",
            path,
        ],
        capture_output=True,
    )
    return result.stdout.decode().strip()


def git(
    cmd: list[str],
    dir: Optional[str] = None,
    use_git_dir: bool = False,
    git_dir: str = "./.git",
    capture_output=False,
    user: Optional[str] = None,
) -> subprocess.CompletedProcess:
    dirarg = [f"--git-dir={git_dir}"] if use_git_dir else []
    result = run_cmd(
        ["git", *dirarg] + cmd,
        cwd=dir,
        capture_output=capture_output,
        switch_user=user,
    )
    assert isinstance(result, subprocess.CompletedProcess)
    return result


def git_get_branch(path, use_git_dir: bool = True, git_dir="./.git") -> str:
    result = git(
        ["branch", "--show-current"],
        dir=path,
        use_git_dir=True,
        git_dir=git_dir,
        capture_output=True,
    )
    if result.returncode:
        raise Exception(
            f"Error getting git branch for {path}: {result.stderr}"
        )
    return result.stdout.decode().strip()


def log_or_exception(
    raise_exception: bool,
    msg: str,
    exc_class=Exception,
    log_level=logging.WARNING,
):
    if raise_exception:
        raise exc_class(msg)
    else:
        logging.log(log_level, msg)


def get_user_name(uid: Union[str, int]) -> str:
    if isinstance(uid, int) or uid.isnumeric():
        return pwd.getpwuid(int(uid)).pw_name
    return uid


def get_group_name(gid: Union[str, int]) -> str:
    if isinstance(gid, int) or gid.isnumeric():
        return grp.getgrgid(int(gid)).gr_name
    return gid


def get_uid(user: Union[int, str]) -> int:
    if isinstance(user, int) or user.isnumeric():
        return int(user)
    return pwd.getpwnam(user).pw_uid


def get_gid(group: Union[int, str]) -> int:
    if isinstance(group, int) or group.isnumeric():
        return int(group)
    return grp.getgrnam(group).gr_gid


def read_files_from_tar(
    tar_file: str, files: Sequence[str]
) -> Generator[tuple[str, IO], None, None]:
    assert os.path.exists(tar_file)
    with tarfile.open(tar_file) as index:
        for path in files:
            fd = index.extractfile(index.getmember(path))
            assert fd
            yield path, fd


def download_file(path: str, url: str, update: bool = True):
    """Download a file over http[s]. With `update`, tries to use mtime timestamps to download only changed files."""
    url_time = None
    if os.path.exists(path) and update:
        headers = requests.head(url).headers
        file_size = os.path.getsize(path)
        missing = [
            i for i in ["Content-Length", "last-modified"] if i not in headers
        ]
        if missing:
            logging.debug(f"Headers not specified: {missing}")
        if (
            "Content-Length" in headers
            and int(headers["Content-Length"]) != file_size
        ):
            logging.debug(
                f"{path} size differs: local: {file_size}, http: {headers['Content-Length']}"
            )
        elif "last-modified" in headers:
            url_time = parsedate(headers["last-modified"]).astimezone()
            file_time = datetime.datetime.fromtimestamp(
                os.path.getmtime(path)
            ).astimezone()
            if url_time == file_time:
                logging.debug(f"{path} seems already up to date")
                return False
    user_agent = {
        "User-agent": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:46.0) Gecko/20100101 Firefox/46.0"
    }
    download = requests.get(url, headers=user_agent)
    with open(path, "wb") as fd:
        for chunk in download.iter_content(4096):
            fd.write(chunk)
    if "last-modified" in download.headers:
        url_time = parsedate(download.headers["last-modified"]).astimezone()
        os.utime(
            path, (datetime.datetime.now().timestamp(), url_time.timestamp())
        )
    logging.debug(f"{path} downloaded!")
    return True


# stackoverflow magic from https://stackoverflow.com/a/44873382
def sha256sum(filename):
    h = hashlib.sha256()
    b = bytearray(128 * 1024)
    mv = memoryview(b)
    with open(filename, "rb", buffering=0) as f:
        while n := f.readinto(mv):
            h.update(mv[:n])
    return h.hexdigest()


def ellipsize(
    s: str,
    length: int = 25,
    padding: Optional[str] = None,
    ellipsis: str = "...",
    rjust: bool = False,
):
    """
    Ellipsize `s`, shortening it to `(length - len(ellipsis))` and appending `ellipsis` if `s` is longer than `length`.
    If `padding` is non-empty and `s` is shorter than length, `s` is padded with `padding` until it's `length` long.
    """
    if len(s) > length:
        return s[: length - len(ellipsis)] + ellipsis
    if not padding:
        return s
    pad = s.rjust if rjust else s.ljust
    return pad(length, padding)


def colors_supported(force_colors: Optional[bool] = None) -> bool:
    "If force_colors is None, returns isatty(stdout)"
    # stdout is fd 1
    return force_colors if force_colors is not None else os.isatty(1)


def color_str(s: str, use_colors: Optional[bool] = None, **kwargs) -> str:
    if colors_supported(use_colors):
        return click.style(s, **kwargs)
    return s


def color_green(s: str, **kwargs):
    return color_str(s, fg="bright_green", **kwargs)


def color_bold(s: str, **kwargs):
    return color_str(s, bold=True, **kwargs)


def color_mark_selected(
    item: str,
    profile_name: str,
    inherited_from: Optional[str] = None,
    msg_fmt: str = 'Currently selected by profile "%s"%s',
    msg_item_colors: dict[str, Any] = dict(bold=True, fg="bright_green"),
    marker: str = ">>> ",
    marker_config: dict[str, Any] = dict(bold=True, fg="bright_green"),
    split_on: str = "\n",
    suffix: str = "\n\n",
    use_colors: Optional[bool] = None,
) -> str:
    def bold(s: str, _bold=True, **kwargs):
        return color_bold(s, use_colors=use_colors, **kwargs)

    def green(s: str, **kwargs):
        return color_green(s, use_colors=use_colors, **kwargs)

    marker_full = color_str(marker, use_colors=use_colors, **marker_config)

    msg_items = [
        color_str(profile_name, use_colors=use_colors, **msg_item_colors),
        "",
    ]
    if inherited_from and inherited_from != profile_name:
        msg_items[1] = "".join(
            [
                bold(' (inherited from profile "'),
                green(inherited_from, bold=True),
                bold('")'),
            ]
        )
    output = f"{item}{suffix}{msg_fmt % tuple(msg_items)}"
    return "\n".join([(marker_full + o) for o in output.split(split_on)])
