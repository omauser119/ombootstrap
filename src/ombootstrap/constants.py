from .typehelpers import TypeAlias

FASTBOOT = "fastboot"
FLASH_PARTS = {
    "FULL": "full",
    "ABOOT": "abootimg",
    "LK2ND": "lk2nd",
    "QHYPSTUB": "qhypstub",
}
EMMC = "emmc"
MICROSD = "microsd"
LOCATIONS = [EMMC, MICROSD]

JUMPDRIVE = "jumpdrive"
JUMPDRIVE_VERSION = "0.8"

BASE_LOCAL_PACKAGES: list[str] = [
    "base-kupfer",
]

BASE_PACKAGES: list[str] = BASE_LOCAL_PACKAGES + [
    "base",
    "nano",
    "vim",
]

POST_INSTALL_CMDS = [
    "ombootstrap-config apply",
    "ombootstrap-config --user apply",
]

REPOS_CONFIG_FILE = "repos.yml"
REPOS_CONFIG_FILE_USER = "repos.local.yml"

REPOSITORIES = [
    "boot",
    "cross",
    "device",
    "firmware",
    "linux",
    "main",
    "omarchy",
]

DEFAULT_PACKAGE_BRANCH = "dev"
OMBOOTSTRAP_BRANCH_MARKER = "%ombootstrap_branch%"
OMBOOTSTRAP_HTTPS_BASE = (
    f"https://pkgs.omarchy.org/{OMBOOTSTRAP_BRANCH_MARKER}"
)
OMBOOTSTRAP_HTTPS = OMBOOTSTRAP_HTTPS_BASE + "/$arch/$repo"
# Compatibility aliases used by the inherited distro/repository modules.
KUPFER_BRANCH_MARKER = OMBOOTSTRAP_BRANCH_MARKER
KUPFER_HTTPS_BASE = OMBOOTSTRAP_HTTPS_BASE
KUPFER_HTTPS = OMBOOTSTRAP_HTTPS

Arch: TypeAlias = str
ARCHES = [
    "x86_64",
    "aarch64",
    "armv7h",
]

DistroArch: TypeAlias = Arch
TargetArch: TypeAlias = Arch

ALARM_REPOS = {
    "core": "http://mirror.archlinuxarm.org/$arch/$repo",
    "extra": "http://mirror.archlinuxarm.org/$arch/$repo",
    "alarm": "http://mirror.archlinuxarm.org/$arch/$repo",
    "aur": "http://mirror.archlinuxarm.org/$arch/$repo",
}

BASE_DISTROS: dict[DistroArch, dict[str, dict[str, str]]] = {
    "x86_64": {
        "repos": {
            "core": "https://geo.mirror.pkgbuild.com/$repo/os/$arch",
            "extra": "https://geo.mirror.pkgbuild.com/$repo/os/$arch",
        },
    },
    "aarch64": {
        "repos": ALARM_REPOS,
    },
    "armv7h": {
        "repos": ALARM_REPOS,
    },
}

COMPILE_ARCHES: dict[Arch, str] = {
    "x86_64": "amd64",
    "aarch64": "arm64",
    "armv7h": "arm",
}

GCC_HOSTSPECS: dict[DistroArch, dict[TargetArch, str]] = {
    "x86_64": {
        "x86_64": "x86_64-pc-linux-gnu",
        "aarch64": "aarch64-unknown-linux-gnu",
        "armv7h": "arm-unknown-linux-gnueabihf",
    },
    "aarch64": {
        "aarch64": "aarch64-unknown-linux-gnu",
    },
    "armv7h": {"armv7h": "armv7l-unknown-linux-gnueabihf"},
}

CFLAGS_GENERAL = ["-O2", "-pipe", "-fstack-protector-strong"]
CFLAGS_ALARM = [
    " -fno-plt",
    "-fexceptions",
    "-Wp,-D_FORTIFY_SOURCE=2",
    "-Wformat",
    "-Werror=format-security",
    "-fstack-clash-protection",
]
CFLAGS_ARCHES: dict[Arch, list[str]] = {
    "x86_64": ["-march=x86-64", "-mtune=generic"],
    "aarch64": [
        "-march=armv8-a",
    ]
    + CFLAGS_ALARM,
    "armv7h": [
        "-march=armv7-a",
        "-mfloat-abi=hard",
        "-mfpu=neon",
    ]
    + CFLAGS_ALARM,
}

QEMU_ARCHES: dict[Arch, str] = {
    "x86_64": "x86_64",
    "aarch64": "aarch64",
    "armv7h": "arm",
}

QEMU_BINFMT_PKGS = ["qemu-user-static-bin", "binfmt-qemu-static"]
CROSSDIRECT_PKGS = ["crossdirect"] + QEMU_BINFMT_PKGS

SSH_DEFAULT_HOST = "172.16.42.1"
SSH_DEFAULT_PORT = 22
SSH_COMMON_OPTIONS = [
    "-o",
    "GlobalKnownHostsFile=/dev/null",
    "-o",
    "UserKnownHostsFile=/dev/null",
    "-o",
    "StrictHostKeyChecking=no",
]

CHROOT_PATHS = {
    "chroots": "/chroots",
    "jumpdrive": "/var/cache/jumpdrive",
    "pacman": "/pacman",
    "packages": "/packages",
    "pkgbuilds": "/pkgbuilds",
    "images": "/images",
}

WRAPPER_TYPES = [
    "none",
    "docker",
]
WRAPPER_ENV_VAR = "OMBOOTSTRAP_WRAPPED"

MAKEPKG_CMD = [
    "makepkg",
    "--noconfirm",
    "--ignorearch",
    "--needed",
]

SRCINFO_FILE = ".SRCINFO"
SRCINFO_METADATA_FILE = ".srcinfo_meta.json"
SRCINFO_INITIALISED_FILE = ".srcinfo_initialised.json"

SRCINFO_TARBALL_FILE = "srcinfos.tar.gz"
SRCINFO_TARBALL_URL = f"{KUPFER_HTTPS_BASE}/{SRCINFO_TARBALL_FILE}"

FLAVOUR_INFO_FILE = "flavourinfo.json"
FLAVOUR_DESCRIPTION_PREFIX = "ombootstrap flavour:"
