import click
import json
import logging
import os

from functools import wraps
from glob import glob
from typing import Callable, Iterable, Optional, TextIO, Unpack
from typing_extensions import TypedDict

from ombootstrap.config.state import config
from ombootstrap.constants import (
    Arch,
    ARCHES,
    SRCINFO_FILE,
    SRCINFO_INITIALISED_FILE,
    SRCINFO_METADATA_FILE,
    SRCINFO_TARBALL_FILE,
    SRCINFO_TARBALL_URL,
)
from ombootstrap.exec.cmd import run_cmd, shell_quote, CompletedProcess
from ombootstrap.exec.file import get_temp_dir, makedir, remove_file
from ombootstrap.devices.device import get_profile_device
from ombootstrap.distro.distro import (
    get_kupfer_local,
    get_kupfer_url,
    get_kupfer_repo_names,
)
from ombootstrap.distro.package import LocalPackage
from ombootstrap.net.ssh import run_ssh_command, scp_put_files
from ombootstrap.utils import download_file, git, sha256sum
from ombootstrap.version.cli import _check_kbs_version
from ombootstrap.wrapper import check_programs_wrap, enforce_wrap

from .build import (
    build_packages_by_paths,
    get_build_levels_from_paths,
    init_prebuilts,
)
from .pkgbuild import (
    discover_pkgbuilds,
    filter_pkgbuilds,
    get_pkgbuild_dirs,
    init_pkgbuilds,
)

SRCINFO_CACHE_FILES = [
    SRCINFO_FILE,
    SRCINFO_INITIALISED_FILE,
    SRCINFO_METADATA_FILE,
]


class BuildCliArgs(TypedDict):
    paths: list
    force: bool
    arch: Optional[Arch]
    rebuild_dependants: bool
    no_download: bool
    allow_extra_arches: bool


def cli_build_cmd(
    f: Callable[[Unpack[BuildCliArgs]], int | None],
) -> Callable[[Unpack[BuildCliArgs]], int | None]:
    @click.option(
        "--force",
        is_flag=True,
        default=False,
        help="Rebuild even if package is already built",
    )
    @click.option(
        "--arch",
        default=None,
        required=False,
        type=click.Choice(ARCHES),
        help="The CPU architecture to build for",
    )
    @click.option(
        "--rebuild-dependants",
        is_flag=True,
        default=False,
        help="Rebuild packages that depend on packages that will be [re]built",
    )
    @click.option(
        "--no-download",
        is_flag=True,
        default=False,
        help="Don't try downloading packages from online repos before building",
    )
    @click.option(
        "--allow-extra-arches/--disallow-extra-arches",
        is_flag=True,
        default=True,
        show_default=True,
        help="Allow building PKGs for other stated arches if the PKGBUILD sets _arches=all",
    )
    @click.argument("paths", nargs=-1)
    @wraps(f)
    def wrapped(*args, **kwargs) -> int | None:
        return f(*args, **kwargs)

    return wrapped


def build(
    paths: Iterable[str],
    force: bool,
    arch: Optional[Arch] = None,
    rebuild_dependants: bool = False,
    try_download: bool = False,
    allow_override_for_arches_all: bool = True,
):
    config.enforce_config_loaded()
    enforce_wrap()
    arch = arch or get_profile_device(hint_or_set_arch=True).arch

    if arch not in ARCHES:
        raise Exception(
            f'Unknown architecture "{arch}". Choices: {", ".join(ARCHES)}'
        )

    _check_kbs_version(init_pkgbuilds=True)
    return build_packages_by_paths(
        paths,
        arch,
        force=force,
        rebuild_dependants=rebuild_dependants,
        try_download=try_download,
        enable_crosscompile=config.file.build.crosscompile,
        enable_crossdirect=config.file.build.crossdirect,
        enable_ccache=config.file.build.ccache,
        clean_chroot=config.file.build.clean_mode,
        allow_override_for_arches_all=allow_override_for_arches_all,
    )


