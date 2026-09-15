# Installation

Install Python 3, pip, venv, Git and the Arch packaging tools. On an Arch-based
host:

```sh
pacman -S base-devel arch-install-scripts python python-pip git --needed --noconfirm
```

Docker is optional. The default config builds with the host toolchain; on
another distribution, install Docker and set `wrapper.type = "docker"` in the
config if you want containerized builds.

Clone the fork and install it in a virtual environment:

```sh
git clone -b ombootstrap https://gitlab.com/ombootstrap/ombootstrap
cd Ombootstrap
python3 -m venv venv
venv/bin/pip install -e .
venv/bin/ombs --help
```

You can symlink `venv/bin/ombs` to `~/.local/bin/ombs`. The default package
source is bundled in the wheel; `ombs packages init` materializes it in the
configured cache and does not contact the old Kupfer package repository.
