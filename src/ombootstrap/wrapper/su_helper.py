import click
import pwd
import os

from ombootstrap.logger import logging, setup_logging

from ombootstrap.constants import WRAPPER_ENV_VAR
from ombootstrap.exec.cmd import run_cmd, flatten_shell_script
from ombootstrap.exec.file import chown


@click.command("ombs_su")
@click.option(
    "--username",
    default="omarchy",
    help="The user's name. If --uid is provided, the user's uid will be changed to this in passwd",
)
@click.option(
    "--uid",
    default=1000,
    type=int,
    help="uid to change $username to and run as",
)
@click.argument("cmd", type=str, nargs=-1)
def ombootstrap_su(cmd: list[str], uid: int = 1000, username: str = "omarchy"):
    "Changes `username`'s uid to `uid` and executes ombootstrap as that user"
    cmd = list(cmd)
    user = pwd.getpwnam(username)
    home = user.pw_dir
    if uid != user.pw_uid:
        run_cmd(["usermod", "-o", "-u", str(uid), username]).check_returncode()  # type: ignore[union-attr]
        chown(home, username, recursive=False)
    logging.debug(f"wrapper_su_helper: running {cmd} as {repr(username)}")
    env_inject = ["env", f"PATH={os.environ['PATH']}"]
    if WRAPPER_ENV_VAR in os.environ:
        env_inject.append(f"{WRAPPER_ENV_VAR}={os.environ[WRAPPER_ENV_VAR]}")
    su_cmd = [
        "sudo",
        *env_inject,
        "su",
        "-P",
        username,
        "-c",
        flatten_shell_script(
            cmd, wrap_in_shell_quote=True, shell_quote_items=True
        ),
    ]
    result = run_cmd(su_cmd, attach_tty=True)
    assert isinstance(result, int)
    exit(result)


if __name__ == "__main__":
    setup_logging(True)
    ombootstrap_su(prog_name="ombs_su")
