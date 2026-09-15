import atexit
import json
import os
import re
import subprocess
import stat
import tempfile
import click
import logging
from signal import pause
from subprocess import CompletedProcess
from typing import Optional, Union

from ombootstrap.config.cli import ensure_default_config
from ombootstrap.config.state import config, Profile
from ombootstrap.chroot.device import DeviceChroot, get_device_chroot
from ombootstrap.constants import (
    Arch,
    BASE_LOCAL_PACKAGES,
    BASE_PACKAGES,
    POST_INSTALL_CMDS,
)
from ombootstrap.distro.distro import get_base_distro, get_kupfer_https
from ombootstrap.devices.device import Device, get_profile_device
from ombootstrap.exec.cmd import run_root_cmd, generate_cmd_su
from ombootstrap.exec.file import (
    get_temp_dir,
    root_write_file,
    root_makedir,
    makedir,
)
from ombootstrap.flavours.flavour import Flavour, get_profile_flavour
from ombootstrap.net.ssh import copy_ssh_keys
from ombootstrap.packages.build import (
    build_enable_qemu_binfmt,
    build_packages,
    filter_pkgbuilds,
    prefer_prebuilt_recipes,
)
from ombootstrap.utils import programs_available
from ombootstrap.version.cli import _check_kbs_version
from ombootstrap.wrapper import enforce_wrap, needs_wrap

# image files need to be slightly smaller than partitions to fit
IMG_FILE_ROOT_DEFAULT_SIZE = "1800M"
IMG_FILE_BOOT_DEFAULT_SIZE = "90M"
HOST_IMAGE_PROGRAMS = ("pacman", "pacstrap", "makepkg", "repo-add")


def check_host_image_programs():
    """Fail early with an install hint when host mode lacks Arch tooling."""
    if needs_wrap():
        return
    missing = [
        program
        for program in HOST_IMAGE_PROGRAMS
        if not programs_available(program)
    ]
    if not missing:
        return
    missing_text = ", ".join(missing)
    raise Exception(
        "Host image build is enabled (wrapper.type=none), but these "
        f"commands are missing: {missing_text}. On Arch/Omarchy install "
        "arch-install-scripts and base-devel, or set "
        '[wrapper].type="docker" to build in Docker.'
    )


def dd_image(input: str, output: str, blocksize="1M") -> CompletedProcess:
    cmd = [
        "dd",
        f"if={input}",
        f"of={output}",
        f"bs={blocksize}",
        "oflag=direct",
        "status=progress",
        "conv=sync,noerror",
    ]
    logging.debug(f"running dd cmd: {cmd}")
    return run_root_cmd(cmd)


def partprobe(device: str):
    return run_root_cmd(["partprobe", device])


def bytes_to_sectors(b: int, sector_size: int, round_up: bool = True):
    sectors, rest = divmod(b, sector_size)
    if rest and round_up:
        sectors += 1
    return sectors


def get_fs_size(partition: str) -> tuple[int, int]:
    blocks_cmd = run_root_cmd(
        ["dumpe2fs", "-h", partition], env={"LC_ALL": "C"}, capture_output=True
    )
    if blocks_cmd.returncode != 0:
        logging.debug(f"dumpe2fs stdout:\n: {blocks_cmd.stdout}")
        logging.debug(f"dumpe2fs stderr:\n {blocks_cmd.stderr}")
        raise Exception(f"Failed to detect new filesystem size of {partition}")
    blocks_text = (
        blocks_cmd.stdout.decode("utf-8") if blocks_cmd.stdout else ""
    )
    try:
        re_blocks = re.search(
            "\\nBlock count:[ ]+([0-9]+)\\n",
            blocks_text,
            flags=re.MULTILINE,
        )
        assert re_blocks, "Block output should contain block count"
        fs_blocks = int(re_blocks.group(1))

        re_blocksize = re.search("\\nBlock size:[ ]+([0-9]+)\\n", blocks_text)
        assert re_blocksize, "Block output should contain block size"
        fs_block_size = int(re_blocksize.group(1))
    except Exception as ex:
        logging.debug(f"dumpe2fs stdout:\n  {blocks_text}")
        logging.debug(f"dumpe2fs stderr:\n: {blocks_cmd.stderr}")
        logging.info(
            "Failed to scrape block size and count from dumpe2fs:", ex
        )
        raise ex
    return fs_blocks, fs_block_size


