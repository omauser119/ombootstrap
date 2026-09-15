import logging
import multiprocessing
import os
import shutil
import subprocess
import sys

from copy import deepcopy
from urllib.error import HTTPError
from typing import Iterable, Iterator, Optional

from ombootstrap.binfmt.binfmt import binfmt_is_registered, binfmt_register
from ombootstrap.constants import (
    CROSSDIRECT_PKGS,
    QEMU_BINFMT_PKGS,
    GCC_HOSTSPECS,
    ARCHES,
    Arch,
    CHROOT_PATHS,
    MAKEPKG_CMD,
)
from ombootstrap.config.state import config
from ombootstrap.exec.cmd import run_cmd, run_root_cmd
from ombootstrap.exec.file import makedir, remove_file, symlink
from ombootstrap.chroot.build import get_build_chroot, BuildChroot
from ombootstrap.distro.distro import (
    get_kupfer_https,
    get_base_distro,
    get_kupfer_local,
    get_kupfer_repo_names,
)
from ombootstrap.distro.package import RemotePackage, LocalPackage
from ombootstrap.distro.repo import LocalRepo
from ombootstrap.progressbar import BAR_PADDING, get_levels_bar
from ombootstrap.wrapper import check_programs_wrap, is_wrapped
from ombootstrap.utils import ellipsize, sha256sum

from .pkgbuild import (
    discover_pkgbuilds,
    filter_pkgbuilds,
    Pkgbase,
    Pkgbuild,
    SubPkgbuild,
)

# These packages are shipped by the Omarchy AArch64 release repository.
# They must never fall back to compiling the local recipes on the phone image.
PREBUILT_ONLY_PACKAGES = {"hyprland", "quickshell-git"}
# Filled by prefer_prebuilt_recipes() for every local recipe that has a
# matching archive in the same remote repository.
_PREBUILT_ONLY_BY_ARCH: dict[Arch, set[str]] = {}


def is_prebuilt_only(name: str, arch: Arch) -> bool:
    return arch == "aarch64" and (
        name in PREBUILT_ONLY_PACKAGES
        or name in _PREBUILT_ONLY_BY_ARCH.get(arch, set())
    )


pacman_cmd = [
    "pacman",
    "-Syuu",
    "--noconfirm",
    "--overwrite=*",
    "--needed",
]


def get_makepkg_env(arch: Optional[Arch] = None):
    # has to be a function because calls to `config` must be done after config file was read
    threads = config.file.build.threads or multiprocessing.cpu_count()
    # env = {key: val for key, val in os.environ.items() if not key.split('_', maxsplit=1)[0] in ['CI', 'GITLAB', 'FF']}
    env = {
        "LANG": "C",
        "CARGO_BUILD_JOBS": str(threads),
        "MAKEFLAGS": f"-j{threads}",
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    }
    native = config.runtime.arch
    assert native
    if arch and arch != native:
        env |= {"QEMU_LD_PREFIX": f"/usr/{GCC_HOSTSPECS[native][arch]}"}
    return env


def init_local_repo(repo: str, arch: Arch):
    repo_dir = os.path.join(config.get_package_dir(arch), repo)
    if not os.path.exists(repo_dir):
        logging.info(f'Creating local repo "{repo}" ({arch})')
        makedir(repo_dir)
    for ext in ["db", "files"]:
        filename_stripped = f"{repo}.{ext}"
        filename = f"{filename_stripped}.tar.xz"
        if not os.path.exists(os.path.join(repo_dir, filename)):
            logging.info(
                f'Initialising local repo {f"{ext} " if ext != "db" else ""}db for repo "{repo}" ({arch})'
            )
            result = run_cmd(
                [
                    "tar",
                    "-czf",
                    filename,
                    "-T",
                    "/dev/null",
                ],
                cwd=os.path.join(repo_dir),
            )
            assert isinstance(result, subprocess.CompletedProcess)
            if result.returncode != 0:
                raise Exception(f'Failed to create local repo "{repo}"')
        symlink_path = os.path.join(repo_dir, filename_stripped)
        if not os.path.islink(symlink_path):
            if os.path.exists(symlink_path):
                remove_file(symlink_path)
            symlink(filename, symlink_path)


def init_prebuilts(arch: Arch):
    """Ensure that all `constants.REPOSITORIES` inside `dir` exist"""
    prebuilts_dir = config.get_path("packages")
    makedir(prebuilts_dir)
    for repo in get_kupfer_repo_names(local=True):
        init_local_repo(repo, arch)


