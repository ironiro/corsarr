"""Speaker selection and post-processing for the two characters."""
from __future__ import annotations

import random

from .i18n import t

PIRATE = "🏴‍☠️"
GENZ = "📱"


def characters() -> str:
    return t("prompt.characters", pirate=PIRATE, genz=GENZ)


def choose_speaker(settings: dict) -> str:
    """'pirate' | 'genz' | 'dialog' | 'normal', random per message among the active characters."""
    pirate, genz = settings.get("pirate_enabled", True), settings.get("genz_enabled", True)
    if pirate and genz:
        return random.choice(["pirate", "genz", "dialog"])
    if pirate:
        return "pirate"
    if genz:
        return "genz"
    return "normal"


def speaker_instruction(speaker: str) -> str:
    return t(f"prompt.speaker_{speaker}", pirate=PIRATE, genz=GENZ)


def enforce(text: str, speaker: str) -> str:
    """Repair lines that lack the speaker emoji or carry a disabled one."""
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    out: list[str] = []
    for i, ln in enumerate(lines):
        has_p, has_g = ln.startswith(PIRATE), ln.startswith(GENZ)
        if speaker == "normal":
            ln = ln.removeprefix(PIRATE).removeprefix(GENZ).strip()
        elif speaker == "pirate" and not has_p:
            ln = f"{PIRATE} {ln.removeprefix(GENZ).strip()}"
        elif speaker == "genz" and not has_g:
            ln = f"{GENZ} {ln.removeprefix(PIRATE).strip()}"
        elif speaker == "dialog" and not (has_p or has_g):
            ln = f"{PIRATE if i % 2 == 0 else GENZ} {ln}"
        out.append(ln)
    return "\n".join(out)
