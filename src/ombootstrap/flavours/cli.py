import click
import logging

from json import dumps as json_dump
from typing import Optional

from ombootstrap.config.cli import resolve_profile_field
from ombootstrap.config.state import config
from ombootstrap.utils import color_mark_selected, colors_supported
from ombootstrap.version.cli import _check_kbs_version

from .flavour import get_flavours, get_flavour

profile_option = click.option(
    "-p",
    "--profile",
    help="name of the profile to use",
    required=False,
    default=None,
)


@click.command(name="flavours")
@click.option(
    "-j", "--json", is_flag=True, help="output machine-parsable JSON format"
)
@click.option(
    "--output-file",
    type=click.Path(exists=False, file_okay=True),
    help="Dump JSON to file",
)
def cmd_flavours(json: bool = False, output_file: Optional[str] = None):
    "list information about available flavours"
    results = []
    json_results = {}
    profile_flavour = None
    interactive_json = json and not output_file
    use_colors = (
        colors_supported(config.runtime.colors) and not interactive_json
    )
    profile_name = config.file.profiles.current
    selected, inherited_from = None, None
    if output_file:
        json = True
    _check_kbs_version(init_pkgbuilds=False)
    flavours = get_flavours()
    if not flavours:
        raise Exception("No flavours found!")
    if not interactive_json:
        try:
            selected, inherited_from = resolve_profile_field(
                None, profile_name, "flavour", config.file.profiles
            )
            if selected:
                profile_flavour = get_flavour(selected)
        except Exception as ex:
            logging.debug(
                f"Failed to get profile flavour for marking as currently selected, continuing anyway. Exception: {ex}"
            )
    for name in sorted(flavours.keys()):
        f = flavours[name]
        try:
            f.parse_flavourinfo()
        except Exception as ex:
            logging.debug(
                f"A problem happened while parsing flavourinfo for {name}, continuing anyway. Exception: {ex}"
            )
        if not interactive_json:
            snippet = f.nice_str(newlines=True, colors=use_colors)
            if profile_flavour == f:
                snippet = color_mark_selected(
                    snippet, profile_name or "[unknown]", inherited_from
                )
            snippet += "\n"
            results += snippet.split("\n")
        if json:
            d = dict(f)
            d["description"] = (
                f.flavour_info.description
                if (f.flavour_info and f.flavour_info.description)
                else f.description
            )
            if "flavour_info" in d and d["flavour_info"]:
                for k in set(d["flavour_info"].keys()) - set(["description"]):
                    d[k] = d["flavour_info"][k]
            del d["flavour_info"]
            d["pkgbuild"] = f.pkgbuild.path if f.pkgbuild else None
            d["package"] = f.pkgbuild.name
            d["arches"] = sorted(f.pkgbuild.arches) if f.pkgbuild else None
            json_results[name] = d
    print()
    if output_file:
        with open(output_file, "w") as fd:
            fd.write(json_dump(json_results))
    if interactive_json:
        print(json_dump(json_results, indent=4))
    else:
        for r in results:
            print(r)
