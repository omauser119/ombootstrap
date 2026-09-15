import atexit
import os
import uuid
import pathlib

from typing import Optional, Protocol

from ombootstrap.config.state import config
from ombootstrap.config.state import dump_file as dump_config_file
from ombootstrap.constants import CHROOT_PATHS, WRAPPER_ENV_VAR

WRAPPER_PATHS = CHROOT_PATHS | {
    "ccache": "/ccache",
    "rust": "/rust",
}


class WrapperProtocol(Protocol):
    """Wrappers wrap ombootstrap in some form of isolation from the host OS, i.e. docker or chroots"""

    def wrap(self):
        """Instructs the wrapper to reexecute ombootstrap in a wrapped environment"""

    def stop(self):
        """Instructs the wrapper to stop the wrapped instance and clean up"""

    def is_wrapped(self) -> bool:
        """
        Queries the wrapper whether it believes we're executing wrapped by it currently.
        Checks `env[OMBOOTSTRAP_WRAPPED] == self.type.capitalize()` by default.
        """


class Wrapper(WrapperProtocol):
    uuid: str
    identifier: str
    type: str
    wrapped_config_path: Optional[str]
    argv_override: Optional[list[str]]
    should_exit: bool
    atexit_registered: bool

    def __init__(
        self, random_id: Optional[str] = None, name: Optional[str] = None
    ):
        self.uuid = str(random_id or uuid.uuid4())
        self.identifier = name or f"ombootstrap-{self.uuid}"
        self.argv_override = None
        self.should_exit = True
        self.atexit_registered = False
        self.wrapped_config_path = None

    def filter_args_wrapper(self, args):
        """filter out -c/--config since it doesn't apply in wrapper"""
        results = []
        done = False
        for i, arg in enumerate(args):
            if done:
                break
            if arg[0] != "-":
                results += args[i:]
                done = True
                break
            for argname in ["--config", "-C"]:
                if arg.startswith(argname):
                    done = True
                    if (
                        arg.strip() != argname
                    ):  # arg is longer, assume --arg=value
                        offset = 1
                    else:
                        offset = 2
                    results += args[i + offset :]
                    break
            if not done:
                results.append(arg)
        return results

    def generate_wrapper_config(
        self,
        target_path: str = "/tmp/ombootstrap",
        paths: dict[str, str] = WRAPPER_PATHS,
        config_overrides: dict[str, dict] = {},
    ) -> str:
        wrapped_config = (
            f"{target_path.rstrip('/')}/{self.identifier}_wrapped.toml"
        )

        dump_config_file(
            file_path=wrapped_config,
            config=(
                config.file
                | {
                    "paths": paths,
                }
                | config_overrides
            ),
        )
        self.wrapped_config_path = wrapped_config
        return wrapped_config

    def at_exit(self):
        if self.wrapped_config_path:
            os.remove(self.wrapped_config_path)
        self.stop()
        self.atexit_registered = False

    def wrap(self):
        if not self.atexit_registered:
            atexit.register(self.at_exit)
            self.atexit_registered = True

    def stop(self):
        raise NotImplementedError()

    def is_wrapped(self):
        return os.getenv(WRAPPER_ENV_VAR) == self.type.upper()

    def get_bind_mounts_default(
        self,
        wrapped_config_path: Optional[str] = None,
        ssh_dir: Optional[str] = None,
        target_home: str = "/root",
    ):
        wrapped_config_path = wrapped_config_path or self.wrapped_config_path
        ssh_dir = ssh_dir or os.path.join(pathlib.Path.home(), ".ssh")
        assert wrapped_config_path
        mounts = {
            "/dev": "/dev",
            wrapped_config_path: f"{target_home}/.config/ombootstrap/ombootstrap.toml",
        }
        if ssh_dir:
            mounts |= {
                ssh_dir: f"{target_home}/.ssh",
            }
        return mounts