def prefer_prebuilt_recipes(arch: Arch) -> dict[str, Pkgbuild]:
    """Return recipes while preferring matching remote archives at build time.

    Recipes stay in the graph so ``check_package_version_built`` can download
    a matching archive into the local image repository before dependants are
    built.  This is required when the final rootfs uses local repositories.
    """
    recipes = discover_pkgbuilds()
    binaries = {}
    binary_repositories = {}
    # Match the configured repository order: Ombootstrap/Omarchy before base.
    for distro in (get_kupfer_https(arch), get_base_distro(arch)):
        for repository in distro.repos.values():
            if not repository.scan(allow_failure=True):
                logging.warning(
                    f"Prebuilt index unavailable: {repository.name}; keeping local fallback"
                )
                continue
            for name, binary in repository.packages.items():
                binaries.setdefault(name, binary)
                binary_repositories.setdefault(name, repository.name)

    prebuilt_only = _PREBUILT_ONLY_BY_ARCH.setdefault(arch, set())
    prebuilt_only.clear()
    selected = {}
    for name, recipe in recipes.items():
        # Device kernels must always use our pinned sources and configuration.
        if recipe.repo == "linux":
            selected[name] = recipe
            continue
        binary = binaries.get(name)
        matching_binary = (
            binary is not None
            and binary.name == recipe.name
            and binary.arch in (arch, "any")
            and binary_repositories.get(name) == recipe.repo
        )
        if arch == "aarch64" and name in PREBUILT_ONLY_PACKAGES:
            if not matching_binary:
                raise Exception(
                    f"Required prebuilt package {name} is unavailable for {arch}; "
                    "refusing to compile the local recipe"
                )
            prebuilt_only.add(name)
            logging.info(
                f"Using required prebuilt {name} {binary.version} ({arch}); "
                f"the archive will be staged before dependants build"
            )
        elif matching_binary:
            prebuilt_only.add(name)
            logging.info(
                f"Using prebuilt-only {name} {binary.version} ({arch}); "
                f"the archive will be staged when this recipe is checked"
            )
        selected[name] = recipe
    return selected


def generate_dependency_chain(
    package_repo: dict[str, Pkgbuild],
    to_build: Iterable[Pkgbuild],
    arch: Arch,
    *,
    allow_override_for_arches_all: bool = True,
    allow_skipping_build_packages: bool = False,
) -> list[set[Pkgbuild]]:
    """
    Resolve the requested packages and return dependency-first build levels.

    A package is put one level above its deepest local dependency.  Packages
    missing from ``package_repo`` are supplied by a binary repository and are
    therefore left outside the local build graph.
    """
    def matches_arch(package: Pkgbuild):
        if "any" in package.arches or arch in package.arches:
            return True
        if allow_override_for_arches_all and package.arches_hint == "all":
            logging.warning(
                f"Package {package.path!r} lacks arch {arch} but specifies _arches={package.arches_hint}; overriding!"
            )
            return True
        return False

    # discover_pkgbuilds() indexes concrete and replacement names.  Dependencies
    # may also use a provides name, so index every name exposed by each recipe.
    packages_by_name: dict[str, Pkgbuild] = {}
    for key, package in package_repo.items():
        packages_by_name.setdefault(key, package)
        for name in package.names():
            packages_by_name.setdefault(name, package)

    roots: set[Pkgbuild] = set()
    for package in to_build:
        if not matches_arch(package):
            if not allow_skipping_build_packages:
                raise Exception(
                    f"Can't build package {package.path!r} because it's not available for {arch=}.\n"
                    f"Available arches: {package.arches!r}; package arches hint: {package.arches_hint!r}"
                )
            logging.warning(
                f"Skipping requested package {package.path!r} because it's not available for {arch=}"
            )
            continue
        roots.add(package)

    def get_dependencies(package: Pkgbuild) -> Iterator[Pkgbuild]:
        """Return local, architecture-compatible dependencies of package."""
        seen: set[Pkgbuild] = set()
        for dep_name in package.depends or {}:
            dep_pkg = packages_by_name.get(dep_name)
            # A dependency absent from the local recipe set is provided by a
            # binary repository and must not be added to this build graph.
            if dep_pkg is None or dep_pkg is package or dep_pkg in seen:
                continue
            seen.add(dep_pkg)
            if not matches_arch(dep_pkg):
                logging.warning(
                    f"Not adding {dep_pkg.path} as dependency because arches don't match: {dep_pkg}"
                )
                continue
            yield dep_pkg

    # Compute distance from the deepest local dependency.  This avoids the old
    # level-moving algorithm's bug where a shared dependency could remain in the
    # same level as its dependant (q6voiced before tinyalsa).
    depths: dict[Pkgbuild, int] = {}
    visiting: list[Pkgbuild] = []

    def get_depth(package: Pkgbuild) -> int:
        if package in depths:
            return depths[package]
        if package in visiting:
            cycle = " -> ".join(pkg.name for pkg in visiting + [package])
            raise Exception(f"Dependency cycle detected: {cycle}")
        visiting.append(package)
        dependencies = list(get_dependencies(package))
        depth = 0 if not dependencies else 1 + max(
            get_depth(dep_pkg) for dep_pkg in dependencies
        )
        visiting.pop()
        depths[package] = depth
        logging.debug(f"Package {package.name} assigned dependency level {depth}")
        return depth

    for package in roots:
        get_depth(package)

    levels: dict[int, set[Pkgbuild]] = {}
    for package, depth in depths.items():
        levels.setdefault(depth, set()).add(package)
    return [levels[level] for level in sorted(levels)]


def add_file_to_repo(
    file_path: str, repo_name: str, arch: Arch, remove_original: bool = True
):
    check_programs_wrap(["repo-add"])
    repo_dir = os.path.join(config.get_package_dir(arch), repo_name)
    pacman_cache_dir = os.path.join(config.get_path("pacman"), arch)
    file_name = os.path.basename(file_path)
    target_file = os.path.join(repo_dir, file_name)

    init_local_repo(repo_name, arch)
    if file_path != target_file:
        logging.debug(f"moving {file_path} to {target_file} ({repo_dir})")
        shutil.copy(
            file_path,
            repo_dir,
        )
        if remove_original:
            remove_file(file_path)

    # clean up same name package from pacman cache
    cache_file = os.path.join(pacman_cache_dir, file_name)
    if os.path.exists(cache_file):
        logging.debug(f"Removing cached package file {cache_file}")
        remove_file(cache_file)
    cmd = [
        "repo-add",
        "--remove",
        os.path.join(
            repo_dir,
            f"{repo_name}.db.tar.xz",
        ),
        target_file,
    ]
    logging.debug(f"repo: running cmd: {cmd}")
    result = run_cmd(cmd, stderr=sys.stdout)
    assert isinstance(result, subprocess.CompletedProcess)
    if result.returncode != 0:
        raise Exception(
            f"Failed add package {target_file} to repo {repo_name}"
        )
    for ext in ["db", "files"]:
        old = os.path.join(repo_dir, f"{repo_name}.{ext}.tar.xz.old")
        if os.path.exists(old):
            remove_file(old)


