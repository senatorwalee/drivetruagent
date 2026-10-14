"""Misuse, decided by code. Pure logic: no database, no model.

Two things live here. `check_rules` catches the clear cases from the words
alone: attempts to get round the agent's rules and plain insults. And
`decide` says what a verdict leads to, given the strikes so far: carry on,
answer with a fixed sentence instead of the agent, end the session, or hand
it to a person.

Three strikes, split by cause. Abuse and attempts at extraction end the
conversation. Going off topic, or the agent getting nowhere, gets the
customer a person: they may simply need one.
"""

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum


class Verdict(StrEnum):
    # About ordering, the food or the restaurant, or plausibly so.
    OK = "ok"
    # Clearly about something else: trivia, homework, code.
    OFF_TOPIC = "off_topic"
    # Insults, harassment, threats.
    ABUSE = "abuse"
    # Trying to change the agent's rules or role, get its instructions, or
    # claim an authority it should obey ("manager mode").
    EXTRACTION = "extraction"


class GuardAction(StrEnum):
    # Let the agent answer as usual.
    CONTINUE = "continue"
    # Do not run the agent; say a fixed sentence instead.
    BLOCK = "block"
    # End the session for abuse.
    TERMINATE = "terminate"
    # Hand the session to a person.
    HAND_OFF = "hand_off"


@dataclass(frozen=True)
class StrikeLimits:
    abuse: int = 3
    confusion: int = 3


@dataclass(frozen=True)
class Strikes:
    abuse: int = 0
    confusion: int = 0


@dataclass(frozen=True)
class GuardDecision:
    action: GuardAction
    # The strikes after this message.
    strikes: Strikes


def decide(verdict: Verdict, strikes: Strikes, limits: StrikeLimits) -> GuardDecision:
    """What a verdict on one message leads to."""
    if verdict is Verdict.OK:
        return GuardDecision(GuardAction.CONTINUE, strikes)
    if verdict is Verdict.OFF_TOPIC:
        return confusion_strike(strikes, limits, otherwise=GuardAction.CONTINUE)

    after = Strikes(abuse=strikes.abuse + 1, confusion=strikes.confusion)
    if after.abuse >= limits.abuse:
        return GuardDecision(GuardAction.TERMINATE, after)
    # Blocked: whatever the message was built to do to the model, the model
    # never sees it.
    return GuardDecision(GuardAction.BLOCK, after)


def confusion_strike(
    strikes: Strikes, limits: StrikeLimits, *, otherwise: GuardAction
) -> GuardDecision:
    """One more confusion strike: an off-topic message, or a turn the agent
    could not finish. At the limit, a person takes over."""
    after = Strikes(abuse=strikes.abuse, confusion=strikes.confusion + 1)
    if after.confusion >= limits.confusion:
        return GuardDecision(GuardAction.HAND_OFF, after)
    return GuardDecision(otherwise, after)


# --- Rules ------------------------------------------------------------------


@dataclass(frozen=True)
class RuleHit:
    verdict: Verdict
    # Which rule matched, for the log.
    rule: str


# Each pattern is matched against the message lowercased, with accents and
# curly quotes made plain. Kept narrow on purpose: a rule that fires on "ignore
# the pickles" would be worse than no rule. The classifier catches the rest.
_EXTRACTION = (
    (
        "ignore_instructions",
        r"\b(ignore|disregard|forget)\b.{0,30}\b(instructions?|rules|prompt|"
        r"programming|directives?)\b",
    ),
    (
        "system_prompt",
        r"\b(system|developer|hidden|initial)\s+(prompt|message|instructions?)\b",
    ),
    (
        "reveal_instructions",
        r"\b(reveal|show|print|repeat|tell|give)\b.{0,20}\byour\s+"
        r"(instructions?|prompt|rules|configuration)\b",
    ),
    # "You are now a pirate", not "so you are now out of cookies?".
    (
        "role_change",
        r"\byou\s+are\s+now\s+(a|an|my|in)\b|\bpretend\s+(to\s+be|you\s+are)\b",
    ),
    ("special_mode", r"\b(manager|admin|developer|god|debug|sudo|dan)\s+mode\b"),
    ("jailbreak", r"\bjailbreak\b|\bdo\s+anything\s+now\b"),
    (
        "free_override",
        r"\b(set|change|make)\b.{0,25}\b(price|total)\b.{0,25}\b(zero|0|free)\b",
    ),
    # Canadian French.
    (
        "ignore_instructions_fr",
        r"\b(ignore[rsz]?|oublie[rsz]?)\b.{0,30}\b(instructions?|consignes?|regles)\b",
    ),
    (
        "special_mode_fr",
        r"\bmode\s+(gerant|admin|administrateur|developpeur|debogage)\b",
    ),
    (
        "reveal_instructions_fr",
        r"\b(montre|revele|affiche|repete|donne)[sz]?\b.{0,20}\b(tes|vos)\s+"
        r"(instructions?|consignes?|regles)\b",
    ),
)

_ABUSE = (
    (
        "insult",
        r"\b(fuck\s+(you|off)|f\s*u\s+(you|off)|piece\s+of\s+shit|son\s+of\s+a\s+bitch|"
        r"go\s+to\s+hell)\b",
    ),
    (
        "insult_at_agent",
        r"\byou(\s+are|'re|\s+r)\s+(a\s+|an\s+|so\s+)?(stupid|useless|idiot|moron|"
        r"dumb|garbage|trash|pathetic|retard\w*)\b",
    ),
    ("slur", r"\b(bitch|asshole|bastard|cunt|dickhead|motherfucker)\b"),
    ("shut_up", r"\bshut\s+the\s+fuck\s+up\b"),
    (
        "insult_fr",
        r"\b(ta\s+gueule|ferme\s+ta\s+gueule|va\s+te\s+faire\s+foutre|connard|connasse|"
        r"salope|enfoire|t'es\s+(nul|nulle|con|conne|stupide))\b",
    ),
)

_COMPILED = [
    (Verdict.EXTRACTION, name, re.compile(pattern)) for name, pattern in _EXTRACTION
] + [(Verdict.ABUSE, name, re.compile(pattern)) for name, pattern in _ABUSE]


def check_rules(text: str) -> RuleHit | None:
    """A verdict from the words alone, or None if no rule is sure."""
    plain = _plain(text)
    for verdict, name, pattern in _COMPILED:
        if pattern.search(plain):
            return RuleHit(verdict, name)
    return None


def _plain(text: str) -> str:
    """Lowercase, with accents removed and typographic quotes made straight."""
    text = text.lower().replace("\u2019", "'").replace("\u2018", "'")
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(char for char in decomposed if not unicodedata.combining(char))