def init_pkgbuild_caches(
    clean_src_dirs: bool = True, remote_branch: Optional[str] = None
):
    if config.file.pkgbuilds.git_repo == "bundled":
        # Kupfer's precomputed archive does not describe our mobile recipes.
        # discover_pkgbuilds generates and caches fresh local metadata on demand.
        logging.info("Using local Ombootstrap recipe metadata")
        return

    def read_srcinitialised_checksum(src_initialised):
        with open(src_initialised) as fd:
            d = json.load(fd)
        if isinstance(d, dict):
            return d.get("PKGBUILD", "!!!ERROR!!!")
        raise Exception("JSON content not a dictionary!")

    # get_kupfer_url() resolves repo branch variable in url
    url = get_kupfer_url(url=SRCINFO_TARBALL_URL, branch=remote_branch)
    cachetar = os.path.join(config.get_path("packages"), SRCINFO_TARBALL_FILE)
    makedir(os.path.dirname(cachetar))
    logging.info(
        f"Updating PKGBUILD caches from {url}"
        + (", pruning outdated src/ directories" if clean_src_dirs else "")
    )
    updated = download_file(cachetar, url)
    logging.info(
        "Cache tarball was "
        + ("downloaded successfully" if updated else "already up to date")
    )
    tmpdir = get_temp_dir()
    logging.debug(f"Extracting {cachetar} to {tmpdir}")
    res = run_cmd(["tar", "xf", cachetar], cwd=tmpdir)
    assert isinstance(res, CompletedProcess)
    if res.returncode:
        raise Exception(
            f"failed to extract srcinfo cache archive '{cachetar}'"
        )
    pkgbuild_dirs = get_pkgbuild_dirs()
    for pkg in pkgbuild_dirs:
        logging.info(f"{pkg}: analyzing cache")
        pkgdir = os.path.join(config.get_path("pkgbuilds"), pkg)
        srcdir = os.path.join(pkgdir, "src")
        src_initialised = os.path.join(pkgdir, SRCINFO_INITIALISED_FILE)
        cachedir = os.path.join(tmpdir, pkg)
        pkgbuild_checksum = sha256sum(os.path.join(pkgdir, "PKGBUILD"))
        copy_files: set[str] = set(SRCINFO_CACHE_FILES)
        if os.path.exists(src_initialised):
            try:
                if (
                    read_srcinitialised_checksum(src_initialised)
                    == pkgbuild_checksum
                ):
                    copy_files.remove(SRCINFO_INITIALISED_FILE)
                    for f in copy_files.copy():
                        fpath = os.path.join(pkgdir, f)
                        if os.path.exists(fpath):
                            copy_files.remove(f)
                    if not copy_files:
                        logging.info(
                            f"{pkg}: SRCINFO cache already up to date"
                        )
                        continue
            except Exception as ex:
                logging.warning(
                    f"{pkg}: Something went wrong parsing {SRCINFO_INITIALISED_FILE}, treating as outdated!:\n{ex}"
                )
            if clean_src_dirs and os.path.exists(srcdir):
                logging.info(f"{pkg}: outdated src/ detected, removing")
                remove_file(srcdir, recursive=True)
                remove_file(src_initialised)
        if not os.path.exists(cachedir):
            logging.info(f"{pkg}: not found in remote repo cache, skipping")
            continue
        cache_initialised = os.path.join(cachedir, SRCINFO_INITIALISED_FILE)
        try:
            if (
                read_srcinitialised_checksum(cache_initialised)
                != pkgbuild_checksum
            ):
                logging.info(
                    f"{pkg}: PKGBUILD checksum differs from remote repo cache, skipping"
                )
                continue
        except Exception as ex:
            logging.warning(
                f"{pkg}: Failed to parse the remote repo's cached {SRCINFO_INITIALISED_FILE}, skipping!:\n{ex}"
            )
            continue
        if not copy_files:
            continue
        logging.info(f"{pkg}: Copying srcinfo cache from remote repo")
        logging.debug(f"{pkg}: copying {copy_files}")
        copy_files_list = [
            shell_quote(os.path.join(cachedir, f)) for f in copy_files
        ]
        res = run_cmd(f"cp {' '.join(copy_files_list)} {shell_quote(pkgdir)}/")
        assert isinstance(res, CompletedProcess)
        if res.returncode:
            raise Exception(
                f"{pkg}: failed to copy cache contents from {cachedir}"
            )