def strip_compression_extension(filename: str):
    for ext in ["zst", "xz", "gz", "bz2"]:
        if filename.endswith(f".pkg.tar.{ext}"):
            return filename[: -(len(ext) + 1)]
    logging.debug(f"file {filename} matches no known package extension")
    return filename


def add_package_to_repo(package: Pkgbuild, arch: Arch):
    logging.info(f"Adding {package.path} to repo {package.repo}")
    pkgbuild_dir = os.path.join(config.get_path("pkgbuilds"), package.path)

    files = []
    for file in os.listdir(pkgbuild_dir):
        # Forced extension by makepkg.conf
        pkgext = ".pkg.tar"
        if pkgext not in file:
            continue
        stripped_name = strip_compression_extension(file)
        if not stripped_name.endswith(pkgext):
            continue

        repo_file = os.path.join(
            config.get_package_dir(arch), package.repo, file
        )
        files.append(repo_file)
        add_file_to_repo(os.path.join(pkgbuild_dir, file), package.repo, arch)

        # copy any-arch packages to other repos as well
        if stripped_name.endswith(f"-any{pkgext}"):
            for repo_arch in ARCHES:
                if repo_arch == arch:
                    continue  # done already
                add_file_to_repo(
                    repo_file, package.repo, repo_arch, remove_original=False
                )

    return files


def try_download_package(
    dest_file_path: str, package: Pkgbuild, arch: Arch
) -> Optional[str]:
    filename = os.path.basename(dest_file_path)
    logging.debug(f"checking if we can download {filename}")
    pkgname = package.name
    repo_name = package.repo
    repos = get_kupfer_https(arch, scan=True).repos
    if repo_name not in repos:
        logging.warning(f"Repository {repo_name} is not a known HTTPS repo")
        return None
    repo = repos[repo_name]
    if pkgname not in repo.packages:
        logging.warning(
            f"Package {pkgname} not found in remote repos, building instead."
        )
        return None
    repo_pkg: RemotePackage = repo.packages[pkgname]
    if repo_pkg.version != package.version:
        logging.info(
            f"Preferring prebuilt release {repo_pkg.version} for {pkgname} "
            f"(recipe version: {package.version})"
        )
    if repo_pkg.filename != filename:
        versions_str = f"local: {filename}, remote: {repo_pkg.filename}"
        logging.debug(
            f"using remote archive filename for {pkgname}: {versions_str}"
        )
    cache_file = os.path.join(
        config.get_path("pacman"), arch, repo_pkg.filename
    )
    if os.path.exists(cache_file):
        if not repo_pkg._desc or "SHA256SUM" not in repo_pkg._desc:
            cache_matches = False
            extra_msg = ". However, we can't validate it, as the https repo doesnt provide a SHA256SUM for it."
        else:
            cache_matches = (
                sha256sum(cache_file) == repo_pkg._desc["SHA256SUM"]
            )
            extra_msg = (
                ". However its checksum doesn't match."
                if not cache_matches
                else " and its checksum matches."
            )
        logging.debug(
            f"While checking the HTTPS repo DB, we found a matching filename in the pacman cache{extra_msg}"
        )
        if cache_matches:
            logging.info(
                f"copying cache file {cache_file} to repo as verified by remote checksum"
            )
            shutil.copy(cache_file, dest_file_path)
            remove_file(cache_file)
            return dest_file_path
    url = repo_pkg.resolved_url
    assert url
    try:
        path = repo_pkg.acquire()
        assert os.path.exists(path)
        return path
    except HTTPError as e:
        if e.code == 404:
            logging.debug(
                f"remote package {filename} missing on server: {url}"
            )
        else:
            logging.error(
                f"remote package {filename} failed to download ({e.code}): {url}: {e}"
            )
        return None


