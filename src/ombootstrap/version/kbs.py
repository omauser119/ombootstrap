import logging
import os

from typing import NamedTuple, Union

from ombootstrap.distro.repo_config import ReposConfigFile
from ombootstrap.utils import git
from .compare import semver_compare, VerComp

KBS_VERSION: Union[str, None] = None

KBS_VERSION_MIN_KEY = "ombs_min_version"
KBS_VERSION_CI_MIN_KEY = "ombs_ci_version"


def get_kbs_version(kbs_folder: Union[str, None] = None) -> Union[str, None]:
    from importlib.metadata import version

    try:
        return "v" + version("ombootstrap")
    except Exception:
        return None


class GitVersionSplit(NamedTuple):
    base_version: str
    git_commit_count: int | None
    git_commit_ref: str | None


def split_git_version_info(ver: str) -> GitVersionSplit:
    splits = ver.rsplit("-", 2)
    if len(splits) > 2:
        if (
            splits[-2].isdecimal()
            and splits[-1].startswith("g")
            and (git_suffix := splits[-1][1:])
            and git_suffix.isalnum()
        ):
            ver_orig = ver
            ver = "-".join(splits[:-2])
            logging.debug(
                f"Ombootstrap version: discarding git suffix {git_suffix!r} from {ver_orig!r}: {ver!r}"
            )
            return GitVersionSplit(ver, int(splits[-2]), splits[-1])
    return GitVersionSplit(ver, None, None)


def compare_kbs_version_generic(
    kbs_version: str | None,
    minimum_ver: str | None,
    *,
    handle_git_suffix: bool = True,
) -> Union[VerComp, None]:
    if minimum_ver is None or kbs_version is None:
        return None
    kbs_version, minimum_ver = (
        s.lstrip("v") for s in (kbs_version, minimum_ver)
    )
    if kbs_version == minimum_ver:
        return VerComp.EQUAL
    try:
        if not handle_git_suffix:
            return semver_compare(minimum_ver, kbs_version)
        min_splits = split_git_version_info(minimum_ver)
        kbs_splits = split_git_version_info(kbs_version)
        base_comp = semver_compare(
            min_splits.base_version, kbs_splits.base_version
        )
        if base_comp != VerComp.EQUAL:
            return base_comp
        if (min_commits := min_splits.git_commit_count or 0) != (
            kbs_commits := kbs_splits.git_commit_count or 0
        ):
            return (
                VerComp.RIGHT_NEWER
                if min_commits < kbs_commits
                else VerComp.RIGHT_OLDER
            )
        logging.warning(
            f"Ombootstrap version {kbs_version!r} differs from {minimum_ver!r} only in git commit hash, treating as equal"
        )
        return VerComp.EQUAL
    except Exception as ex:
        logging.warning(
            f"Failed to compare Ombootstrap version {kbs_version!r} to required minimum version {minimum_ver!r}: {ex!r}"
        )
        return None


def compare_kbs_version(
    kbs_version: str, repo_config: ReposConfigFile
) -> bool | None:
    """Return True if Ombootstrap is new enough for PKGBUILDs."""
    minimum_ver = repo_config.get(KBS_VERSION_MIN_KEY)
    kbs_state = compare_kbs_version_generic(
        kbs_version=kbs_version, minimum_ver=minimum_ver
    )
    if not minimum_ver:
        logging.warning(
            f"Can't check PKGBUILDs for compatible Ombootstrap version as {KBS_VERSION_MIN_KEY!r} "
            "is empty in PKGBUILDs repos.yml"
        )
        return None
    if kbs_state == VerComp.RIGHT_OLDER:
        logging.warning(
            f"Ombootstrap version {kbs_version!r} is older than {minimum_ver!r} required by PKGBUILDs.\n"
            "Some functionality may randomly be broken.\nYou have been warned."
        )
        return False
    return True


def compare_kbs_ci_version(
    kbs_version: str, repo_config: ReposConfigFile
) -> bool | None:
    """Return True if Ombootstrap is new enough for PKGBUILDs in CI."""
    minimum_ver = repo_config.get(KBS_VERSION_CI_MIN_KEY)
    if not minimum_ver:
        logging.warning(
            "Can't check PKGBUILDs for compatible Ombootstrap CI version: "
            f"Minimum CI Ombootstrap version {KBS_VERSION_CI_MIN_KEY!r} is empty in PKGBUILDs repos.yml!"
        )
        return None
    kbs_state = compare_kbs_version_generic(
        kbs_version=kbs_version, minimum_ver=minimum_ver
    )
    if kbs_state == VerComp.RIGHT_OLDER:
        logging.error(
            f"Ombootstrap CI version {kbs_version!r} is older than {minimum_ver!r} required by PKGBUILDs ombs_ci_version!\n"
            "CI is likely to fail!"
        )
        return False
    return True
