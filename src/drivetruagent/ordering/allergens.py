"""What an item, as configured, contains or may contain.

Pure logic: plain values in, plain values out, no database. The allergen
matrix declares allergens for an item and for options that add them (cheese
adds milk). Given the options chosen, this works out what is present, why,
and which unchosen options would add more.

Only what is declared counts. An allergen nobody declared is "not listed",
which is all the matrix can say: it is never turned into "free of" or "safe".
"""

import uuid
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass

from drivetruagent.enums import Allergen, AllergenLevel

# Stronger first: if one source contains an allergen and another only may,
# it is contained.
_STRENGTH = {AllergenLevel.CONTAINS: 2, AllergenLevel.MAY_CONTAIN: 1}
_ORDER = {allergen: position for position, allergen in enumerate(Allergen)}


@dataclass(frozen=True)
class Declaration:
    allergen: Allergen
    level: AllergenLevel
    # None: the item itself, whatever is chosen. Otherwise the option's id.
    option_id: uuid.UUID | None = None
    # Why it may be present, per language. Only for MAY_CONTAIN.
    note: Mapping[str, str] | None = None


@dataclass(frozen=True)
class Found:
    """One allergen present in the item as configured."""

    allergen: Allergen
    level: AllergenLevel
    # Where it comes from at that level: None for the item itself, else the
    # option's id. In the order declared.
    sources: tuple[uuid.UUID | None, ...]
    # Why it may be present, for MAY_CONTAIN.
    notes: tuple[Mapping[str, str], ...] = ()


@dataclass(frozen=True)
class OptionAdds:
    """An option not chosen that would add an allergen, or make a "may
    contain" a "contains"."""

    option_id: uuid.UUID
    adds: tuple[Declaration, ...]


@dataclass(frozen=True)
class Assessment:
    found: tuple[Found, ...]
    # Asked about, and declared neither way.
    not_listed: tuple[Allergen, ...]
    options_that_add: tuple[OptionAdds, ...]

    @property
    def contains(self) -> tuple[Found, ...]:
        return tuple(f for f in self.found if f.level is AllergenLevel.CONTAINS)

    @property
    def may_contain(self) -> tuple[Found, ...]:
        return tuple(f for f in self.found if f.level is AllergenLevel.MAY_CONTAIN)


def assess(
    declarations: Iterable[Declaration],
    chosen: Collection[uuid.UUID],
    asked: Collection[Allergen] | None = None,
) -> Assessment:
    """The item with these options chosen. `asked`: only these allergens;
    None for everything declared."""
    wanted = set(Allergen) if asked is None else set(asked)
    relevant = [d for d in declarations if d.allergen in wanted]
    applies = [d for d in relevant if d.option_id is None or d.option_id in chosen]
    found = _combine(applies)
    present = {f.allergen: f.level for f in found}

    not_listed = (
        ()
        if asked is None
        else tuple(sorted(wanted - present.keys(), key=_ORDER.__getitem__))
    )
    return Assessment(found, not_listed, _options_that_add(relevant, chosen, present))


def _combine(declarations: list[Declaration]) -> tuple[Found, ...]:
    by_allergen: dict[Allergen, list[Declaration]] = {}
    for declaration in declarations:
        by_allergen.setdefault(declaration.allergen, []).append(declaration)

    found: list[Found] = []
    for allergen in sorted(by_allergen, key=_ORDER.__getitem__):
        group = by_allergen[allergen]
        level = max((d.level for d in group), key=_STRENGTH.__getitem__)
        strongest = [d for d in group if d.level is level]
        found.append(
            Found(
                allergen,
                level,
                tuple(d.option_id for d in strongest),
                tuple(d.note for d in strongest if d.note is not None),
            )
        )
    return tuple(found)


def _options_that_add(
    declarations: list[Declaration],
    chosen: Collection[uuid.UUID],
    present: Mapping[Allergen, AllergenLevel],
) -> tuple[OptionAdds, ...]:
    adds: dict[uuid.UUID, list[Declaration]] = {}
    for declaration in declarations:
        option_id = declaration.option_id
        if option_id is None or option_id in chosen:
            continue
        now = present.get(declaration.allergen)
        if now is None or _STRENGTH[declaration.level] > _STRENGTH[now]:
            adds.setdefault(option_id, []).append(declaration)
    return tuple(
        OptionAdds(option_id, tuple(found)) for option_id, found in adds.items()
    )
