import logging
import os
import sys

from glob import glob
from typing import ClassVar

from ombootstrap.constants import Arch
from ombootstrap.exec.cmd import run_root_cmd
from ombootstrap.exec.file import makedir, remove_file, root_makedir
from ombootstrap.config.state import config

from .abstract import Chroot, get_chroot
from .helpers import base_chroot_name


class BaseChroot(Chroot):
    _copy_base: ClassVar[bool] = False

    def create_rootfs(self, reset, pacman_conf_target, active_previously):
        if reset and os.path.exists(self.path):
            logging.info(f"Resetting {self.name}")
            for dir in glob(os.path.join(self.path, "*")):
                remove_file(dir, recursive=True)
        makedir(config.get_path("chroots"))
        root_makedir(self.get_path())

        self.write_pacman_conf()
        self.mount_pacman_cache()

        # A previously interrupted pacstrap can leave a local database from
        # an older pacman format in the rootfs. pacstrap invokes pacman
        # against that database and aborts before it has a chance to install
        # anything, so upgrade it before starting a new transaction.
        pacman_db = self.get_path("var/lib/pacman")
        if os.path.isdir(os.path.join(pacman_db, "local")):
            logging.info(f"Upgrading pacman database in {self.name}")
            db_upgrade = run_root_cmd(
                [
                    "pacman-db-upgrade",
                    "--root",
                    self.path,
                    "--dbpath",
                    pacman_db,
                ],
                stderr=sys.stdout,
            )
            if db_upgrade.returncode != 0:
                raise Exception(
                    f'Failed to upgrade pacman database in chroot "{self.name}"'
                )

        logging.info(
            f"Pacstrapping chroot {self.name}: {', '.join(self.base_packages)}"
        )

        result = run_root_cmd(
            [
                "pacstrap",
                "-C",
                pacman_conf_target,
                "-G",
                self.path,
                *self.base_packages,
                "--needed",
                "--overwrite=*",
                "-yyuu",
            ],
            stderr=sys.stdout,
        )
        if result.returncode != 0:
            raise Exception(f'Failed to initialize chroot "{self.name}"')
        self.initialized = True


def get_base_chroot(arch: Arch) -> BaseChroot:
    name = base_chroot_name(arch)
    args = dict(arch=arch, copy_base=False)
    chroot = get_chroot(
        name, initialize=False, chroot_class=BaseChroot, chroot_args=args
    )
    assert isinstance(chroot, BaseChroot)
    return chroot
