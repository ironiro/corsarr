"""The bot's characters: which ones speak a message, and making sure each line carries its emoji.

Every character can be switched on and off on its own (setting `<id>_enabled`, in the chat or the web
interface). Per message one active character speaks, or now and then two of them have a short exchange.
Their descriptions live in i18n (prompt.char_<id>); all of them are part of the cached system prompt.
"""
from __future__ import annotations

import random
import re

from .i18n import t

# id -> emoji every line of that character starts with. Order = order in the web interface.
CHARACTERS: dict[str, str] = {
    "pirate": "🏴‍☠️",
    "genz": "📱",
    "butler": "🎩",
    "critic": "🧐",
    "clerk": "📼",
    "noir": "🕵️",
    "trailer": "🎙️",
    "computer": "🤖",
    "grandma": "👵",
    "reporter": "⚽",
    "cat": "🐈",
    "bard": "🧙",
}
DEFAULT_ON = ("pirate", "genz")  # the original two; the others start switched off
DIALOG_SHARE = 1 / 3             # with several active characters: how often two of them talk together

PIRATE, GENZ = CHARACTERS["pirate"], CHARACTERS["genz"]
NAME_TAG = re.compile(r"^\*{1,2}[^*\n]{1,30}:\*{1,2}\s*")


def setting(cid: str) -> str:
    return f"{cid}_enabled"


def default_settings() -> dict[str, bool]:
    return {setting(cid): cid in DEFAULT_ON for cid in CHARACTERS}


def active(settings: dict) -> list[str]:
    return [cid for cid in CHARACTERS if settings.get(setting(cid), cid in DEFAULT_ON)]


def characters() -> str:
    """All characters with their emoji and description, plus the rules – for the system prompt."""
    lines = [f"- {emoji} {t(f'prompt.char_{cid}')}" for cid, emoji in CHARACTERS.items()]
    return t("prompt.characters", list="\n".join(lines))


def choose_speaker(settings: dict) -> str:
    """One character id, 'dialog:<a>,<b>' or 'normal' – random per message among the active characters."""
    on = active(settings)
    if not on:
        return "normal"
    if len(on) >= 2 and random.random() < DIALOG_SHARE:
        a, b = random.sample(on, 2)
        return f"dialog:{a},{b}"
    return random.choice(on)


def speakers(speaker: str) -> list[str]:
    """Character ids that may speak in a message for `speaker`."""
    if speaker.startswith("dialog:"):
        return [c for c in speaker.removeprefix("dialog:").split(",") if c in CHARACTERS]
    return [speaker] if speaker in CHARACTERS else []


def speaker_instruction(speaker: str) -> str:
    who = speakers(speaker)
    if not who:
        return t("prompt.speaker_normal")
    if len(who) == 1:
        return t("prompt.speaker_one", name=t(f"prompt.name_{who[0]}"), emoji=CHARACTERS[who[0]])
    return t("prompt.speaker_dialog", a=t(f"prompt.name_{who[0]}"), a_emoji=CHARACTERS[who[0]],
             b=t(f"prompt.name_{who[1]}"), b_emoji=CHARACTERS[who[1]])


def strip_emoji(line: str) -> tuple[str | None, str]:
    """(character id or None, line without the character emoji)."""
    for cid, emoji in CHARACTERS.items():
        # Models often drop the invisible variation selector (🕵️ -> 🕵), so accept both spellings.
        for form in (emoji, emoji.replace("️", "")):
            if line.startswith(form):
                return cid, line[len(form):].lstrip("️").strip()
    return None, line.strip()


def enforce(text: str, speaker: str) -> str:
    """Repair lines that lack the speaker's emoji or carry one of a character that isn't speaking."""
    allowed = speakers(speaker)
    out: list[str] = []
    for i, ln in enumerate(ln.strip() for ln in text.strip().splitlines() if ln.strip()):
        cid, rest = strip_emoji(ln)
        # Some models add a name tag ("**👵 Grandma:** …") although the emoji already says who speaks
        rest = NAME_TAG.sub("", rest, count=1)
        cid = cid or strip_emoji(rest)[0]
        rest = strip_emoji(rest)[1]
        if not allowed:
            out.append(rest)
            continue
        if cid not in allowed:
            cid = allowed[i % len(allowed)]  # a dialog without emojis: alternate the two
        out.append(f"{CHARACTERS[cid]} {rest}")
    return "\n".join(out)