non_interactive_flag = click.option("--non-interactive", is_flag=True)
init_caches_flag = click.option(
    "--init-caches/--no-init-caches",
    is_flag=True,
    default=True,
    show_default=True,
    help="Fill PKGBUILDs caches from HTTPS repo where checksums match",
)
remove_outdated_src_flag = click.option(
    "--clean-src-dirs/--no-clean-src-dirs",
    is_flag=True,
    default=True,
    show_default=True,
    help="Remove outdated src/ directories to avoid problems",
)
switch_branch_flag = click.option(
    "--switch-branch",
    is_flag=True,
    help="Force the branch to be corrected even in non-interactive mode",
)
discard_changes_flag = click.option(
    "--discard-changes",
    is_flag=True,
    help="When switching branches, discard any locally changed conflicting files",
)


@click.group(name="packages")
def cmd_packages():
    """Build and manage packages and PKGBUILDs"""


@cmd_packages.command(name="update")
@non_interactive_flag
@init_caches_flag
@switch_branch_flag
@discard_changes_flag
@remove_outdated_src_flag
def cmd_update(
    non_interactive: bool = False,
    init_caches: bool = False,
    clean_src_dirs: bool = True,
    switch_branch: bool = False,
    discard_changes: bool = False,
):
    """Update PKGBUILDs git repo"""
    enforce_wrap()
    init_pkgbuilds(
        interactive=not non_interactive,
        lazy=False,
        update=True,
        switch_branch=switch_branch,
        discard_changes=discard_changes,
    )
    _check_kbs_version(init_pkgbuilds=False)
    if init_caches:
        init_pkgbuild_caches(clean_src_dirs=clean_src_dirs)
    logging.info("Refreshing outdated SRCINFO caches")
    discover_pkgbuilds(lazy=False)


@cmd_packages.command(name="init")
@non_interactive_flag
@init_caches_flag
@switch_branch_flag
@discard_changes_flag
@remove_outdated_src_flag
@click.option(
    "-u", "--update", is_flag=True, help="Use git pull to update the PKGBUILDs"
)
def cmd_init(
    non_interactive: bool = False,
    init_caches: bool = True,
    clean_src_dirs: bool = True,
    switch_branch: bool = False,
    discard_changes: bool = False,
    update: bool = False,
):
    "Ensure PKGBUILDs git repo is checked out locally"
    init_pkgbuilds(
        interactive=not non_interactive,
        lazy=False,
        update=update,
        switch_branch=switch_branch,
        discard_changes=discard_changes,
    )
    _check_kbs_version(init_pkgbuilds=False)
    if init_caches:
        init_pkgbuild_caches(clean_src_dirs=clean_src_dirs)
    for arch in ARCHES:
        init_prebuilts(arch)


@cmd_packages.command(name="build")
@cli_build_cmd
def cmd_build(
    paths: list[str],
    force=False,
    arch: Optional[Arch] = None,
    rebuild_dependants: bool = False,
    no_download: bool = False,
    allow_extra_arches: bool = True,
):
    """
    Build packages (and dependencies) by paths as required.

    The paths are specified relative to the PKGBUILDs dir, eg. "cross/crossdirect".

    Multiple paths may be specified as separate arguments.

    Packages that aren't built already will be downloaded from HTTPS repos unless --no-download is passed,
    if an exact version match exists on the server.
    """
    build(
        paths,
        force,
        arch=arch,
        rebuild_dependants=rebuild_dependants,
        try_download=not no_download,
        allow_override_for_arches_all=allow_extra_arches,
    )


