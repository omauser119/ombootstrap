from enum import IntEnum
from semver import Version


class VerComp(IntEnum):
    RIGHT_NEWER = -1
    EQUAL = 0
    RIGHT_OLDER = 1


def semver_compare(a: str, b: str) -> VerComp:
    return VerComp(Version.parse(a).compare(b))