def check_package_version_built(
    package: Pkgbuild,
    arch: Arch,
    try_download: bool = False,
    refresh_sources: bool = False,
) -> bool:
    logging.info(
        f"Checking if {package.name} is built for architecture {arch}"
    )

    if refresh_sources and not (
        is_prebuilt_only(package.name, arch)
    ):
        setup_sources(package)

    missing = True
    filename = package.get_filename(arch)
    filename_stripped = strip_compression_extension(filename)
    local_repo: Optional[LocalRepo] = None
    if not filename_stripped.endswith(".pkg.tar"):
        raise Exception(
            f"{package.name}: stripped filename has unknown extension. {filename}"
        )
    logging.debug(f"Checking if {filename_stripped} is built")

    any_arch = filename_stripped.endswith("any.pkg.tar")
    if any_arch:
        logging.debug(f"{package.name}: any-arch pkg detected")

    init_prebuilts(arch)
    # check if DB entry exists and matches PKGBUILD
    try:
        local_distro = get_kupfer_local(arch, in_chroot=False, scan=True)
        if package.repo not in local_distro.repos:
            raise Exception(f"Repo {package.repo} not found locally")
        local_repo = local_distro.repos[package.repo]
        if not local_repo.scanned:
            local_repo.scan()
        if package.name not in local_repo.packages:
            raise Exception(f"Package '{package.name}' not found")
        binpkg: LocalPackage = local_repo.packages[package.name]
        if package.version != binpkg.version:
            raise Exception(
                f"Versions differ: PKGBUILD: {package.version}, Repo: {binpkg.version}"
            )
        if binpkg.arch not in (
            ["any"] if package.arches == ["any"] else [arch]
        ):
            raise Exception(
                f"Wrong Architecture: {binpkg.arch}, requested: {arch}"
            )
        assert binpkg.resolved_url
        filepath = binpkg.resolved_url.split("file://")[1]
        if filename_stripped != strip_compression_extension(binpkg.filename):
            raise Exception(
                f"Repo entry exists but the filename {binpkg.filename} doesn't match expected {filename_stripped}"
            )
        if not os.path.exists(filepath):
            raise Exception(
                f"Repo entry exists but file {filepath} is missing from disk"
            )
        assert binpkg._desc
        if "SHA256SUM" not in binpkg._desc or not binpkg._desc["SHA256SUM"]:
            raise Exception("Repo entry exists but has no checksum")
        if sha256sum(filepath) != binpkg._desc["SHA256SUM"]:
            raise Exception("Repo entry exists but checksum doesn't match")
        missing = False
        file = filepath
        filename = binpkg.filename
        logging.debug(
            f"{filename} found in {package.repo}.db ({arch}) and checksum matches"
        )
    except Exception as ex:
        logging.debug(
            f"Failed to search local repos for package {package.name}: {ex}"
        )

    # file might be in repo directory but not in DB or checksum mismatch
    for ext in ["xz", "zst"]:
        if not missing:
            break
        file = os.path.join(
            config.get_package_dir(arch),
            package.repo,
            f"{filename_stripped}.{ext}",
        )
        if not os.path.exists(file):
            # look for 'any' arch packages in other repos
            if any_arch:
                target_repo_file = os.path.join(
                    config.get_package_dir(arch), package.repo, filename
                )
                if os.path.exists(target_repo_file):
                    file = target_repo_file
                    missing = False
                else:
                    # we have to check if another arch's repo holds our any-arch pkg
                    for repo_arch in ARCHES:
                        if repo_arch == arch:
                            continue  # we already checked that
                        other_repo_file = os.path.join(
                            config.get_package_dir(repo_arch),
                            package.repo,
                            filename,
                        )
                        if os.path.exists(other_repo_file):
                            logging.info(
                                f"package {file} found in {repo_arch} repo, copying to {arch}"
                            )
                            file = other_repo_file
                            missing = False
            if try_download and missing:
                downloaded = try_download_package(file, package, arch)
                if downloaded:
                    file = downloaded
                    filename = os.path.basename(file)
                    missing = False
                    logging.info(
                        f"Successfully downloaded {filename} from HTTPS mirror"
                    )
        if os.path.exists(file):
            missing = False
            add_file_to_repo(
                file, repo_name=package.repo, arch=arch, remove_original=False
            )
            assert local_repo
            local_repo.scan()
    # copy arch=(any) packages to all arches
    if any_arch and not missing:
        # copy to other arches if they don't have it
        for repo_arch in ARCHES:
            if repo_arch == arch:
                continue  # we already have that
            copy_target = os.path.join(
                config.get_package_dir(repo_arch), package.repo, filename
            )
            if not os.path.exists(copy_target):
                logging.info(
                    f"copying any-arch package {package.name} to {repo_arch} repo: {copy_target}"
                )
                add_file_to_repo(
                    file, package.repo, repo_arch, remove_original=False
                )
                other_repo = get_kupfer_local(
                    repo_arch, in_chroot=False, scan=False
                ).repos.get(package.repo, None)
                if other_repo and other_repo.scanned:
                    other_repo.scan()
    return not missing


def setup_build_chroot(
    arch: Arch,
    extra_packages: list[str] = [],
    add_kupfer_repos: bool = True,
    clean_chroot: bool = False,
    repo: Optional[dict[str, Pkgbuild]] = None,
) -> BuildChroot:
    assert config.runtime.arch
    if arch != config.runtime.arch:
        build_enable_qemu_binfmt(
            arch, repo=repo or discover_pkgbuilds(), lazy=False
        )
    init_prebuilts(arch)
    chroot = get_build_chroot(arch, add_kupfer_repos=add_kupfer_repos)
    chroot.mount_packages()
    logging.debug(f"Initializing {arch} build chroot")
    chroot.initialize(reset=clean_chroot)
    chroot.write_pacman_conf()  # in case it was initialized with different repos
    chroot.activate()
    chroot.mount_pacman_cache()
    chroot.mount_pkgbuilds()
    if extra_packages:
        chroot.try_install_packages(extra_packages, allow_fail=False)
    assert config.runtime.uid is not None
    chroot.create_user(
        "omarchy", password="12345678", uid=config.runtime.uid, non_unique=True
    )
    if not os.path.exists(chroot.get_path("/etc/sudoers.d/omarchy_nopw")):
        chroot.add_sudo_config(
            "omarchy_nopw", "omarchy", password_required=False
        )

    return chroot


def setup_git_insecure_paths(chroot: BuildChroot, username: str = "omarchy"):
    chroot.run_cmd(
        ["git", "config", "--global", "--add", "safe.directory", "'*'"],
        switch_user=username,
    ).check_returncode()  # type: ignore[union-attr]


