import click
import logging

from ombootstrap.constants import REPOS_CONFIG_FILE
from ombootstrap.distro.repo_config import get_repo_config
from .kbs import get_kbs_version, compare_kbs_version, compare_kbs_ci_version


def _check_kbs_version(
    *, init_pkgbuilds: bool = False, ci_mode: bool = False
):  # quiet helper for other modules
    repo_config, repo_config_found = get_repo_config(
        initialize_pkgbuilds=init_pkgbuilds
    )
    if not repo_config_found:
        return
    kbs_version = get_kbs_version()
    if not kbs_version:
        return
    compare_kbs_version(kbs_version, repo_config)


@click.group("version", no_args_is_help=False, invoke_without_command=True)
@click.pass_context
def cmd_version(ctx: click.Context):
    """
    Print Ombootstrap version or check PKGBUILD compatibility
    """
    if not ctx.invoked_subcommand:
        ctx.invoke(cmd_version_show)


@cmd_version.command("show")
def cmd_version_show():
    """
    Print the current version and exit (default action)
    """
    version = get_kbs_version()
    if not version:
        logging.error(f"Failed to fetch Ombootstrap version: {version=}")
        exit(1)
    print(version)


@cmd_version.command("check")
@click.option(
    "--ci-mode",
    "--ci",
    is_flag=True,
    default=False,
    help="Compare local version against required Build-CI version",
)
def cmd_version_check(ci_mode: bool = False):
    """
    Compare Ombootstrap version against the minimum version from PKGBUILDs

    The bundled recipe tree contains a repos.yml file with the minimum
    Ombootstrap version needed by the recipes.

    Returns 0 if the KBS version is >= the minimum version.
    Returns 1 if the KBS version is smaller than the minimum version.
    Returns 2 on other failures, e.g. missing files.
    """
    kbs_version = get_kbs_version()
    if not kbs_version:
        logging.error("Can't compare Ombootstrap version as it is unavailable")
        exit(2)
    repo_config, file_found = get_repo_config(initialize_pkgbuilds=False)
    if not file_found:
        logging.error(
            f"{REPOS_CONFIG_FILE} not found in PKGBUILDs; cannot check Ombootstrap compatibility"
        )
        exit(2)
    res = compare_kbs_version(kbs_version=kbs_version, repo_config=repo_config)
    res_ci = True
    if ci_mode:
        res_ci = compare_kbs_ci_version(
            kbs_version=kbs_version, repo_config=repo_config
        )
        if res_ci is None:
            exit(2)
        if res_ci:
            logging.info("Ombootstrap CI version is new enough!")
    if res is None:
        exit(2)
    if res:
        logging.info(
            f"{'Success: ' if res_ci else ''}Ombootstrap version {kbs_version!r} is new enough for PKGBUILDs!"
        )
    exit(0 if res and res_ci else 1)