def align_bytes(size_bytes: int, alignment: int = 4096) -> int:
    rest = size_bytes % alignment
    if rest:
        size_bytes += alignment - rest
    return size_bytes


def shrink_fs(loop_device: str, file: str, sector_size: int):
    partprobe(loop_device)
    logging.debug(f"Checking filesystem at {loop_device}p2")
    result = run_root_cmd(["e2fsck", "-fy", f"{loop_device}p2"])
    if result.returncode > 2:
        # https://man7.org/linux/man-pages/man8/e2fsck.8.html#EXIT_CODE
        raise Exception(
            f"Failed to e2fsck {loop_device}p2 with exit code {result.returncode}"
        )

    logging.info(f"Shrinking filesystem at {loop_device}p2")
    result = run_root_cmd(["resize2fs", "-M", f"{loop_device}p2"])
    if result.returncode != 0:
        raise Exception(f"Failed to resize2fs {loop_device}p2")

    logging.debug(f"Reading size of shrunken filesystem on {loop_device}p2")
    fs_blocks, fs_block_size = get_fs_size(f"{loop_device}p2")
    sectors = bytes_to_sectors(fs_blocks * fs_block_size, sector_size)

    logging.info(
        f"Shrinking partition at {loop_device}p2 to {sectors} sectors ({sectors * sector_size} bytes)"
    )
    child_proccess = subprocess.Popen(
        generate_cmd_su(
            ["fdisk", "-b", str(sector_size), loop_device], switch_user="root"
        ),  # type: ignore
        stdin=subprocess.PIPE,
    )
    assert child_proccess.stdin is not None
    child_proccess.stdin.write(
        "\n".join(
            [  # type: ignore
                "d",
                "2",
                "n",
                "p",
                "2",
                "",
                f"+{sectors}",
                "w",
                "q",
            ]
        ).encode("utf-8")
    )

    child_proccess.communicate()

    returncode = child_proccess.wait()
    if returncode == 1:
        # For some reason re-reading the partition table fails, but that is not a problem
        partprobe(loop_device)
    if returncode > 1:
        raise Exception(
            f"Failed to shrink partition size of {loop_device}p2 with fdisk"
        )

    partprobe(loop_device).check_returncode()

    logging.debug(f"Finding end sector of partition at {loop_device}p2")
    result = run_root_cmd(
        ["fdisk", "-b", str(sector_size), "-l", loop_device],
        capture_output=True,
    )
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr)
        raise Exception(f"Failed to fdisk -l {loop_device}")

    end_sector = 0
    for line in result.stdout.decode("utf-8").split("\n"):
        if line.startswith(f"{loop_device}p2"):
            parts = list(filter(lambda part: part != "", line.split(" ")))
            end_sector = int(parts[2])

    if end_sector == 0:
        raise Exception(f"Failed to find end sector of {loop_device}p2")

    end_size = align_bytes((end_sector + 1) * sector_size, 4096)

    logging.debug(
        f"({end_sector} + 1) sectors * {sector_size} bytes/sector = {end_size} bytes"
    )
    logging.info(f"Truncating {file} to {end_size} bytes")
    result = subprocess.run(["truncate", "-s", str(end_size), file])
    if result.returncode != 0:
        raise Exception(f"Failed to truncate {file}")
    partprobe(loop_device)


def losetup_destroy(loop_device):
    logging.debug(f"Destroying loop device {loop_device}")
    run_root_cmd(
        [
            "losetup",
            "-d",
            loop_device,
        ],
        stderr=subprocess.DEVNULL,
    )


def get_device_name(device: Union[str, Device]) -> str:
    return device.name if isinstance(device, Device) else device


def get_flavour_name(flavour: Union[str, Flavour]) -> str:
    if isinstance(flavour, Flavour):
        return flavour.name
    return flavour


def get_image_name(
    device: Union[str, Device], flavour: Union[str, Flavour], img_type="full"
) -> str:
    return (
        f"{get_device_name(device)}-{get_flavour_name(flavour)}-{img_type}.img"
    )


def get_image_path(
    device: Union[str, Device], flavour: Union[str, Flavour], img_type="full"
) -> str:
    return os.path.join(
        config.get_path("images"), get_image_name(device, flavour, img_type)
    )