def setup_sources(package: Pkgbuild, lazy: bool = True):
    cache = package.srcinfo_cache
    assert cache
    # catch cache._changed: if the PKGBUILD changed whatsoever, that's an indicator the sources might be changed
    if lazy and not cache._changed and cache.is_src_initialised():
        if cache.validate_checksums():
            logging.info(f"{package.path}: Sources already set up.")
            return
    makepkg_setup = MAKEPKG_CMD + [
        "--nodeps",
        "--nobuild",
        "--noprepare",
        "--skippgpcheck",
    ]

    logging.info(f"{package.path}: Getting build chroot for source setup")
    # we need to use a chroot here because makepkg symlinks sources into src/ via an absolute path
    dir = os.path.join(CHROOT_PATHS["pkgbuilds"], package.path)
    assert config.runtime.arch
    chroot = setup_build_chroot(config.runtime.arch)
    logging.info(f"{package.path}: Setting up sources with makepkg")
    result = chroot.run_cmd(
        makepkg_setup, cwd=dir, switch_user="omarchy", stderr=sys.stdout
    )
    assert isinstance(result, subprocess.CompletedProcess)
    if result.returncode != 0:
        raise Exception(
            f"{package.path}: Failed to setup sources, exit code: {result.returncode}"
        )
    cache.refresh_all(write=True)
    cache.write_src_initialised()
    old_version = package.version
    package.refresh_sources()
    if package.version != old_version:
        logging.info(
            f"{package.path}: version refreshed from {old_version} to {package.version}"
        )


def build_package(
    package: Pkgbuild,
    arch: Arch,
    repo_dir: Optional[str] = None,
    enable_crosscompile: bool = True,
    enable_crossdirect: bool = True,
    enable_ccache: bool = True,
    clean_chroot: bool = False,
    build_user: str = "omarchy",
    repo: Optional[dict[str, Pkgbuild]] = None,
):
    makepkg_compile_opts = ["--holdver"]
    makepkg_conf_path = "etc/makepkg.conf"
    repo_dir = repo_dir if repo_dir else config.get_path("pkgbuilds")
    foreign_arch = config.runtime.arch != arch
    deps = list(package.makedepends)
    names = set(package.names())
    if isinstance(package, SubPkgbuild):
        names |= set(package.pkgbase.names())
    if not package.nodeps:
        deps += list(package.depends)
    deps = list(set(deps) - names)
    needs_rust = "rust" in deps
    logging.info(
        f"{package.path}: Preparing to build: getting native arch build chroot"
    )
    build_root: BuildChroot
    target_chroot = setup_build_chroot(
        arch=arch,
        extra_packages=deps,
        clean_chroot=clean_chroot,
        repo=repo,
    )
    assert config.runtime.arch
    native_chroot = target_chroot
    if foreign_arch:
        logging.info(
            f"{package.path}: Preparing to build: getting {arch} build chroot"
        )
        native_chroot = setup_build_chroot(
            arch=config.runtime.arch,
            extra_packages=["base-devel"] + CROSSDIRECT_PKGS,
            clean_chroot=clean_chroot,
            repo=repo,
        )
    if not package.mode:
        logging.warning(
            f'Package {package.path} has no _mode set, assuming "host"'
        )
    cross = foreign_arch and package.mode == "cross" and enable_crosscompile

    if cross:
        logging.info(f"Cross-compiling {package.path}")
        build_root = native_chroot
        makepkg_compile_opts += ["--nodeps"]
        env = deepcopy(get_makepkg_env(arch))
        if enable_ccache:
            env["PATH"] = f"/usr/lib/ccache:{env['PATH']}"
            native_chroot.mount_ccache(user=build_user)
        logging.info(
            f"{package.path}: Setting up dependencies for cross-compilation"
        )
        # include crossdirect for ccache symlinks and qemu-user
        cross_deps = (
            list(package.makedepends)
            if package.nodeps
            else (
                deps
                + CROSSDIRECT_PKGS
                + [f"{GCC_HOSTSPECS[native_chroot.arch][arch]}-gcc"]
            )
        )
        results = native_chroot.try_install_packages(cross_deps)
        if not package.nodeps:
            res_crossdirect = results["crossdirect"]
            assert isinstance(res_crossdirect, subprocess.CompletedProcess)
            if res_crossdirect.returncode != 0:
                raise Exception("Unable to install crossdirect")
        # mount foreign arch chroot inside native chroot
        chroot_relative = os.path.join(
            CHROOT_PATHS["chroots"], target_chroot.name
        )
        makepkg_path_absolute = native_chroot.write_makepkg_conf(
            target_arch=arch, cross_chroot_relative=chroot_relative, cross=True
        )
        makepkg_conf_path = os.path.join(
            "etc", os.path.basename(makepkg_path_absolute)
        )
        native_chroot.mount_crosscompile(target_chroot)
    else:
        logging.info(f"Host-compiling {package.path}")
        build_root = target_chroot
        makepkg_compile_opts += [
            "--nodeps" if package.nodeps else "--syncdeps"
        ]
        env = deepcopy(get_makepkg_env(arch))
        if (
            foreign_arch
            and package.crossdirect
            and enable_crossdirect
            and package.name not in CROSSDIRECT_PKGS
        ):
            env["PATH"] = f"/native/usr/lib/crossdirect/{arch}:{env['PATH']}"
            target_chroot.mount_crossdirect(native_chroot)
        else:
            if enable_ccache:
                logging.debug("ccache enabled")
                env["PATH"] = f"/usr/lib/ccache:{env['PATH']}"
                deps += ["ccache"]
            logging.debug(
                ("Building for native arch. " if not foreign_arch else "")
                + "Skipping crossdirect."
            )
        if not package.nodeps:
            dep_install = target_chroot.try_install_packages(
                deps, allow_fail=False
            )
            failed_deps = [
                name
                for name, res in dep_install.items()
                if (res.returncode if not isinstance(res, int) else res) != 0
            ]  # type: ignore[union-attr]
            if failed_deps:
                raise Exception(
                    f"{package.path}: Dependencies failed to install: {failed_deps}"
                )

    if enable_ccache:
        build_root.mount_ccache(user=build_user)
    if needs_rust:
        build_root.mount_rust(user=build_user)
    setup_git_insecure_paths(build_root)
    makepkg_conf_absolute = os.path.join("/", makepkg_conf_path)

    build_cmd = [
        "source",
        "/etc/profile",
        "&&",
        *MAKEPKG_CMD,
        "--config",
        makepkg_conf_absolute,
        "--skippgpcheck",
        *makepkg_compile_opts,
    ]
    logging.debug(f"Building: Running {build_cmd}")
    result = build_root.run_cmd(
        build_cmd,
        inner_env=env,
        cwd=os.path.join(CHROOT_PATHS["pkgbuilds"], package.path),
        switch_user=build_user,
        stderr=sys.stdout,
    )
    assert isinstance(result, subprocess.CompletedProcess)
    if result.returncode != 0:
        raise Exception(f"Failed to compile package {package.path}")


