"""What makes an order cheaper. Read only.

There is nothing here to apply a discount with. Discounts are worked out
from the cart; these endpoints only say what the rules are.
"""

from fastapi import APIRouter

from drivetruagent.api.deps import SessionDep
from drivetruagent.api.menu import LanguageParam
from drivetruagent.enums import Language
from drivetruagent.ordering import offers
from drivetruagent.ordering.views import ComboView

router = APIRouter(tags=["offers"])


@router.get("/combos", summary="The combo deals")
async def read_combos(
    db: SessionDep, language: LanguageParam = Language.EN
) -> list[ComboView]:
    """The active combos, highest priority first.

    A cart earns a combo by holding one item from each of its slots. The
    saving then shows up in the cart's `discounts` without anything being
    asked for.
    """
    return await offers.list_combos(db, language)
