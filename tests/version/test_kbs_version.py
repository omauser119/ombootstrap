from pytest import mark
from typing import Optional

from ombootstrap.version.kbs import (
    get_kbs_version,
    compare_kbs_version_generic,
)
from ombootstrap.version.compare import VerComp


def test_get_kbs_version():
    ver = get_kbs_version()
    assert ver
    assert ver.startswith("v")


@mark.parametrize(
    "minimum_ver, kbs_ver, expected",
    [
        ("v0.0.1", "v0.0.1", VerComp.EQUAL),
        ("v0.0.1", "v0.0.2", VerComp.RIGHT_NEWER),
        ("v0.0.1-rc0", "v0.0.1-rc1", VerComp.RIGHT_NEWER),
        ("v0.0.1-rc1", "v0.0.1", VerComp.RIGHT_NEWER),
        ("v0.0.1-rc3", "v0.0.2", VerComp.RIGHT_NEWER),
        ("v0.0.1-rc4", "v0.0.1-rc4-3-g12ab34de", VerComp.RIGHT_NEWER),
        ("v0.0.1-rc4-3-g1234567", "v0.0.1-rc4-3-g12ab34de", VerComp.EQUAL),
        ("v0.0.1", "v0.0.1-rc4-3-g12ab34de", VerComp.RIGHT_OLDER),
        ("v0.0.2", "v0.0.1-rc4-3-g12ab34de", VerComp.RIGHT_OLDER),
        ("v0.0.2", None, None),
        ("v0.0.2", "v0.1.0", VerComp.RIGHT_NEWER),
        ("v0.0.2", "v1.0.0", VerComp.RIGHT_NEWER),
        ("v0.2.2", "v0.1.0", VerComp.RIGHT_OLDER),
        ("v0.2.2", "v1.0.0", VerComp.RIGHT_NEWER),
        (
            "v0.2.0-rc4",
            "v0.2.0-26-gdb61dee",
            VerComp.RIGHT_NEWER,
        ),  # clean rc should be older than non-rc with git commit
    ],
)
def test_kbs_version_compare(
    minimum_ver: str, kbs_ver: str, expected: Optional[VerComp]
):
    assert (
        compare_kbs_version_generic(
            kbs_version=kbs_ver, minimum_ver=minimum_ver
        )
        == expected
    )
