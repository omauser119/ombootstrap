from typing import Optional
import logging
import os
import pathlib
import click

from ombootstrap.config.state import config
from ombootstrap.constants import (
    SSH_COMMON_OPTIONS,
    SSH_DEFAULT_HOST,
    SSH_DEFAULT_PORT,
)
from ombootstrap.exec.cmd import run_cmd
from ombootstrap.exec.file import write_file
from ombootstrap.chroot.abstract import Chroot
from ombootstrap.wrapper import check_programs_wrap


@click.command(name="ssh")
@click.argument("cmd", nargs=-1)
@click.option("--user", "-u", help="the SSH username", default=None)
@click.option("--host", "-h", help="the SSH host", default=SSH_DEFAULT_HOST)
@click.option(
    "--port", "-p", help="the SSH port", type=int, default=SSH_DEFAULT_PORT
)
def cmd_ssh(cmd: list[str], user: str, host: str, port: int):
    """Establish SSH connection to device"""
    run_ssh_command(list(cmd), user=user, host=host, port=port, alloc_tty=True)


def run_ssh_command(
    cmd: list[str] = [],
    user: Optional[str] = None,
    host: str = SSH_DEFAULT_HOST,
    port: int = SSH_DEFAULT_PORT,
    alloc_tty: bool = True,
):
    check_programs_wrap(["ssh"])
    if not user:
        user = config.get_profile()["username"]
    keys = find_ssh_keys()
    extra_args = []
    if len(keys) > 0:
        extra_args += ["-i", keys[0]]
    if config.runtime.verbose:
        extra_args += ["-v"]
    if alloc_tty:
        extra_args += ["-t"]
    hoststr = f"{(user + '@') if user else ''}{host}"
    logging.info(f"Opening SSH connection to {hoststr} ({port})")
    logging.debug(f"ssh: trying to run {cmd} on {hoststr}")
    full_cmd = (
        [
            "ssh",
        ]
        + extra_args
        + SSH_COMMON_OPTIONS
        + [
            "-p",
            str(port),
            hoststr,
            "--",
        ]
        + cmd
    )
    logging.debug(f"running cmd: {full_cmd}")
    return run_cmd(full_cmd)


def scp_put_files(
    src: list[str],
    dst: str,
    user: Optional[str] = None,
    host: str = SSH_DEFAULT_HOST,
    port: int = SSH_DEFAULT_PORT,
):
    check_programs_wrap(["scp"])
    if not user:
        user = config.get_profile()["username"]
    keys = find_ssh_keys()
    key_args = []
    if len(keys) > 0:
        key_args = ["-i", keys[0]]
    cmd = (
        [
            "scp",
        ]
        + key_args
        + SSH_COMMON_OPTIONS
        + [
            "-P",
            str(port),
        ]
        + src
        + [
            f"{user}@{host}:{dst}",
        ]
    )
    logging.info(f"Copying files to {user}@{host}:{dst}:\n{src}")
    logging.debug(f"running cmd: {cmd}")
    return run_cmd(cmd)


def find_ssh_keys():
    dir = os.path.join(pathlib.Path.home(), ".ssh")
    if not os.path.exists(dir):
        return []
    keys = []
    for file in os.listdir(dir):
        if file.startswith("id_") and not file.endswith(".pub"):
            keys.append(os.path.join(dir, file))
    return keys


def copy_ssh_keys(chroot: Chroot, user: str, allow_fail: bool = False):
    check_programs_wrap(["ssh-keygen"])
    ssh_dir_relative = os.path.join("/home", user, ".ssh")
    ssh_dir = chroot.get_path(ssh_dir_relative)
    authorized_keys_file_rel = os.path.join(
        ssh_dir_relative, "authorized_keys"
    )
    authorized_keys_file = chroot.get_path(authorized_keys_file_rel)

    keys = find_ssh_keys()
    if len(keys) == 0:
        logging.warning("Could not find any ssh key to copy")
        create = click.confirm(
            "Do you want me to generate an ssh key for you?", True
        )
        if not create:
            return
        result = run_cmd(
            [
                "ssh-keygen",
                "-f",
                os.path.join(pathlib.Path.home(), ".ssh", "id_ed25519_kupfer"),
                "-t",
                "ed25519",
                "-C",
                "kupfer",
                "-N",
                "",
            ]
        )
        if result.returncode != 0:  # type: ignore
            logging.fatal("Failed to generate ssh key")
        keys = find_ssh_keys()

    if not keys:
        logging.warning("No SSH keys to be copied. Skipping.")
        return

    auth_key_lines = []
    for key in keys:
        pub = f"{key}.pub"
        if not os.path.exists(pub):
            logging.debug(f"Skipping key {key}: {pub} not found")
            continue
        try:
            with open(pub, "r") as file:
                contents = file.read()
                if not contents.strip():
                    continue
                auth_key_lines.append(contents)
        except Exception as ex:
            logging.warning(f"Could not read ssh pub key {pub}", exc_info=ex)
            continue

    if not os.path.exists(ssh_dir):
        logging.info(
            f"Creating {ssh_dir_relative!r} dir in chroot {chroot.path!r}"
        )
        chroot.run_cmd(
            ["mkdir", "-p", "-m", "700", ssh_dir_relative], switch_user=user
        )
    logging.info(f"Writing SSH pub keys to {authorized_keys_file}")
    try:
        write_file(
            authorized_keys_file,
            "\n".join(auth_key_lines),
            user=str(chroot.get_uid(user)),
            mode="644",
        )
    except Exception as ex:
        logging.error(
            f"Failed to write SSH authorized_keys_file at {authorized_keys_file!r}:",
            exc_info=ex,
        )
        if allow_fail:
            return
        raise ex from ex