def get_dependants(
    repo: dict[str, Pkgbuild],
    packages: Iterable[Pkgbuild],
    arch: Arch,
    recursive: bool = True,
    allow_override_for_arches_all: bool = True,
) -> set[Pkgbuild]:
    names = set([pkg.name for pkg in packages])
    to_add = set[Pkgbuild]()
    for pkg in repo.values():
        if set.intersection(names, set(pkg.depends)):
            if not set([arch, "any"]).intersection(pkg.arches):
                if not (
                    allow_override_for_arches_all and pkg.arches_hint == "all"
                ):
                    logging.warn(
                        f"get_dependants: skipping matched pkg {pkg.name} due to wrong arch: {pkg.arches}"
                    )
                    continue
            to_add.add(pkg)
    if recursive and to_add:
        to_add.update(
            get_dependants(
                repo,
                to_add,
                arch=arch,
                allow_override_for_arches_all=allow_override_for_arches_all,
            )
        )
    return to_add


def get_pkg_names_str(pkgs: Iterable[Pkgbuild]) -> str:
    return ", ".join(x.name for x in pkgs)


def get_pkg_levels_str(pkg_levels: Iterable[Iterable[Pkgbuild]]):
    return "\n".join(
        f"{i}: {get_pkg_names_str(level)}"
        for i, level in enumerate(pkg_levels)
    )


def get_unbuilt_package_levels(
    packages: Iterable[Pkgbuild],
    arch: Arch,
    repo: Optional[dict[str, Pkgbuild]] = None,
    force: bool = False,
    rebuild_dependants: bool = False,
    try_download: bool = False,
    refresh_sources: bool = True,
    allow_override_for_arches_all: bool = True,
    allow_skipping_build_packages: bool = False,
) -> list[set[Pkgbuild]]:
    repo = repo or discover_pkgbuilds()
    dependants = set[Pkgbuild]()
    if rebuild_dependants:
        dependants = get_dependants(
            repo,
            packages,
            arch=arch,
            allow_override_for_arches_all=allow_override_for_arches_all,
        )
    package_levels = generate_dependency_chain(
        package_repo=repo,
        to_build=set(packages).union(dependants),
        arch=arch,
        allow_override_for_arches_all=allow_override_for_arches_all,
    )
    build_names = set[str]()
    build_levels = list[set[Pkgbuild]]()
    includes_dependants = (
        " (includes dependants)" if rebuild_dependants else ""
    )
    logging.info(
        f"Checking for unbuilt packages ({arch}) in dependency order{includes_dependants}:\n{get_pkg_levels_str(package_levels)}"
    )
    i = 0
    total_levels = len(package_levels)
    package_bar = get_levels_bar(
        total=sum([len(lev) for lev in package_levels]),
        desc=f"Checking pkgs ({arch})",
        unit="pkgs",
        fields={"levels_total": total_levels},
        enable_rate=False,
    )
    counter_built = package_bar.add_subcounter("green")
    counter_unbuilt = package_bar.add_subcounter("blue")
    for level_num, level_packages in enumerate(package_levels):
        level_num = level_num + 1
        package_bar.update(0, name=" " * BAR_PADDING, level=level_num)
        level = set[Pkgbuild]()
        if not level_packages:
            continue

        def add_to_level(pkg, level, reason=""):
            if reason:
                reason = f": {reason}"
            counter_unbuilt.update(force=True)
            logging.info(
                f"Level {level}/{total_levels} ({arch}): Adding {package.path}{reason}"
            )
            level.add(package)
            build_names.update(package.names())

        for package in level_packages:
            package_bar.update(
                0,
                force=True,
                name=ellipsize(package.name, padding=" ", length=BAR_PADDING),
            )
            if force and package in packages:
                if is_prebuilt_only(package.name, arch):
                    package_built = check_package_version_built(
                        package,
                        arch,
                        try_download=try_download and package.repo != "linux",
                        refresh_sources=refresh_sources,
                    )
                    if not package_built:
                        raise Exception(
                            f"Required prebuilt package {package.name} could not be downloaded for {arch}; "
                            "refusing to compile the local recipe"
                        )
                    counter_built.update(force=True)
                else:
                    add_to_level(package, level, "query match and force=True")
            elif rebuild_dependants and package in dependants:
                if is_prebuilt_only(package.name, arch):
                    package_built = check_package_version_built(
                        package,
                        arch,
                        try_download=try_download and package.repo != "linux",
                        refresh_sources=refresh_sources,
                    )
                    if not package_built:
                        raise Exception(
                            f"Required prebuilt package {package.name} could not be downloaded for {arch}; "
                            "refusing to compile the local recipe"
                        )
                    counter_built.update(force=True)
                else:
                    add_to_level(
                        package,
                        level,
                        "package is a dependant, dependant-rebuilds requested",
                    )
            else:
                package_built = check_package_version_built(
                    package,
                    arch,
                    try_download=try_download and package.repo != "linux",
                    refresh_sources=refresh_sources,
                )
                if not package_built and is_prebuilt_only(package.name, arch):
                    raise Exception(
                        f"Required prebuilt package {package.name} could not be downloaded for {arch}; "
                        "refusing to compile the local recipe"
                    )
                if not package_built:
                    add_to_level(package, level, "package unbuilt")
                    continue
                logging.info(
                    f"Level {level_num}/{total_levels} ({arch}): {package.path}: Package doesn't need [re]building"
                )
                counter_built.update(force=True)

        logging.debug(
            f"Finished checking level {level_num}/{total_levels} ({arch}). Adding unbuilt pkgs: {get_pkg_names_str(level)}"
        )
        if level:
            build_levels.append(level)
            i += 1
    package_bar.close(clear=True)
    return build_levels