def losetup_rootfs_image(image_path: str, sector_size: int) -> str:
    logging.debug(
        f"Creating loop device for {image_path} with sector size {sector_size}"
    )
    result = run_root_cmd(
        [
            "losetup",
            "-f",
            "-b",
            str(sector_size),
            "-P",
            image_path,
        ]
    )
    if result.returncode != 0:
        raise Exception(f"Failed to create loop device for {image_path}")

    logging.debug(f"Finding loop device for {image_path}")

    result = subprocess.run(["losetup", "-J"], capture_output=True)
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr)
        raise Exception("Failed to list loop devices")

    data = json.loads(result.stdout.decode("utf-8"))
    loop_device = ""
    for d in data["loopdevices"]:
        if d["back-file"] == image_path:
            loop_device = d["name"]
            break

    if loop_device == "":
        raise Exception(f"Failed to find loop device for {image_path}")
    partprobe(loop_device)

    atexit.register(losetup_destroy, loop_device)

    return loop_device


def mount_chroot(rootfs_source: str, boot_src: str, chroot: DeviceChroot):
    logging.debug(f"Mounting {rootfs_source} at {chroot.path}")

    chroot.mount_rootfs(rootfs_source)
    assert os.path.ismount(chroot.path)

    root_makedir(chroot.get_path("boot"))

    logging.debug(f"Mounting {boot_src} at {chroot.path}/boot")
    chroot.mount(boot_src, "/boot", options=["defaults"])


def dump_file_from_image(
    image_path: str, file_path: str, target_path: Optional[str] = None
):
    target_path = target_path or os.path.join(
        get_temp_dir(), os.path.basename(file_path)
    )
    result = run_root_cmd(
        [
            "debugfs",
            image_path,
            "-R",
            f"'dump /{file_path.lstrip('/')} {target_path}'",
        ]
    )
    if result.returncode != 0 or not os.path.exists(target_path):
        raise Exception(f"Failed to dump {file_path} from /boot")
    return target_path


def dump_aboot(image_path: str) -> str:
    return dump_file_from_image(image_path, file_path="/aboot.img")


def dump_lk2nd(image_path: str) -> str:
    """
    This doesn't append the image with the appended DTB which is needed for some devices, so it should get added in the future.
    """
    return dump_file_from_image(image_path, file_path="/lk2nd.img")


def dump_qhypstub(image_path: str) -> str:
    return dump_file_from_image(image_path, file_path="/qhyptstub.img")


def cleanup_image_mounts(image_path: str, allow_hidden_mount: bool = False):
    """Unmount stale loop mounts backed by an image before recreating it."""
    result = run_root_cmd(
        ["losetup", "-j", image_path],
        capture_output=True,
    )
    if not isinstance(result, CompletedProcess) or result.returncode != 0:
        return

    loop_devices = set()
    output = result.stdout.decode("utf-8", errors="replace") if result.stdout else ""
    for line in output.splitlines():
        loop_device = line.split(":", 1)[0].strip()
        if loop_device.startswith("/dev/loop"):
            loop_devices.add(loop_device)

    for loop_device in sorted(loop_devices):
        mounts = run_root_cmd(
            ["findmnt", "-rn", "-S", loop_device, "-o", "TARGET"],
            capture_output=True,
        )
        targets_output = (
            mounts.stdout.decode("utf-8", errors="replace")
            if isinstance(mounts, CompletedProcess) and mounts.stdout
            else ""
        )
        targets = sorted(
            {line.strip() for line in targets_output.splitlines() if line.strip()},
            key=len,
            reverse=True,
        )
        for target in targets:
            logging.warning(
                f"Unmounting stale image mount {target} backed by {image_path}"
            )
            unmounted = run_root_cmd(
                ["umount", "--lazy", target],
                capture_output=True,
            )
            if not isinstance(unmounted, CompletedProcess) or unmounted.returncode != 0:
                # A single mount can be visible through several chroot
                # namespaces.  Unmounting the first target may remove all of
                # them, making later duplicate targets report ENOENT.
                still_mounted = run_root_cmd(
                    ["findmnt", "-rn", target],
                    capture_output=True,
                )
                if (
                    isinstance(still_mounted, CompletedProcess)
                    and still_mounted.returncode != 0
                ):
                    continue
                details = (
                    unmounted.stderr.decode("utf-8", errors="replace").strip()
                    if isinstance(unmounted, CompletedProcess) and unmounted.stderr
                    else ""
                )
                raise Exception(
                    f"Failed to unmount stale image mount {target}"
                    + (f": {details}" if details else "")
                )

        detached = run_root_cmd(
            ["losetup", "-d", loop_device],
            capture_output=True,
        )
        if isinstance(detached, CompletedProcess) and detached.returncode != 0:
            # Lazy unmounting can release the loop device concurrently.  In
            # that case losetup reports ENXIO even though no attachment
            # remains and there is nothing left to clean up.
            remaining = run_root_cmd(
                ["losetup", "-j", image_path],
                capture_output=True,
            )
            remaining_output = (
                remaining.stdout or b""
                if isinstance(remaining, CompletedProcess)
                else b""
            )
            if (
                isinstance(remaining, CompletedProcess)
                and remaining.returncode == 0
                and not remaining_output.strip()
            ):
                continue
            if allow_hidden_mount and not targets:
                # The mount may belong to a Docker namespace that has already
                # disappeared from our mount table. The image path is replaced
                # atomically below, so keeping this old loop attachment cannot
                # corrupt the new filesystem.
                logging.warning(
                    f"Leaving hidden stale loop device {loop_device} attached"
                )
                continue
            details = (
                detached.stderr.decode("utf-8", errors="replace").strip()
                if detached.stderr
                else ""
            )
            raise Exception(
                f"Failed to detach stale loop device {loop_device}"
                + (f": {details}" if details else "")
            )


