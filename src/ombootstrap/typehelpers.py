from typing import Union

try:
    from typing import TypeAlias  # type: ignore[attr-defined]
except ImportError:
    from typing_extensions import TypeAlias

TypeAlias = TypeAlias

try:
    from types import UnionType
except ImportError:
    UnionType: TypeAlias = Union  # type: ignore[no-redef]

try:
    from types import NoneType
except ImportError:
    NoneType: TypeAlias = type(None)  # type: ignore[no-redef]
