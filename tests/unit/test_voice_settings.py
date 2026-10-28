"""How a voice call is set up to hear the customer. No network."""

from typing import Any

from drivetruagent.agent.model import ToolDefinition
from drivetruagent.agent.voice import (
    TRANSCRIPTION_PROMPT,
    VoiceSettings,
    session_config,
)
from drivetruagent.config import Settings

TOOLS = [ToolDefinition("get_cart", "Read the cart.", {"type": "object"})]


def heard(settings: VoiceSettings) -> dict[str, Any]:
    config: dict[str, Any] = dict(session_config("Take orders.", TOOLS, settings))
    return config["audio"]["input"]


def test_by_default_it_is_set_up_for_a_speakerphone() -> None:
    audio = heard(VoiceSettings())

    assert audio["noise_reduction"] == {"type": "far_field"}
    assert audio["turn_detection"] == {
        "type": "semantic_vad",
        "eagerness": "low",
        "interrupt_response": True,
    }


def test_the_transcriber_is_told_what_language_to_expect() -> None:
    """Without it, noise came back as words in Chinese and Korean."""
    transcription = heard(VoiceSettings())["transcription"]

    assert transcription == {
        "model": "gpt-4o-mini-transcribe",
        "prompt": TRANSCRIPTION_PROMPT,
    }
    assert "English or Canadian French" in TRANSCRIPTION_PROMPT


def test_loudness_based_detection_ignores_quieter_sound() -> None:
    audio = heard(
        VoiceSettings(turn_detection="server", vad_threshold=0.8, interruptions=False)
    )

    assert audio["turn_detection"] == {
        "type": "server_vad",
        "threshold": 0.8,
        "interrupt_response": False,
    }


def test_noise_reduction_can_be_turned_off() -> None:
    assert "noise_reduction" not in heard(VoiceSettings(noise_reduction=None))


def test_the_room_tuning_comes_from_the_settings() -> None:
    settings = Settings(
        database_url="postgresql+psycopg://unused",
        voice_noise_reduction="off",
        voice_turn_detection="server",
        voice_vad_threshold=0.75,
        voice_interruptions=False,
        _env_file=None,  # pyright: ignore[reportCallIssue]
    )

    voice = settings.voice_settings

    assert voice.noise_reduction is None
    assert (voice.turn_detection, voice.vad_threshold, voice.interruptions) == (
        "server",
        0.75,
        False,
    )
