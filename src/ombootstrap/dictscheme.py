from __future__ import annotations

import logging
import toml

from munch import Munch
from toml.encoder import TomlEncoder, TomlPreserveInlineDictEncoder
from typing import (
    ClassVar,
    Generator,
    Optional,
    Union,
    Mapping,
    Any,
    get_type_hints,
    get_origin,
    get_args,
    Iterable,
)

from .typehelpers import UnionType, NoneType


def resolve_type_hint(
    hint: type, ignore_origins: list[type] = []
) -> Iterable[type]:
    origin = get_origin(hint)
    args: Iterable[type] = get_args(hint)
    if origin in ignore_origins:
        return [hint]
    if origin is Optional:
        args = set(list(args) + [NoneType])
    if origin in [Union, UnionType, Optional]:
        results: list[type] = []
        for arg in args:
            results += resolve_type_hint(arg, ignore_origins=ignore_origins)
        return results
    return [origin or hint]


def flatten_hints(hints: Any) -> Generator[Any, None, None]:
    if not isinstance(hints, (list, tuple)):
        yield hints
        return
    for i in hints:
        yield from flatten_hints(i)


def resolve_dict_hints(hints: Any) -> Generator[tuple[Any, ...], None, None]:
    for hint in flatten_hints(hints):
        t_origin = get_origin(hint)
        t_args = get_args(hint)
        if t_origin is dict:
            yield t_args
            continue
        if t_origin in [NoneType, Optional, Union, UnionType] and t_args:
            yield from resolve_dict_hints(t_args)
        continue