def get_build_levels_from_paths(
    paths: list[str] | str,
    arch: Arch,
    repo: Optional[dict[str, Pkgbuild]] = None,
    **kwargs,
) -> list[set[Pkgbuild]]:
    if isinstance(paths, str):
        paths = [paths]
    assert config.runtime.arch
    for _arch in set([arch, config.runtime.arch]):
        init_prebuilts(_arch)
    packages = filter_pkgbuilds(
        paths, arch=arch, repo=repo, allow_empty_results=False
    )
    return get_build_levels_from_pkgbuilds(
        packages,
        arch=arch,
        repo=repo,
        **kwargs,
    )


def get_build_levels_from_pkgbuilds(
    packages: Iterable[Pkgbuild],
    arch: Arch,
    repo: Optional[dict[str, Pkgbuild]] = None,
    force: bool = False,
    rebuild_dependants: bool = False,
    try_download: bool = False,
    allow_override_for_arches_all: bool = True,
    allow_skipping_build_packages: bool = False,
) -> list[set[Pkgbuild]]:
    check_programs_wrap(["makepkg", "pacman", "pacstrap"])
    init_prebuilts(arch)
    build_levels = get_unbuilt_package_levels(
        packages,
        arch,
        repo=repo,
        force=force,
        rebuild_dependants=rebuild_dependants,
        try_download=try_download,
        allow_override_for_arches_all=allow_override_for_arches_all,
        allow_skipping_build_packages=allow_skipping_build_packages,
    )

    if not build_levels:
        logging.info("Everything built already")
    return build_levels


def build_packages(
    packages: Iterable[Pkgbuild],
    arch: Arch,
    repo: Optional[dict[str, Pkgbuild]] = None,
    force: bool = False,
    rebuild_dependants: bool = False,
    try_download: bool = False,
    enable_crosscompile: bool = True,
    enable_crossdirect: bool = True,
    enable_ccache: bool = True,
    clean_chroot: bool = False,
    allow_override_for_arches_all: bool = True,
    allow_skipping_build_packages: bool = False,
):
    build_levels = get_build_levels_from_pkgbuilds(
        packages,
        arch=arch,
        repo=repo,
        force=force,
        rebuild_dependants=rebuild_dependants,
        try_download=try_download,
        allow_override_for_arches_all=allow_override_for_arches_all,
        allow_skipping_build_packages=allow_skipping_build_packages,
    )
    if not build_levels:
        return
    return build_packages_by_levels(
        build_levels,
        arch=arch,
        repo=repo,
        enable_crosscompile=enable_crosscompile,
        enable_crossdirect=enable_crossdirect,
        enable_ccache=enable_ccache,
        clean_chroot=clean_chroot,
    )


