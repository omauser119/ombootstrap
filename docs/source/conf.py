import logging
import os
from sphinx.config import getenv
from ombootstrap.utils import git

# sys.path.insert(0, os.path.abspath('../..'))
extensions = [
    "sphinx_click",
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.linkcode",
    "myst_parser",
]
myst_all_links_external = True
templates_path = ["templates"]
project = "Ombootstrap"
html_title = "Ombootstrap"
html_theme = "furo"
html_static_path = ["static"]
html_css_files = ["kupfer_docs.css"]
html_favicon = "static/kupfer-white-filled.svg"
html_theme_options = {
    "globaltoc_maxdepth": 5,
    "globaltoc_collapse": True,
    "light_logo": "kupfer-black-transparent.svg",
    "dark_logo": "kupfer-white-transparent.svg",
    "light_css_variables": {
        "color-brand-primary": "#882a1a",
        "color-brand-content": "#882a1a",
    },
    "dark_css_variables": {
        "color-brand-primary": "#eba38d",
        "color-brand-content": "#eba38d",
        "color-problematic": "#ff7564",
    },
    "source_repository": "https://gitlab.com/kupfer/ombootstrap",
    "source_directory": "docs/source/",
}


autosummary_generate = True
autodoc_default_options = {
    "members": True,
    "undoc-members": True,
    "show-inheritance": True,
    "inherited-members": True,
}
autodoc_preserve_defaults = True


def get_version():
    try:
        res = git(
            ["rev-parse", "HEAD"],
            dir=os.path.join(os.path.dirname(__file__), "../.."),
            use_git_dir=True,
            capture_output=True,
        )
        res.check_returncode()
        ver = res.stdout.decode().strip()
        logging.info(f"Detected git {ver=}")
        return ver
    except Exception as ex:
        logging.warning("Couldn't get git branch:", exc_info=ex)
        return "HEAD"


version = getenv("version") or get_version()


def linkcode_resolve(domain, info):
    if domain != "py":
        return None
    if not info["module"]:
        return None
    filename = info["module"].replace(".", "/")
    return "%s/-/blob/%s/src/%s.py" % (
        html_theme_options["source_repository"],
        version,
        filename,
    )