@cmd_packages.command(name="sideload")
@click.argument("paths", nargs=-1)
@click.option(
    "--arch",
    default=None,
    required=False,
    type=click.Choice(ARCHES),
    help="The CPU architecture to build for",
)
@click.option(
    "-B",
    "--no-build",
    is_flag=True,
    default=False,
    help="Don't try to build packages, just copy and install",
)
def cmd_sideload(
    paths: Iterable[str], arch: Optional[Arch] = None, no_build: bool = False
):
    """Build packages, copy to the device via SSH and install them"""
    if not paths:
        raise Exception("No packages specified")
    arch = arch or get_profile_device(hint_or_set_arch=True).arch
    if not no_build:
        build(paths, False, arch=arch, try_download=True)
    repo: dict[str, LocalPackage] = get_kupfer_local(
        arch=arch, scan=True, in_chroot=False
    ).get_packages()
    files = [
        pkg.resolved_url.split("file://")[1]
        for pkg in repo.values()
        if pkg.resolved_url and pkg.name in paths
    ]
    logging.debug(f"Sideload: Found package files: {files}")
    if not files:
        logging.fatal("No packages matched")
        return
    scp_put_files(files, "/tmp").check_returncode()
    run_ssh_command(
        [
            "sudo",
            "pacman",
            "-U",
            *[os.path.join("/tmp", os.path.basename(file)) for file in files],
            "--noconfirm",
            "'--overwrite=\\*'",
        ],
        alloc_tty=True,
    ).check_returncode()


CLEAN_LOCATIONS = ["src", "pkg", *SRCINFO_CACHE_FILES]


@cmd_packages.command(name="clean")
@click.option(
    "-f",
    "--force",
    is_flag=True,
    default=False,
    help="Don't prompt for confirmation",
)
@click.option(
    "-n",
    "--noop",
    is_flag=True,
    default=False,
    help="Print what would be removed but dont execute",
)
@click.argument(
    "what", type=click.Choice(["all", "git", *CLEAN_LOCATIONS]), nargs=-1
)
def cmd_clean(
    what: Iterable[str] = ["all"], force: bool = False, noop: bool = False
):
    """
    Clean temporary files from PKGBUILDs

    Specifying no location defaults to the special value 'all', meaning all regular locations.

    There is also the special value 'git' which uses git to clean everything.
    Be careful with it, as it means re-downloading sources for your packages.
    """
    if noop:
        logging.debug("Running in noop mode!")
    if force:
        logging.debug("Running in FORCE mode!")
    what = what or ["all"]
    logging.debug(f"Clearing {what} from PKGBUILDs")
    pkgbuilds = config.get_path("pkgbuilds")
    if "git" in what:
        check_programs_wrap(["git"])
        warning = "Really reset PKGBUILDs to git state completely?\nThis will erase any untracked changes to your PKGBUILDs directory."
        if not (noop or force or click.confirm(warning)):
            return
        result = git(
            [
                "clean",
                "-dffX" + ("n" if noop else ""),
            ]
            + get_kupfer_repo_names(local=True),
            dir=pkgbuilds,
        )
        if result.returncode != 0:
            logging.fatal("Failed to git clean")
            exit(1)
    else:
        if "all" in what:
            what = CLEAN_LOCATIONS
        what = set(what)
        dirs = []
        for loc in CLEAN_LOCATIONS:
            if loc in what:
                logging.info(f"gathering {loc} instances")
                dirs += glob(os.path.join(pkgbuilds, "*", "*", loc))

        dir_lines = "\n".join(dirs)
        verb = "Would remove" if noop else "Removing"
        logging.info(verb + ":\n" + dir_lines)

        if not (noop or force):
            if not click.confirm("Really remove all of these?", default=True):
                return

        if not noop:
            for dir in dirs:
                remove_file(dir, recursive=True)


@cmd_packages.command(name="list")
def cmd_list():
    "List information about available source packages (PKGBUILDs)"
    pkgdir = os.path.join(
        config.get_path("pkgbuilds"), get_kupfer_repo_names(local=False)[0]
    )
    if not os.path.exists(pkgdir):
        raise Exception(
            f"PKGBUILDs seem not to be initialised yet: {pkgdir} doesn't exist!\n"
            f"Try running `ombs packages init` first!"
        )
    check_programs_wrap(["git", "makepkg", "pacman"])
    _check_kbs_version(init_pkgbuilds=False)
    packages = discover_pkgbuilds()
    logging.info(f"Done! {len(packages)} Pkgbuilds:")
    for name in sorted(packages.keys()):
        p = packages[name]
        print(
            f"name: {p.name}; ver: {p.version}; mode: {p.mode}; crossdirect: {p.crossdirect} provides: {p.provides}; replaces: {p.replaces};"
            f"local_depends: {p.local_depends}; depends: {p.depends}"
        )