def build_packages_by_levels(
    build_levels: list[set[Pkgbuild]],
    arch: Arch,
    repo: Optional[dict[str, Pkgbuild]] = None,
    enable_crosscompile: bool = True,
    enable_crossdirect: bool = True,
    enable_ccache: bool = True,
    clean_chroot: bool = False,
):
    logging.info(f"Build plan made:\n{get_pkg_levels_str(build_levels)}")
    total_levels = len(build_levels)
    package_bar = get_levels_bar(
        desc=f"Building pkgs ({arch})",
        color="purple",
        unit="pkgs",
        total=sum([len(lev) for lev in build_levels]),
        fields={"levels_total": total_levels},
        enable_rate=False,
    )
    files = []
    updated_repos: set[str] = set()
    package_bar.update(-1)
    for level, need_build in enumerate(build_levels):
        level = level + 1
        package_bar.update(
            incr=0, force=True, name=" " * BAR_PADDING, level=level
        )
        logging.info(
            f"(Level {level}/{total_levels}) Building {get_pkg_names_str(need_build)}"
        )
        for package in need_build:
            package_bar.update(
                force=True,
                name=ellipsize(package.name, padding=" ", length=BAR_PADDING),
            )
            base = (
                package.pkgbase
                if isinstance(package, SubPkgbuild)
                else package
            )
            assert isinstance(base, Pkgbase)
            if package.is_built(arch):
                logging.info(
                    f"Skipping building {package.name} since it was already built this run as part of pkgbase {base.name}"
                )
                continue
            build_package(
                package,
                arch=arch,
                enable_crosscompile=enable_crosscompile,
                enable_crossdirect=enable_crossdirect,
                enable_ccache=enable_ccache,
                clean_chroot=clean_chroot,
                repo=repo,
            )
            files += add_package_to_repo(package, arch)
            updated_repos.add(package.repo)
            for _arch in ["any", arch]:
                if _arch in base.arches:
                    base._built_for.add(_arch)
            package_bar.update()
    # rescan affected repos
    local_repos = get_kupfer_local(arch, in_chroot=False, scan=False)
    for repo_name in updated_repos:
        assert repo_name in local_repos.repos
        local_repos.repos[repo_name].scan()

    package_bar.close(clear=True)
    return files


def build_packages_by_paths(
    paths: Iterable[str],
    arch: Arch,
    repo: Optional[dict[str, Pkgbuild]] = None,
    force=False,
    rebuild_dependants: bool = False,
    try_download: bool = False,
    enable_crosscompile: bool = True,
    enable_crossdirect: bool = True,
    enable_ccache: bool = True,
    clean_chroot: bool = False,
    allow_override_for_arches_all: bool = True,
):
    if isinstance(paths, str):
        paths = [paths]

    check_programs_wrap(["makepkg", "pacman", "pacstrap"])
    assert config.runtime.arch
    for _arch in set([arch, config.runtime.arch]):
        init_prebuilts(_arch)
    packages = filter_pkgbuilds(
        paths, arch=arch, repo=repo, allow_empty_results=False
    )
    return build_packages(
        packages,
        arch,
        repo=repo,
        force=force,
        rebuild_dependants=rebuild_dependants,
        try_download=try_download,
        enable_crosscompile=enable_crosscompile,
        enable_crossdirect=enable_crossdirect,
        enable_ccache=enable_ccache,
        clean_chroot=clean_chroot,
        allow_override_for_arches_all=allow_override_for_arches_all,
    )


_qemu_enabled: dict[Arch, bool] = {arch: False for arch in ARCHES}


def build_enable_qemu_binfmt(
    arch: Arch,
    repo: Optional[dict[str, Pkgbuild]] = None,
    lazy: bool = True,
    native_chroot: Optional[BuildChroot] = None,
):
    """
    Build and enable qemu-user-static, binfmt and crossdirect
    Specify lazy=False to force building the packages.
    """
    if arch not in ARCHES:
        raise Exception(
            f'Unknown binfmt architecture "{arch}". Choices: {", ".join(ARCHES)}'
        )
    if _qemu_enabled[arch] or (lazy and binfmt_is_registered(arch)):
        if not _qemu_enabled[arch]:
            logging.info(f"qemu binfmt for {arch} was already enabled!")
        return
    native = config.runtime.arch
    assert native
    if arch == native:
        _qemu_enabled[arch] = True
        logging.warning("Not enabling binfmt for host architecture!")
        return
    logging.info("Installing qemu-user (building if necessary)")
    check_programs_wrap(["pacman", "makepkg", "pacstrap"])
    # build qemu-user, binfmt, crossdirect
    packages = list(CROSSDIRECT_PKGS)
    hostspec = GCC_HOSTSPECS[arch][arch]
    cross_gcc = f"{hostspec}-gcc"
    if repo:
        for pkg in repo.values():
            if pkg.name == cross_gcc or cross_gcc in pkg.provides:
                if config.runtime.arch not in pkg.arches:
                    logging.debug(
                        f"Package {pkg.path} matches {cross_gcc=} name but not arch: {pkg.arches=}"
                    )
                    continue
                packages.append(pkg.path)
                logging.debug(
                    f"Adding gcc package {pkg.path} to the  necessary crosscompilation tools"
                )
                break
    build_packages_by_paths(
        packages,
        native,
        repo=repo,
        try_download=True,
        enable_crosscompile=False,
        enable_crossdirect=False,
        enable_ccache=False,
    )
    crossrepo = (
        get_kupfer_local(native, in_chroot=False, scan=True)
        .repos["cross"]
        .packages
    )
    pkgfiles = [
        # we want this to crash on unresolved URL
        os.path.join((crossrepo[pkg].resolved_url or "").split("file://")[1])
        for pkg in QEMU_BINFMT_PKGS
    ]  # type: ignore
    runcmd = run_root_cmd
    if native_chroot or not is_wrapped():
        native_chroot = native_chroot or setup_build_chroot(native)
        runcmd = native_chroot.run_cmd
        hostdir = config.get_path("packages")
        _files = []
        # convert host paths to in-chroot paths
        for p in pkgfiles:
            assert p.startswith(hostdir)
            _files.append(
                os.path.join(
                    CHROOT_PATHS["packages"], p[len(hostdir) :].lstrip("/")
                )
            )
        pkgfiles = _files
    runcmd(
        ["pacman", "-U", "--noconfirm", "--needed"] + pkgfiles,
        stderr=sys.stdout,
    )
    binfmt_register(arch, chroot=native_chroot)
    _qemu_enabled[arch] = True
