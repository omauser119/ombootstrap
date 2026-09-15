import logging
import os
import pathlib
import subprocess
import sys

from ombootstrap.config.state import config
from ombootstrap.exec.file import makedir

from .wrapper import Wrapper, WRAPPER_PATHS

VERSION_FILE = "docker_version.txt"
DOCKER_FILE = "Dockerfile"
DOCKER_PATHS = WRAPPER_PATHS.copy()


def docker_volumes_args(volume_mappings: dict[str, str]) -> list[str]:
    result = []
    for source, destination in volume_mappings.items():
        result += ["-v", f"{source}:{destination}:z"]
    return result


class DockerWrapper(Wrapper):
    type: str = "docker"

    def wrap(self):
        super().wrap()
        script_path = config.runtime.script_source_dir
        assert script_path
        docker_path = script_path
        tried = [docker_path]
        if not os.path.exists(os.path.join(docker_path, DOCKER_FILE)):
            docker_path = os.path.realpath(os.path.join(script_path, "../.."))
            tried.append(docker_path)
        if not os.path.exists(os.path.join(docker_path, DOCKER_FILE)):
            _par_dir = os.path.dirname(script_path)
            # handle venv
            if os.path.basename(_par_dir) == "site-packages":
                _path = os.path.join(_par_dir, "../../../..")
                docker_path = os.path.realpath(_path)
                tried.append(f"{_path} => {docker_path}")
                logging.debug(
                    f"{DOCKER_FILE!r} not found at {script_path!r}, trying {docker_path!r}"
                )
        version_file = os.path.join(script_path, "../..", VERSION_FILE)
        if not os.path.exists(version_file):
            _vfile = os.path.join(docker_path, VERSION_FILE)
            logging.warning(
                f"{VERSION_FILE} not found at {version_file!r}."
                f"\nTrying {_vfile!r}"
                "\nDid you use `pip install .` instead of `pip install -e .`?"
            )
            if os.path.exists(_vfile):
                version_file = _vfile
        if os.path.exists(version_file):
            with open(version_file) as fd:
                version = fd.read().replace("\n", "").strip()
            logging.debug(f"Read docker tag {version} from {version_file}")
        else:
            version = "BUILD"
            logging.error(
                f"'{script_path}/{VERSION_FILE}' doesn't exist, defaulting docker tag to {version}!"
                "\nThis installation is potentially broken!"
                "\nDid you use `pip install .` instead of `pip install -e .` to install Ombootstrap?"
                f"Tried locations: {[version_file, _vfile]}"
            )
        tag = f"ombootstrap:{version.lower()}"
        if version == "BUILD":
            logging.info(f'Building docker image "{tag}"')
            cmd = [
                "docker",
                "build",
                "--pull",
                "--network",
                "host",
                ".",
                "-t",
                tag,
            ] + (["-q"] if not config.runtime.verbose else [])
            _dfile = os.path.join(docker_path, DOCKER_FILE)
            if not os.path.exists(_dfile):
                _sep = "\n -"
                raise Exception(
                    f"{DOCKER_FILE!r} not found. Tried locations:"
                    + (
                        _sep.join(
                            ["", *[repr(f"{p}/{DOCKER_FILE}") for p in tried]]
                        )
                    )
                )
            logging.debug(
                f"Running docker cmd (chdir={script_path!r}) : "
                + " ".join(cmd)
            )
            mute_docker = not config.runtime.verbose
            result = subprocess.run(
                cmd,
                cwd=docker_path,
                capture_output=mute_docker,
            )
            if result.returncode != 0:
                error_msg = (
                    ("\n" + result.stderr.decode() + "\n")
                    if mute_docker
                    else ""
                )
                logging.fatal(
                    f"Docker error: {error_msg}Failed to build docker image: see errors above: ^^^^"
                )
                exit(1)
        else:
            # Check if the image for the version already exists
            result = subprocess.run(
                [
                    "docker",
                    "images",
                    "-q",
                    tag,
                ],
                capture_output=True,
            )
            if result.stdout == b"":
                logging.info(
                    f"Pulling ombootstrap docker image version '{version}'"
                )
                subprocess.run(
                    [
                        "docker",
                        "pull",
                        tag,
                    ]
                )
        container_name = f"ombootstrap-{self.uuid}"

        wrapped_config = self.generate_wrapper_config()

        target_user = "root" if config.runtime.uid == 0 else "omarchy"
        target_home = (
            "/root" if target_user == "root" else f"/home/{target_user}"
        )

        ssh_dir = os.path.join(pathlib.Path.home(), ".ssh")
        if not os.path.exists(ssh_dir):
            os.makedirs(ssh_dir, mode=0o700)
        volumes = self.get_bind_mounts_default(
            wrapped_config, ssh_dir=ssh_dir, target_home=target_home
        )
        for vol_name, vol_dest in DOCKER_PATHS.items():
            vol_src = config.get_path(vol_name)
            makedir(vol_src)
            volumes[vol_src] = vol_dest
        docker_cmd = (
            [
                "docker",
                "run",
                "--net",
                "host",
                "--name",
                container_name,
                "--rm",
                "--interactive",
                "--tty",
                "--privileged",
            ]
            + docker_volumes_args(volumes)
            + [tag]
        )

        kupfer_cmd = [
            "ombs",
            "--config",
            volumes[wrapped_config],
        ]
        kupfer_cmd += self.argv_override or self.filter_args_wrapper(
            sys.argv[1:]
        )
        if config.runtime.uid:
            kupfer_cmd = [
                "wrapper_su_helper",
                "--uid",
                str(config.runtime.uid),
                "--username",
                "omarchy",
                "--",
            ] + kupfer_cmd

        cmd = docker_cmd + kupfer_cmd
        logging.debug("Wrapping in docker:" + repr(cmd))
        result = subprocess.run(cmd)
        if self.should_exit:
            exit(result.returncode)
        return result.returncode

    def stop(self):
        subprocess.run(
            [
                "docker",
                "kill",
                self.identifier,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


wrapper = DockerWrapper()