@cmd_packages.command(name="plan")
@cli_build_cmd
@click.option(
    "-o",
    "--output-file",
    required=False,
    type=click.File(mode="w"),
    help="Write output to specified file instead of stdout",
)
@click.option(
    "--exclude-any-pkgs",
    is_flag=True,
    help="Exclude packages with 'any' architecture",
)
def cmd_dump_build_plan(
    paths: list[str],
    *,
    output_file: Optional[TextIO] = None,
    quiet: bool = False,
    force=False,
    arch: Optional[Arch] = None,
    rebuild_dependants: bool = False,
    no_download: bool = False,
    allow_extra_arches: bool = True,
    exclude_any_pkgs: bool = False,
):
    """
    Dump build plan as JSON
    """
    config.enforce_config_loaded()
    enforce_wrap()
    arch = arch or get_profile_device(hint_or_set_arch=True).arch

    if arch not in ARCHES:
        raise Exception(
            f'Unknown architecture "{arch}". Choices: {", ".join(ARCHES)}'
        )
    _check_kbs_version(init_pkgbuilds=True)
    levels = get_build_levels_from_paths(
        paths=paths or ["all"],
        arch=arch,
        force=force,
        rebuild_dependants=rebuild_dependants,
        try_download=not no_download,
        allow_override_for_arches_all=allow_extra_arches,
    )
    data: list[dict[str, int | list[dict[str, str]]]] = []
    for i, _level in enumerate(levels):
        level = []
        for pkg in _level:
            if not set(pkg.arches).intersection(("any", arch)):
                logging.warning(
                    f"Package {pkg.name!r} doesn't have {arch} in {pkg.arches=}; check {pkg.path}/PKGBUILD"
                )
            if exclude_any_pkgs and "any" in pkg.arches:
                logging.warning(
                    f"Ignoring arches='any' PKGBUILD {pkg.path!r} in level {i} as requested"
                )
                continue
            level.append(pkg)

        if not level:
            logging.warning(f"Ignoring empty level {i}")
            continue

        sorted_level = sorted(level, key=lambda p: p.path)
        pkgs: list[dict[str, str]] = [
            {
                "name": p.name,
                "path": p.path,
                "arch": (
                    list(set(p.arches).intersection(("any", arch))) or [arch]
                )[0],
            }
            for p in sorted_level
        ]
        l_data: dict[str, int | list[dict[str, str]]] = {
            "level": len(data) + 1,
            "pkgs": pkgs,
        }
        data.append(l_data)

    js = json.dumps({"arch": arch, "levels": data}, indent=2)
    if not quiet:
        print("\n\n" + js)
    if not output_file:
        return
    output_file.write(js)
    logging.info(f"{len(js)} bytes successfully written to {output_file.name}")


