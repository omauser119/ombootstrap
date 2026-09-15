import click
import logging

from typing import Optional, Sequence, Union

from ombootstrap.config.state import config
from ombootstrap.constants import Arch
from ombootstrap.utils import programs_available
from .docker import DockerWrapper
from .wrapper import Wrapper

wrapper_impls: dict[str, Wrapper] = {
    "docker": DockerWrapper(),
}


def get_wrapper_type(wrapper_type: Optional[str] = None) -> str:
    return wrapper_type or config.file.wrapper.type


def get_wrapper_impl(wrapper_type: Optional[str] = None) -> Wrapper:
    return wrapper_impls[get_wrapper_type(wrapper_type)]


def wrap(wrapper_type: Optional[str] = None):
    wrapper_type = get_wrapper_type(wrapper_type)
    if wrapper_type != "none":
        get_wrapper_impl(wrapper_type).wrap()


def is_wrapped(wrapper_type: Optional[str] = None) -> bool:
    wrapper_type = get_wrapper_type(wrapper_type)
    return (
        wrapper_type != "none" and get_wrapper_impl(wrapper_type).is_wrapped()
    )


def needs_wrap(wrapper_type: Optional[str] = None) -> bool:
    wrapper_type = wrapper_type or get_wrapper_type()
    return (
        wrapper_type != "none"
        and not is_wrapped(wrapper_type)
        and not config.runtime.no_wrap
    )


def enforce_wrap(no_wrapper=False):
    wrapper_type = get_wrapper_type()
    if needs_wrap(wrapper_type) and not no_wrapper:
        logging.info(f"Wrapping in {wrapper_type}")
        wrap()


def check_programs_wrap(programs: Union[str, Sequence[str]]):
    if not programs_available(programs):
        logging.debug(
            f"Wrapping because one of {[programs] if isinstance(programs, str) else programs} isn't available."
        )
        enforce_wrap()


def wrap_if_foreign_arch(arch: Arch):
    if arch != config.runtime.arch:
        enforce_wrap()


def execute_without_exit(
    f, argv_override: Optional[list[str]], *args, **kwargs
):
    """
    If no wrap is needed, executes and returns `f(*args, **kwargs)`.
    If a wrap is determined to be necessary, force a wrap with argv_override applied.
    If a wrap was forced, None is returned.
    WARNING: No protection against f() returning None is taken.
    """
    if not needs_wrap():
        return f(*args, **kwargs)
    assert get_wrapper_type() != "none", (
        "needs_wrap() should've returned False"
    )
    w = get_wrapper_impl()
    w_cmd = w.argv_override
    # we need to avoid throwing and catching SystemExit due to FDs getting closed otherwise
    w_should_exit = w.should_exit
    w.argv_override = argv_override
    w.should_exit = False
    w.wrap()
    w.argv_override = w_cmd
    w.should_exit = w_should_exit
    return None


nowrapper_option = click.option(
    "-w/-W",
    "--force-wrapper/--no-wrapper",
    "wrapper_override",
    is_flag=True,
    default=None,
    help="Force or disable the docker wrapper. Defaults to autodetection.",
)