def create_img_file(image_path: str, size_str: str):
    replace_existing = os.path.isfile(image_path)
    cleanup_image_mounts(image_path, allow_hidden_mount=replace_existing)

    # A loop mount from an earlier container can remain in another mount
    # namespace. Replacing an existing regular file gives that mount its old
    # inode while the next build receives a fresh inode, so mkfs cannot corrupt
    # a filesystem that is still mounted elsewhere.
    temp_path: Optional[str] = None
    target_mode = 0o644
    if replace_existing:
        target_mode = stat.S_IMODE(os.stat(image_path).st_mode)
        fd, temp_path = tempfile.mkstemp(
            prefix=f".{os.path.basename(image_path)}.",
            dir=os.path.dirname(image_path) or ".",
        )
        os.close(fd)
        target = temp_path
    else:
        target = image_path

    try:
        result = subprocess.run(["truncate", "-s", size_str, target])
        if result.returncode != 0:
            raise Exception(f"Failed to allocate {image_path}")
        if temp_path is not None:
            os.chmod(temp_path, target_mode)
            os.replace(temp_path, image_path)
            temp_path = None
    finally:
        if temp_path is not None:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass
    return image_path


def partition_device(device: str):
    boot_partition_size = "100MiB"
    create_partition_table = ["mklabel", "msdos"]
    create_boot_partition = [
        "mkpart",
        "primary",
        "ext2",
        "0%",
        boot_partition_size,
    ]
    create_root_partition = ["mkpart", "primary", boot_partition_size, "100%"]
    enable_boot = ["set", "1", "boot", "on"]
    result = run_root_cmd(
        [
            "parted",
            "--script",
            device,
        ]
        + create_partition_table
        + create_boot_partition
        + create_root_partition
        + enable_boot
    )
    if result.returncode != 0:
        raise Exception(f"Failed to create partitions on {device}")


def create_filesystem(
    device: str,
    blocksize: Optional[int],
    label=None,
    options=[],
    fstype="ext4",
):
    """Creates a new filesystem. Blocksize defaults"""
    labels = ["-L", label] if label else []
    cmd = [f"mkfs.{fstype}", "-F", *labels]
    if blocksize:
        # blocksize can be 4k max due to pagesize
        blocksize = min(blocksize, 4096)
        if fstype.startswith("ext"):
            # blocksize for ext-fs must be >=1024
            blocksize = max(blocksize, 1024)
        cmd += [
            "-b",
            str(blocksize),
        ]
    cmd.extend(options)
    cmd.append(device)
    result = run_root_cmd(cmd)
    if result.returncode != 0:
        raise Exception(
            f"Failed to create {fstype} filesystem on {device} with CMD: {cmd}"
        )


def create_root_fs(device: str, blocksize: Optional[int]):
    # Omarchy installs well over 100k files (desktop assets, libraries and
    # kernel modules). Keep the inode ratio very generous so a full image does not
    # report ENOSPC while block space is still available.
    create_filesystem(
        device,
        blocksize=blocksize,
        label="kupfer_root",
        options=["-O", "^metadata_csum", "-i", "2048"],
    )