@cmd_packages.command(name="check")
@click.option("--ci-mode", "--ci", is_flag=True, default=False)
@click.argument("paths", nargs=-1)
def cmd_check(paths: list[str], ci_mode: bool = False):
    """Check that specified PKGBUILDs are formatted correctly"""
    config.enforce_config_loaded()
    check_programs_wrap(["makepkg", "git"])
    _check_kbs_version(init_pkgbuilds=False, ci_mode=ci_mode)

    def check_quoteworthy(s: str) -> bool:
        quoteworthy = ['"', "'", "$", " ", ";", "&", "<", ">", "*", "?"]
        for symbol in quoteworthy:
            if symbol in s:
                return True
        return False

    paths = list(paths) or ["all"]
    packages = filter_pkgbuilds(paths, allow_empty_results=False)

    for package in packages:
        name = package.name

        is_git_package = False
        if name.endswith("-git"):
            is_git_package = True

        required_arches = ""
        provided_arches: list[str] = []

        mode_key = "_mode"
        nodeps_key = "_nodeps"
        crossdirect_key = "_crossdirect"
        pkgbase_key = "pkgbase"
        pkgname_key = "pkgname"
        arches_key = "_arches"
        arch_key = "arch"
        commit_key = "_commit"
        source_key = "source"
        sha256sums_key = "sha256sums"
        required = {
            mode_key: True,
            nodeps_key: False,
            crossdirect_key: False,
            pkgbase_key: False,
            pkgname_key: True,
            "pkgdesc": False,
            "pkgver": True,
            "pkgrel": True,
            arches_key: True,
            arch_key: True,
            "license": True,
            "url": False,
            "provides": is_git_package,
            "conflicts": False,
            "replaces": False,
            "depends": False,
            "optdepends": False,
            "makedepends": False,
            "backup": False,
            "install": False,
            "options": False,
            commit_key: is_git_package,
            source_key: False,
            sha256sums_key: False,
            "noextract": False,
        }
        pkgbuild_path = os.path.join(
            config.get_path("pkgbuilds"), package.path, "PKGBUILD"
        )
        with open(pkgbuild_path, "r") as file:
            content = file.read()
        if "\t" in content:
            logging.fatal(f"\\t is not allowed in {pkgbuild_path}")
            exit(1)
        lines = content.split("\n")
        if len(lines) == 0:
            logging.fatal(f"Empty {pkgbuild_path}")
            exit(1)
        line_index = 0
        key_index = 0
        hold_key = False
        key = ""
        while True:
            line = lines[line_index]

            if line.startswith("#"):
                line_index += 1
                continue

            if line.startswith("_") and line.split("=", 1)[0] not in [
                mode_key,
                nodeps_key,
                arches_key,
                commit_key,
            ]:
                line_index += 1
                continue

            formatted = True
            next_key = False
            next_line = False
            reason = ""

            if hold_key:
                next_line = True
            else:
                if key_index < len(required):
                    key = list(required)[key_index]
                    if line.startswith(key):
                        if key == pkgbase_key:
                            required[pkgname_key] = False
                        if key == source_key:
                            required[sha256sums_key] = True
                        next_key = True
                        next_line = True
                    elif key in required and not required[key]:
                        next_key = True

            if line == ")":
                hold_key = False
                next_key = True

            if key == arches_key:
                required_arches = line.split("=")[1]

            if line.endswith("=("):
                hold_key = True

            if line.startswith("    ") or line == ")":
                next_line = True

            if line.startswith("  ") and not line.startswith("    "):
                formatted = False
                reason = "Multiline variables should be indented with 4 spaces"

            if '"' in line and not check_quoteworthy(line):
                formatted = False
                reason = 'Found literal " although no special character was found in the line to justify the usage of a literal "'

            if "'" in line and '"' not in line:
                formatted = False
                reason = "Found literal ' although either a literal \" or no qoutes should be used"

            if (
                "=(" in line
                and " " in line
                and '"' not in line
                and not line.endswith("=(")
            ) or (hold_key and line.endswith(")")):
                formatted = False
                reason = (
                    "Multiple elements in a list need to be in separate lines"
                )

            if formatted and not next_key and not next_line:
                if key_index == len(required):
                    if lines[line_index] == "":
                        break
                    else:
                        formatted = False
                        reason = (
                            "Expected final emtpy line after all variables"
                        )
                else:
                    formatted = False
                    reason = f'Expected to find "{key}"'

            if not formatted:
                logging.fatal(
                    f'Formatting error in {pkgbuild_path}: Line {line_index + 1}: "{line}"'
                )
                if reason != "":
                    logging.fatal(reason)
                exit(1)

            if key == arch_key:
                if line.endswith(")"):
                    if line.startswith(f"{arch_key}=("):
                        check_arches_hint(
                            pkgbuild_path, required_arches, [line[6:-1]]
                        )
                    else:
                        check_arches_hint(
                            pkgbuild_path, required_arches, provided_arches
                        )
                elif line.startswith("    "):
                    provided_arches.append(line[4:])

            if next_key and not hold_key:
                key_index += 1
            if next_line:
                line_index += 1

        logging.info(f"{package.path} nicely formatted!")


def check_arches_hint(path: str, required: str, provided: list[str]):
    if required == "all":
        for arch in ARCHES:
            if arch not in provided:
                logging.warning(
                    f"Missing {arch} in arches list in {path}, because _arches hint is `all`"
                )