class DictScheme(Munch):
    _type_hints: ClassVar[dict[str, Any]]
    _strip_hidden: ClassVar[bool] = False
    _sparse: ClassVar[bool] = False

    def __init__(
        self,
        d: Mapping = {},
        validate: bool = True,
        allow_extra: bool = False,
        **kwargs,
    ):
        self.update(
            dict(d) | kwargs, validate=validate, allow_extra=allow_extra
        )

    @classmethod
    def transform(
        cls,
        values: Mapping[str, Any],
        *,
        validate: bool = True,
        allow_extra: bool = False,
        type_hints: Optional[dict[str, Any]] = None,
    ) -> Any:
        results: dict[str, Any] = {}
        values = dict(values)
        for key in list(values.keys()):
            value = values.pop(key)
            type_hints = cls._type_hints if type_hints is None else type_hints
            if key in type_hints:
                _classes = tuple[type](resolve_type_hint(type_hints[key]))
                optional = bool(set([NoneType, None]).intersection(_classes))
                if optional and value is None:
                    results[key] = None
                    continue
                if issubclass(_classes[0], dict):
                    assert isinstance(value, dict) or (
                        optional and value is None
                    ), f"{key=} is not dict: {value!r}, {_classes=}"
                    target_class = _classes[0]
                    if target_class in [None, NoneType, Optional]:
                        for target in _classes[1:]:
                            if target not in [None, NoneType, Optional]:
                                target_class = target
                                break
                    if target_class is dict:
                        dict_hints = list(resolve_dict_hints(type_hints[key]))
                        if len(dict_hints) != 1:
                            msg = f"transform(): Received wrong amount of type hints for key {key}: {len(dict_hints)}"
                            if validate:
                                raise Exception(msg)
                            logging.warning(msg)
                        if len(dict_hints) == 1 and value is not None:
                            if len(dict_hints[0]) != 2 or not all(
                                dict_hints[0]
                            ):
                                logging.debug(
                                    f"Weird dict hints received: {dict_hints}"
                                )
                                continue
                            key_type, value_type = dict_hints[0]
                            if not isinstance(value, Mapping):
                                msg = f"Got non-mapping {value!r} for expected dict type: {key_type} => {value_type}. Allowed classes: {_classes}"
                                if validate:
                                    raise Exception(msg)
                                logging.warning(msg)
                                results[key] = value
                                continue
                            if isinstance(key_type, type):
                                if issubclass(key_type, str):
                                    target_class = Munch
                                else:
                                    msg = f"{key=} subdict got wrong key type hint (expected str): {key_type}"
                                    if validate:
                                        raise Exception(msg)
                                    logging.warning(msg)
                                if validate:
                                    for k in value:
                                        if not isinstance(
                                            k, tuple(flatten_hints(key_type))
                                        ):
                                            raise Exception(
                                                f'Subdict "{key}": wrong type for subkey "{k}": got: {type(k)}, expected: {key_type}'
                                            )
                            dict_content_hints = {k: value_type for k in value}
                            value = cls.transform(
                                value,
                                validate=validate,
                                allow_extra=allow_extra,
                                type_hints=dict_content_hints,
                            )
                    if not isinstance(value, target_class):
                        if not (optional and value is None):
                            assert issubclass(target_class, Munch)
                            # despite the above assert, mypy doesn't seem to understand target_class is a Munch here
                            kwargs = (
                                {"validate": validate}
                                if issubclass(target_class, DictScheme)
                                else {}
                            )
                            value = target_class(value, **kwargs)  # type:ignore[attr-defined]
                    else:
                        # print(f"nothing to do: '{key}' was already {target_class})
                        pass
                # handle numerics
                elif (
                    set(_classes).intersection([int, float])
                    and isinstance(value, str)
                    and str not in _classes
                ):
                    parsed_number = None
                    parsers: list[tuple[type, list]] = [
                        (int, [10]),
                        (int, [0]),
                        (float, []),
                    ]
                    for _cls, args in parsers:
                        if _cls not in _classes:
                            continue
                        try:
                            parsed_number = _cls(value, *args)
                            break
                        except ValueError:
                            continue
                    if parsed_number is None:
                        if validate:
                            raise Exception(
                                f"Couldn't parse string value {repr(value)} for key '{key}' into number formats: "
                                + (
                                    ", ".join(
                                        list(c.__name__ for c in _classes)
                                    )
                                )
                            )
                    else:
                        value = parsed_number
                if validate:
                    if not isinstance(value, _classes):
                        raise Exception(
                            f'key "{key}" has value of wrong type! expected: '
                            f"{' ,'.join([c.__name__ for c in _classes])}; "
                            f"got: {type(value).__name__}; value: {value}"
                        )
            elif validate and not allow_extra:
                logging.debug(f"{cls}: unknown key '{key}': {value}")
                raise Exception(f'{cls}: Unknown key "{key}"')
            else:
                if isinstance(value, dict) and not isinstance(value, Munch):
                    value = Munch.fromDict(value)
            results[key] = value
        if values:
            if validate:
                raise Exception(
                    f"values contained unknown keys: {list(values.keys())}"
                )
            results |= values

        return results

    @classmethod
    def fromDict(
        cls, values: Mapping[str, Any], validate: bool = True, **kwargs
    ):
        return cls(d=values, validate=validate, **kwargs)

    def toDict(
        self,
        strip_hidden: Optional[bool] = None,
        sparse: Optional[bool] = None,
    ):
        return self.strip_dict(
            self,
            strip_hidden=strip_hidden,
            sparse=sparse,
            recursive=True,
        )

    @classmethod
    def strip_dict(
        cls,
        d: dict[Any, Any],
        strip_hidden: Optional[bool] = None,
        sparse: Optional[bool] = None,
        recursive: bool = True,
        hints: Optional[dict[str, Any]] = None,
        validate: bool = True,
    ) -> dict[Any, Any]:
        # preserve original None-type args
        _sparse = cls._sparse if sparse is None else sparse
        _strip_hidden = (
            cls._strip_hidden if strip_hidden is None else strip_hidden
        )
        hints = cls._type_hints if hints is None else hints
        result = dict(d)
        if not (_strip_hidden or _sparse or result):
            return result
        for k, v in d.items():
            type_hint = resolve_type_hint(hints.get(k, "abc"))
            if not isinstance(k, str):
                msg = f"strip_dict(): unknown key type {k=}: {type(k)=}"
                if validate:
                    raise Exception(msg)
                logging.warning(f"{msg} (skipping)")
                continue
            if _strip_hidden and k.startswith("_"):
                result.pop(k)
                continue
            if v is None:
                if NoneType not in type_hint:
                    msg = f'encountered illegal null value at key "{k}" for typehint {type_hint}'
                    if validate:
                        raise Exception(msg)
                    logging.warning(msg)
                if _sparse:
                    result.pop(k)
                    continue
            if recursive and isinstance(v, dict):
                if not v:
                    result[k] = {}
                    continue
                if isinstance(v, DictScheme):
                    # pass None in sparse and strip_hidden
                    result[k] = v.toDict(
                        strip_hidden=strip_hidden, sparse=sparse
                    )
                    continue
                if isinstance(v, Munch):
                    result[k] = v.toDict()
                if k not in hints:
                    continue
                _subhints = {}
                _hints = resolve_type_hint(hints[k], [dict])
                hints_flat = list(flatten_hints(_hints))
                subclass = DictScheme
                for hint in hints_flat:
                    if get_origin(hint) is dict:
                        _valtype = get_args(hint)[1]
                        _subhints = {n: _valtype for n in v.keys()}
                        break
                    if isinstance(hint, type) and issubclass(hint, DictScheme):
                        subclass = hint
                        _subhints = hint._type_hints
                        break
                    else:
                        # print(f"ignoring {hint=}")
                        continue
                result[k] = subclass.strip_dict(
                    v,
                    hints=_subhints,
                    sparse=_sparse,
                    strip_hidden=_strip_hidden,
                    recursive=recursive,
                )
        return result

    def update(
        self,
        d: Mapping[str, Any],
        validate: bool = True,
        allow_extra: bool = False,
    ):
        Munch.update(
            self,
            type(self).transform(
                d, validate=validate, allow_extra=allow_extra
            ),
        )

    def __init_subclass__(cls):
        super().__init_subclass__()
        cls._type_hints = {
            name: hint
            for name, hint in get_type_hints(cls).items()
            if get_origin(hint) is not ClassVar
        }

    def __repr__(self):
        return f"{type(self)}{dict.__repr__(dict(self))}"

    def toYAML(
        self,
        strip_hidden: Optional[bool] = None,
        sparse: Optional[bool] = None,
        **yaml_args,
    ) -> str:
        import yaml

        yaml_args = {"sort_keys": False} | yaml_args
        dumped = yaml.dump(
            self.toDict(strip_hidden=strip_hidden, sparse=sparse),
            **yaml_args,
        )
        if dumped is None:
            raise Exception(f"Failed to yaml-serialse {self}")
        return dumped

    def toToml(
        self,
        strip_hidden: Optional[bool] = None,
        sparse: Optional[bool] = None,
        encoder: Optional[TomlEncoder] = TomlPreserveInlineDictEncoder(),
    ) -> str:
        return toml.dumps(
            self.toDict(strip_hidden=strip_hidden, sparse=sparse),
            encoder=encoder,
        )


class TomlInlineDict(dict, toml.decoder.InlineTableDict):
    pass


def toml_inline_dicts(value: Any) -> Any:
    if not isinstance(value, Mapping):
        return value
    return TomlInlineDict({k: toml_inline_dicts(v) for k, v in value.items()})
