import os
import urllib.request
import click

from typing import Optional

from ombootstrap.config.state import config
from ombootstrap.constants import (
    FLASH_PARTS,
    FASTBOOT,
    JUMPDRIVE,
    JUMPDRIVE_VERSION,
)
from ombootstrap.exec.file import makedir
from ombootstrap.devices.device import get_profile_device
from ombootstrap.flavours.flavour import get_profile_flavour
from ombootstrap.flavours.cli import profile_option
from ombootstrap.version.cli import _check_kbs_version
from ombootstrap.wrapper import enforce_wrap

from .fastboot import fastboot_boot, fastboot_erase
from .image import (
    get_device_name,
    losetup_rootfs_image,
    get_image_path,
    dump_aboot,
    dump_lk2nd,
)

LK2ND = FLASH_PARTS["LK2ND"]
ABOOT = FLASH_PARTS["ABOOT"]

BOOT_TYPES = [ABOOT, LK2ND, JUMPDRIVE]


@click.command(name="boot")
@profile_option
@click.argument(
    "type", required=False, default=ABOOT, type=click.Choice(BOOT_TYPES)
)
@click.option(
    "-b",
    "--sector-size",
    type=int,
    help="Override the device's sector size",
    default=None,
)
@click.option(
    "--erase-dtbo/--no-erase-dtbo",
    is_flag=True,
    default=False,
    show_default=True,
    help="Erase the DTBO partition before flashing",
)
@click.option(
    "--confirm",
    is_flag=True,
    help="Ask for confirmation before executing fastboot commands",
)
def cmd_boot(
    type: str,
    profile: Optional[str] = None,
    sector_size: Optional[int] = None,
    erase_dtbo: bool = False,
    confirm: bool = False,
):
    """Boot JumpDrive or the Kupfer aboot image. DTBO erasure must be explicitly requested."""
    enforce_wrap()
    _check_kbs_version(init_pkgbuilds=True)
    device = get_profile_device(profile)
    flavour = get_profile_flavour(profile).name
    deviceinfo = device.parse_deviceinfo()
    sector_size = sector_size or device.get_image_sectorsize_default()
    if not sector_size:
        raise Exception(
            f"Device {device.name} has no rootfs_image_sector_size specified"
        )
    image_path = get_image_path(device, flavour)
    strategy = deviceinfo.flash_method
    if not strategy:
        raise Exception(f"Device {device.name} has no flash strategy defined")

    if strategy == FASTBOOT:
        if type == JUMPDRIVE:
            file = f"boot-{get_device_name(device)}.img"
            path = os.path.join(config.get_path("jumpdrive"), file)
            makedir(os.path.dirname(path))
            if not os.path.exists(path):
                urllib.request.urlretrieve(
                    f"https://github.com/dreemurrs-embedded/Jumpdrive/releases/download/{JUMPDRIVE_VERSION}/{file}",
                    path,
                )
        else:
            loop_device = losetup_rootfs_image(image_path, sector_size)
            if type == LK2ND:
                path = dump_lk2nd(loop_device + "p1")
            elif type == ABOOT:
                path = dump_aboot(loop_device + "p1")
            else:
                raise Exception(f"Unknown boot image type {type}")
        if erase_dtbo:
            fastboot_erase("dtbo", confirm=confirm)
        fastboot_boot(path, confirm=confirm)
    else:
        raise Exception(
            f'Unsupported flash strategy "{strategy}" for device {device.name}'
        )