def create_boot_fs(device: str, blocksize: Optional[int]):
    create_filesystem(
        device, blocksize=blocksize, label="kupfer_boot", fstype="ext2"
    )


def install_rootfs(
    rootfs_device: str,
    bootfs_device: str,
    device: Union[str, Device],
    flavour: Flavour,
    arch: Arch,
    packages: list[str],
    use_local_repos: bool,
    profile: Profile,
):
    user = profile["username"] or "omarchy"
    chroot = get_device_chroot(
        device=get_device_name(device),
        flavour=flavour.name,
        arch=arch,
        packages=packages,
        use_local_repos=use_local_repos,
    )

    mount_chroot(rootfs_device, bootfs_device, chroot)

    chroot.mount_pacman_cache()
    chroot.initialize()
    chroot.activate()
    chroot.create_user(
        user=user,
        password=profile["password"],
    )
    chroot.add_sudo_config(
        config_name="wheel", privilegee="%wheel", password_required=True
    )
    copy_ssh_keys(
        chroot,
        user=user,
        allow_fail=True,
    )
    files = {
        "etc/pacman.conf": get_base_distro(arch).get_pacman_conf(
            check_space=True,
            extra_repos=get_kupfer_https(arch).repos,
            in_chroot=True,
        ),
        "etc/hostname": profile["hostname"] or "omarchy-phone",
    }
    for target, content in files.items():
        root_write_file(os.path.join(chroot.path, target.lstrip("/")), content)

    logging.info("Running post-install CMDs")
    for cmd in POST_INSTALL_CMDS:
        result = chroot.run_cmd(cmd)
        assert isinstance(result, subprocess.CompletedProcess)
        if result.returncode != 0:
            raise Exception(f"Error running post-install cmd: {cmd}")

    if flavour.name == "omarchy-mobile":
        chroot.run_cmd(["ombs-mobile-setup-user", user]).check_returncode()

    logging.info("Preparing to unmount chroot")
    res = chroot.run_cmd("sync && umount /boot", attach_tty=True)
    logging.debug(f"rc: {res}")
    chroot.deactivate()

    logging.debug(f'Unmounting rootfs at "{chroot.path}"')
    res = run_root_cmd(["umount", chroot.path])
    assert isinstance(res, CompletedProcess)
    logging.debug(f"rc: {res.returncode}")


@click.group(name="image")
def cmd_image():
    """Build, flash and boot device images"""


sectorsize_option = click.option(
    "-b",
    "--sector-size",
    help="Override the device's sector size",
    type=int,
    default=None,
)


