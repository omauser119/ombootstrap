import click
import logging

from json import dumps as json_dump
from typing import Optional

from ombootstrap.config.state import config
from ombootstrap.config.cli import resolve_profile_field
from ombootstrap.utils import color_mark_selected, colors_supported
from ombootstrap.version.cli import _check_kbs_version

from .device import get_devices, get_device


@click.command(name="devices")
@click.option(
    "-j", "--json", is_flag=True, help="output machine-parsable JSON format"
)
@click.option(
    "--force-parse-deviceinfo/--no-parse-deviceinfo",
    is_flag=True,
    default=None,
    help="Force or disable deviceinfo parsing. The default is to try but continue if it fails.",
)
@click.option(
    "--download-packages/--no-download-packages",
    is_flag=True,
    default=False,
    help="Download packages while trying to parse deviceinfo",
)
@click.option(
    "--output-file",
    type=click.Path(exists=False, file_okay=True),
    help="Dump JSON to file",
)
def cmd_devices(
    json: bool = False,
    force_parse_deviceinfo: Optional[bool] = True,
    download_packages: bool = False,
    output_file: Optional[str] = None,
):
    "list the available devices and descriptions"
    _check_kbs_version(init_pkgbuilds=False)
    devices = get_devices()
    if not devices:
        raise Exception("No devices found!")
    profile_device = None
    profile_name = config.file.profiles.current
    selected, inherited_from = None, None
    try:
        selected, inherited_from = resolve_profile_field(
            None, profile_name, "device", config.file.profiles
        )
        if selected:
            profile_device = get_device(selected)
    except Exception as ex:
        logging.debug(
            f"Failed to get profile device for marking as currently selected, continuing anyway. Exception: {ex}"
        )
    output = [""]
    json_output = {}
    interactive_json = json and not output_file
    if output_file:
        json = True
    use_colors = colors_supported(
        False if interactive_json else config.runtime.colors
    )
    for name in sorted(devices.keys()):
        device = devices[name]
        assert device
        if force_parse_deviceinfo in [None, True]:
            try:
                device.parse_deviceinfo(try_download=download_packages)
            except Exception as ex:
                if not force_parse_deviceinfo:
                    logging.debug(
                        f"Failed to parse deviceinfo for extended description, not a problem: {ex}"
                    )
                else:
                    raise ex

        if json:
            json_output[name] = device.get_summary().toDict()
            if interactive_json:
                continue
        snippet = device.nice_str(colors=use_colors, newlines=True)
        if profile_device and profile_device.name == device.name:
            snippet = color_mark_selected(
                snippet, profile_name or "[unknown]", inherited_from
            )
        output.append(f"{snippet}\n")
    if interactive_json:
        output = ["\n" + json_dump(json_output, indent=4)]
    if output_file:
        with open(output_file, "w") as fd:
            fd.write(json_dump(json_output))
    for line in output:
        print(line)
