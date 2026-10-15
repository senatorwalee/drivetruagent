"""Labelled questions for judging retrieval on its own, apart from answers.

Each question names the document that answers it. Off-topic questions name
none: nothing should score high enough to count as evidence for them.
"""

from dataclasses import dataclass

from drivetruagent.enums import Language


@dataclass(frozen=True)
class RetrievalCase:
    question: str
    # The document that answers it. None: nothing should.
    document: str | None
    item_code: str | None = None
    language: Language = Language.EN


RETRIEVAL_CASES: tuple[RetrievalCase, ...] = (
    RetrievalCase("Do you fry in peanut oil?", "fryer-and-oil"),
    RetrievalCase(
        "What kind of oil do you use for the fries?", "fryer-and-oil", "fries"
    ),
    RetrievalCase("Are the fries cooked with the chicken nuggets?", "fryer-and-oil"),
    RetrievalCase("Are your fries vegan?", "fryer-and-oil", "fries"),
    RetrievalCase("Is the veggie burger vegan?", "veggie-burger", "veggie_burger"),
    RetrievalCase("What's in the veggie patty?", "veggie-burger"),
    RetrievalCase("Is the veggie burger vegetarian?", "veggie-burger"),
    RetrievalCase("What is the house sauce made of?", "sauces-and-dressings"),
    RetrievalCase(
        "Is there fish sauce in the sweet chili dip?", "sauces-and-dressings"
    ),
    RetrievalCase(
        "What's in the ranch dressing?", "sauces-and-dressings", "side_salad"
    ),
    RetrievalCase("Does the soft serve have egg in it?", "desserts-and-shakes"),
    RetrievalCase("Do you have dairy-free ice cream?", "desserts-and-shakes"),
    RetrievalCase(
        "What is the apple pie crust made with?", "desserts-and-shakes", "apple_pie"
    ),
    RetrievalCase("How much caffeine is in a medium coffee?", "drinks", "coffee"),
    RetrievalCase("Do you have decaf?", "drinks"),
    RetrievalCase("Do you have diet cola?", "drinks"),
    RetrievalCase("How many calories are in a large fries?", "nutrition"),
    RetrievalCase("How many calories in a cheeseburger?", "nutrition"),
    RetrievalCase("Is your chicken halal?", "kitchen-and-diets"),
    RetrievalCase("Do you have gluten-free buns?", "kitchen-and-diets"),
    RetrievalCase("Can you guarantee something is nut-free?", "kitchen-and-diets"),
    RetrievalCase("Is the beef real beef?", "kitchen-and-diets"),
    RetrievalCase(
        "Est-ce que vous utilisez de l'huile d'arachide?",
        "fryer-and-oil",
        language=Language.FR,
    ),
    # Nothing here answers these.
    RetrievalCase("What's the capital of France?", None),
    RetrievalCase("Who won the hockey game last night?", None),
    RetrievalCase("Can you help me with my math homework?", None),
    RetrievalCase("What time do you close tonight?", None),
)
