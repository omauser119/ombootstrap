import click
import logging

from ombootstrap.exec.cmd import run_cmd, CompletedProcess
from typing import Optional


def confirm_cmd(
    cmd: list[str],
    color="green",
    default=True,
    msg="Really execute fastboot cmd?",
) -> bool:
    return click.confirm(
        f"{click.style(msg, fg=color, bold=True)} {' '.join(cmd)}",
        default=default,
        abort=False,
    )


def fastboot_erase(target: str, confirm: bool = False):
    if not target:
        raise Exception(f"No fastboot erase target specified: {repr(target)}")
    cmd = [
        "fastboot",
        "erase",
        target,
    ]
    if confirm:
        if not confirm_cmd(
            cmd,
            msg=f'Really erase fastboot "{target}" partition?',
            color="yellow",
        ):
            raise Exception("user aborted")
    logging.info(f"Fastboot: Erasing {target}")
    run_cmd(
        cmd,
        capture_output=True,
    )


def fastboot_flash(
    partition: str,
    file: str,
    sparse_size: Optional[str] = None,
    confirm: bool = False,
):
    cmd = [
        "fastboot",
        *(["-S", sparse_size] if sparse_size is not None else []),
        "flash",
        partition,
        file,
    ]
    if confirm:
        if not confirm_cmd(cmd):
            raise Exception("user aborted")
    logging.info(f"Fastboot: Flashing {file} to {partition}")
    result = run_cmd(cmd)
    assert isinstance(result, CompletedProcess)
    if result.returncode != 0:
        raise Exception(f"Failed to flash {file}")


def fastboot_boot(file, confirm: bool = False):
    cmd = [
        "fastboot",
        "boot",
        file,
    ]
    if confirm:
        if not confirm_cmd(cmd):
            raise Exception("user aborted")
    logging.info(f"Fastboot: booting {file}")
    result = run_cmd(cmd)
    assert isinstance(result, CompletedProcess)
    if result.returncode != 0:
        raise Exception(f"Failed to boot {file} using fastboot")
