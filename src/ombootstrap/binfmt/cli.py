import click
import os

from typing import Optional

from ombootstrap.constants import Arch, ARCHES

from .binfmt import binfmt_unregister, binfmt_is_registered

cmd_binfmt = click.Group(
    "binfmt",
    help="Manage qemu binfmt for executing foreign architecture binaries",
)
arches_arg = click.argument(
    "arches", type=click.Choice(ARCHES), nargs=-1, required=True
)
arches_arg_optional = click.argument(
    "arches", type=click.Choice(ARCHES), nargs=-1, required=False
)


@cmd_binfmt.command(
    "register", help="Register a binfmt handler with the kernel"
)
@arches_arg
def cmd_register(arches: list[Arch], disable_chroot: bool = False):
    from ..packages.build import build_enable_qemu_binfmt

    for arch in arches:
        build_enable_qemu_binfmt(arch)


@cmd_binfmt.command(
    "unregister", help="Unregister a binfmt handler from the kernel"
)
@arches_arg_optional
def cmd_unregister(arches: Optional[list[Arch]]):
    for arch in arches or ARCHES:
        binfmt_unregister(arch)


@cmd_binfmt.command(
    "status", help="Get the status of a binfmt handler from the kernel"
)
@arches_arg_optional
def cmd_status(arches: Optional[list[Arch]]):
    for arch in arches or ARCHES:
        native = arch == os.uname().machine
        active = binfmt_is_registered(arch)
        if native and not active:
            # boooring
            continue
        verb = click.style(
            "is" if active else "is NOT",
            fg="green" if (active ^ native) else "red",
            bold=True,
        )
        click.echo(
            f"Binfmt for {arch} {verb} set up! {'(host architecture!)' if native else ''}"
        )