@cmd_image.command(name="build")
@click.argument("profile_name", required=False)
@click.option(
    "--local-repos/--no-local-repos",
    "-l/-L",
    help="Whether to use local package repos at all or only use HTTPS repos.",
    default=True,
    show_default=True,
    is_flag=True,
)
@click.option(
    "--build-pkgs/--no-build-pkgs",
    "-p/-P",
    help="Whether to build missing/outdated local packages if local repos are enabled.",
    default=True,
    show_default=True,
    is_flag=True,
)
@click.option(
    "--no-download-pkgs",
    help="Disable trying to download packages instead of building if building is enabled.",
    default=False,
    is_flag=True,
)
@click.option(
    "--block-target",
    help="Override the block device file to write the final image to",
    type=click.Path(),
    default=None,
)
@click.option(
    "--skip-part-images",
    help="Skip creating image files for the partitions and directly work on the target block device.",
    default=False,
    is_flag=True,
)
@sectorsize_option
def cmd_build(
    profile_name: Optional[str] = None,
    local_repos: bool = True,
    build_pkgs: bool = True,
    no_download_pkgs=False,
    block_target: Optional[str] = None,
    sector_size: Optional[int] = None,
    skip_part_images: bool = False,
):
    """
    Build a device image.

    Unless overriden, required packages will be built or preferably downloaded from HTTPS repos.
    """

    # Keep first-run config creation on the host; only the actual image build
    # is handed to Docker by enforce_wrap() below.
    ensure_default_config(select_device=True)
    config.enforce_profile_device_set()
    config.enforce_profile_flavour_set()
    check_host_image_programs()
    enforce_wrap()
    _check_kbs_version(init_pkgbuilds=True)
    device = get_profile_device(profile_name)
    arch = device.arch
    # check_programs_wrap(['makepkg', 'pacman', 'pacstrap'])
    profile: Profile = config.get_profile(profile_name)
    flavour = get_profile_flavour(profile_name)
    rootfs_size_mb = flavour.parse_flavourinfo().rootfs_size * 1000 + int(
        profile.size_extra_mb
    )

    packages = BASE_LOCAL_PACKAGES + [
        device.package.name,
        flavour.pkgbuild.name,
    ]
    packages_extra = BASE_PACKAGES + profile.pkgs_include

    if arch != config.runtime.arch:
        build_enable_qemu_binfmt(arch)

    if local_repos and build_pkgs:
        logging.info("Selecting prebuilt packages and remaining local builds")
        recipe_repo = prefer_prebuilt_recipes(arch) if not no_download_pkgs else None
        # enforce that local base packages are built
        pkgbuilds = set(
            filter_pkgbuilds(
                packages, arch=arch, repo=recipe_repo, allow_empty_results=True, use_paths=False
            )
        )
        # extra packages might be a mix of package names that are in our PKGBUILDs and packages from the base distro
        pkgbuilds |= set(
            filter_pkgbuilds(
                packages_extra,
                arch=arch,
                repo=recipe_repo,
                allow_empty_results=True,
                use_paths=False,
            )
        )
        build_packages(
            pkgbuilds, arch, repo=recipe_repo,
            try_download=not no_download_pkgs,
        )

    sector_size = sector_size or device.get_image_sectorsize()

    image_path = block_target or get_image_path(device, flavour.name)

    makedir(os.path.dirname(image_path))

    logging.info(f"Creating new file at {image_path}")
    create_img_file(image_path, f"{rootfs_size_mb}M")

    loop_device = losetup_rootfs_image(
        image_path, sector_size or device.get_image_sectorsize_default()
    )

    partition_device(loop_device)
    partprobe(loop_device)

    boot_dev: str
    root_dev: str
    loop_boot = loop_device + "p1"
    loop_root = loop_device + "p2"
    if skip_part_images:
        boot_dev = loop_boot
        root_dev = loop_root
    else:
        logging.info("Creating per-partition image files")
        boot_dev = create_img_file(
            get_image_path(device, flavour, "boot"), IMG_FILE_BOOT_DEFAULT_SIZE
        )
        root_dev = create_img_file(
            get_image_path(device, flavour, "root"), f"{rootfs_size_mb - 200}M"
        )

    create_boot_fs(boot_dev, sector_size)
    create_root_fs(root_dev, sector_size)

    install_rootfs(
        root_dev,
        boot_dev,
        device,
        flavour,
        arch,
        list(set(packages) | set(packages_extra)),
        local_repos,
        profile,
    )

    if not skip_part_images:
        logging.info("Copying partition image files into full image:")
        logging.info(f"Block-copying /boot to {image_path}")
        dd_image(input=boot_dev, output=loop_boot).check_returncode()
        logging.info(f"Block-copying rootfs to {image_path}")
        dd_image(input=root_dev, output=loop_root).check_returncode()

    logging.info(f"Done! Image saved to {image_path}")


@cmd_image.command(name="inspect")
@click.option("--shell", "-s", is_flag=True)
@sectorsize_option
@click.argument("profile", required=False)
def cmd_inspect(
    profile: Optional[str] = None,
    shell: bool = False,
    sector_size: Optional[int] = None,
):
    """Loop-mount the device image for inspection."""
    config.enforce_profile_device_set()
    config.enforce_profile_flavour_set()
    enforce_wrap()
    _check_kbs_version(init_pkgbuilds=True)
    device = get_profile_device(profile)
    arch = device.arch
    flavour = get_profile_flavour(profile).name
    sector_size = sector_size or device.get_image_sectorsize_default()

    chroot = get_device_chroot(device.name, flavour, arch)
    image_path = get_image_path(device, flavour)
    loop_device = losetup_rootfs_image(image_path, sector_size)
    partprobe(loop_device)
    mount_chroot(loop_device + "p2", loop_device + "p1", chroot)

    logging.info(f"Inspect the rootfs image at {chroot.path}")

    if shell:
        chroot.initialized = True
        chroot.activate()
        if arch != config.runtime.arch:
            logging.info("Installing requisites for foreign-arch shell")
            build_enable_qemu_binfmt(arch)
        logging.info("Starting inspection shell")
        chroot.run_cmd("/bin/bash")
    else:
        pause()
