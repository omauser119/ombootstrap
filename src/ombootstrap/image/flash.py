import shutil
import os
import click
import logging

from typing import Optional

from ombootstrap.constants import (
    FLASH_PARTS,
    LOCATIONS,
    FASTBOOT,
    JUMPDRIVE,
)
from ombootstrap.exec.cmd import run_root_cmd
from ombootstrap.exec.file import get_temp_dir
from ombootstrap.devices.device import get_profile_device
from ombootstrap.flavours.flavour import get_profile_flavour
from ombootstrap.flavours.cli import profile_option
from ombootstrap.version.cli import _check_kbs_version
from ombootstrap.wrapper import enforce_wrap

from .fastboot import fastboot_flash
from .image import (
    dd_image,
    dump_aboot,
    dump_lk2nd,
    dump_qhypstub,
    get_image_path,
    losetup_destroy,
    losetup_rootfs_image,
    partprobe,
    shrink_fs,
)

ABOOT = FLASH_PARTS["ABOOT"]
LK2ND = FLASH_PARTS["LK2ND"]
QHYPSTUB = FLASH_PARTS["QHYPSTUB"]
FULL_IMG = FLASH_PARTS["FULL"]

DD = "dd"

FLASH_METHODS = [FASTBOOT, JUMPDRIVE, DD]


def find_jumpdrive(location: str) -> str:
    if location not in LOCATIONS:
        raise Exception(
            f"Invalid location {location}. Choose one of {', '.join(LOCATIONS)}"
        )
    dir = "/dev/disk/by-id"
    for file in os.listdir(dir):
        sanitized_file = file.replace("-", "").replace("_", "").lower()
        if f"jumpdrive{location.split('-')[0]}" in sanitized_file:
            return os.path.realpath(os.path.join(dir, file))
    raise Exception("Unable to discover Jumpdrive")


def test_blockdev(path: str):
    partprobe(path)
    result = run_root_cmd(["lsblk", path, "-o", "SIZE"], capture_output=True)
    if result.returncode != 0:
        raise Exception(f"Failed to lsblk {path}")
    if result.stdout == b"SIZE\n  0B\n":
        raise Exception(
            f"Disk {path} has a size of 0B. That probably means it is not available (e.g. no"
            "microSD inserted or no microSD card slot installed in the device) or corrupt or defect"
        )


def prepare_minimal_image(source_path: str, sector_size: int) -> str:
    minimal_image_dir = get_temp_dir(register_cleanup=True)
    minimal_image_path = os.path.join(
        minimal_image_dir, f"minimal-{os.path.basename(source_path)}"
    )
    logging.info(
        f"Copying image {os.path.basename(source_path)} to {minimal_image_dir} for shrinking"
    )
    shutil.copyfile(source_path, minimal_image_path)

    loop_device = losetup_rootfs_image(minimal_image_path, sector_size)
    partprobe(loop_device)
    shrink_fs(loop_device, minimal_image_path, sector_size)
    losetup_destroy(loop_device)
    return minimal_image_path


@click.command(name="flash")
@profile_option
@click.option("-m", "--method", type=click.Choice(FLASH_METHODS))
@click.option(
    "--split-size",
    help="Chunk size when splitting the image into sparse files via fastboot",
)
@click.option(
    "--shrink/--no-shrink",
    is_flag=True,
    default=True,
    help="Copy and shrink the image file to minimal size",
)
@click.option(
    "-b",
    "--sector-size",
    type=int,
    help="Override the device's sector size",
    default=None,
)
@click.option(
    "--confirm",
    is_flag=True,
    help="Ask for confirmation before executing fastboot commands",
)
@click.argument("what", type=click.Choice(list(FLASH_PARTS.values())))
@click.argument("location", type=str, required=False)
def cmd_flash(
    what: str,
    location: str,
    method: Optional[str] = None,
    split_size: Optional[str] = None,
    profile: Optional[str] = None,
    shrink: bool = True,
    sector_size: Optional[int] = None,
    confirm: bool = False,
):
    """
    Flash a partition onto a device.

    The syntax of LOCATION depends on the flashing method and is usually only required for flashing "full":

    \b
    - fastboot: the regular fastboot partition identifier. Usually "userdata"
    - dd: a path to a block device
    - jumpdrive: one of "emmc", "sdcard" or a path to a block device
    """
    enforce_wrap()
    _check_kbs_version(init_pkgbuilds=True)
    device = get_profile_device(profile)
    flavour = get_profile_flavour(profile).name
    device_image_path = get_image_path(device, flavour)

    deviceinfo = device.parse_deviceinfo()
    sector_size = sector_size or device.get_image_sectorsize_default()
    method = method or deviceinfo.flash_method

    if what not in FLASH_PARTS.values():
        raise Exception(
            f'Unknown what "{what}", must be one of {", ".join(FLASH_PARTS.values())}'
        )

    if location and location.startswith("aboot"):
        raise Exception(
            "You're trying to flash something "
            f"to your aboot partition ({location!r}), "
            "which contains the android bootloader itself.\n"
            "This will brick your phone and is not what you want.\n"
            'Aborting.\nDid you mean to flash to "boot"?'
        )

    if what == FULL_IMG:
        path = ""
        if method not in FLASH_METHODS:
            raise Exception(f"Flash method {method} not supported!")
        if not location:
            raise Exception(
                f"You need to specify a location to flash {what} to"
            )
        path = ""
        image_path = (
            prepare_minimal_image(device_image_path, sector_size)
            if shrink
            else device_image_path
        )
        if method == FASTBOOT:
            fastboot_flash(
                partition=location,
                file=image_path,
                sparse_size=split_size if split_size is not None else "100M",
                confirm=confirm,
            )
        elif method in [JUMPDRIVE, DD]:
            if (
                method == DD
                or location.startswith("/")
                or (location not in LOCATIONS and os.path.exists(location))
            ):
                path = location
            elif method == JUMPDRIVE:
                path = find_jumpdrive(location)
            test_blockdev(path)
            if dd_image(input=image_path, output=path).returncode != 0:
                raise Exception(f"Failed to flash {image_path} to {path}")
        else:
            raise Exception(f'Unhandled flash method "{method}" for "{what}"')
    else:
        if method and method != FASTBOOT:
            raise Exception(
                f'Flashing "{what}" with method "{method}" not supported, try no parameter or "{FASTBOOT}"'
            )
        loop_device = losetup_rootfs_image(device_image_path, sector_size)
        if what == ABOOT:
            path = dump_aboot(f"{loop_device}p1")
            fastboot_flash(location or "boot", path, confirm=confirm)
        elif what == LK2ND:
            path = dump_lk2nd(f"{loop_device}p1")
            fastboot_flash(location or "lk2nd", path, confirm=confirm)
        elif what == QHYPSTUB:
            path = dump_qhypstub(f"{loop_device}p1")
            fastboot_flash(location or "qhypstub", path, confirm=confirm)
        else:
            raise Exception(
                f'Unknown what "{what}", this must be a bug in ombootstrap!'
            )
