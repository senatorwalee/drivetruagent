"""Which options a cart line ends up with.

Pure logic: plain values in, plain values out, no database. Given an item's
option groups and what the customer asked for, work out the final set of
options, filling in defaults and refusing anything the menu does not allow.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from drivetruagent.ordering.errors import (
    MissingRequiredOptions,
    OptionUnavailable,
    TooFewOptions,
    TooManyOptions,
    UnknownOption,
    UnknownOptionGroup,
)

# What the customer asked for: group code -> option codes, e.g.
# {"size": ["large"], "dip": ["ketchup", "barbecue"]}.
type Selections = Mapping[str, Sequence[str]]


@dataclass(frozen=True)
class OptionSpec:
    id: uuid.UUID
    code: str
    name: Mapping[str, str]
    price_delta_cents: int
    is_default: bool
    is_active: bool


@dataclass(frozen=True)
class GroupSpec:
    code: str
    name: Mapping[str, str]
    min_select: int
    max_select: int
    options: tuple[OptionSpec, ...]

    @property
    def available_options(self) -> tuple[OptionSpec, ...]:
        return tuple(option for option in self.options if option.is_active)

    @property
    def default_option(self) -> OptionSpec | None:
        return next((o for o in self.available_options if o.is_default), None)

    def option(self, code: str) -> OptionSpec | None:
        return next((o for o in self.options if o.code == code), None)


@dataclass(frozen=True)
class ItemSpec:
    id: uuid.UUID
    code: str
    name: Mapping[str, str]
    # Which part of the menu it belongs to. Combos are defined by category.
    category_code: str
    base_price_cents: int
    is_active: bool
    groups: tuple[GroupSpec, ...]

    def group(self, code: str) -> GroupSpec | None:
        return next((g for g in self.groups if g.code == code), None)


@dataclass(frozen=True)
class ChosenOption:
    """One resolved choice, with the group it belongs to."""

    group: GroupSpec
    option: OptionSpec


def resolve_selection(item: ItemSpec, requested: Selections) -> list[ChosenOption]:
    """Turn what was asked for into the full set of options for a line.

    For each of the item's groups, in menu order:
      - use what was asked for, if anything;
      - otherwise use the group's default, if it has one;
      - otherwise, if the group requires a choice, report it as missing.

    Raises an OrderingError if the request names something the menu does not
    have, or picks too many or too few. Missing required groups are collected
    and reported together, so the customer is asked once.
    """
    valid_groups = [group.code for group in item.groups]
    for group_code in requested:
        if item.group(group_code) is None:
            raise UnknownOptionGroup(item.code, group_code, valid_groups)

    chosen: list[ChosenOption] = []
    missing: list[GroupSpec] = []

    for group in item.groups:
        # dict.fromkeys drops repeats and keeps the order they were given in.
        codes = list(dict.fromkeys(requested.get(group.code, ())))
        options = [_lookup_option(group, code) for code in codes]

        if not options and group.min_select >= 1:
            default = group.default_option
            if default is None:
                missing.append(group)
                continue
            options = [default]

        if len(options) > group.max_select:
            raise TooManyOptions(group.code, group.max_select, codes)
        if len(options) < group.min_select:
            raise TooFewOptions(group.code, group.min_select, codes)

        chosen.extend(ChosenOption(group, option) for option in options)

    if missing:
        raise MissingRequiredOptions(item.code, [describe_group(g) for g in missing])
    return chosen


def merge_selections(current: Selections, changes: Selections) -> dict[str, list[str]]:
    """Apply a change to a line's existing choices, group by group.

    A group named in `changes` is replaced outright; an empty list clears it.
    A group not named keeps its current choice. So "make the coffee large"
    changes the size and leaves the milk alone.
    """
    merged = {group: list(codes) for group, codes in current.items()}
    for group, codes in changes.items():
        merged[group] = list(codes)
    return merged


def describe_group(group: GroupSpec) -> dict[str, object]:
    """A group and its available options, in the shape error details use."""
    return {
        "group_code": group.code,
        "group_name": dict(group.name),
        "min_select": group.min_select,
        "max_select": group.max_select,
        "options": [
            {
                "option_code": option.code,
                "option_name": dict(option.name),
                "price_delta_cents": option.price_delta_cents,
            }
            for option in group.available_options
        ],
    }


def _lookup_option(group: GroupSpec, code: str) -> OptionSpec:
    valid = [option.code for option in group.available_options]
    option = group.option(code)
    if option is None:
        raise UnknownOption(group.code, code, valid)
    if not option.is_active:
        raise OptionUnavailable(group.code, code, valid)
    return option
