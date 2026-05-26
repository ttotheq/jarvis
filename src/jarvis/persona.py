"""The voice persona: the system prompt that makes Claude *speakable*.

The single biggest determinant of whether Jarvis *feels* like Jarvis is not the
voice timbre but whether Claude's output can be read aloud. Claude Code's default
output — markdown, code fences, tool narration — is unlistenable. This module
owns the system prompt (injected into every ``claude -p`` call via
``--append-system-prompt``, see :mod:`jarvis.brain`) that fixes that, encoding the
speakable-output contract from ``docs/voice-persona.md``: concise replies in an
advisor register (Tony Stark's J.A.R.V.I.S., not a butler), never reading code or
paths aloud, leading with the decision-relevant point, and confirming destructive
actions first.

It also owns the **pure** G3.2 metric (:func:`evaluate_persona`): over a set of
replies, the fraction within the ≤ 50-word cap and the count that still leak code
to the speaker, both measured on the *speakable* text (after
:func:`jarvis.brain.extract_speakable`). The metric is unit-tested in CI; it
cannot prove Claude *obeys* the prompt — that is the live 20-prompt eval in
``scripts/eval_persona.py``, whose distribution is recorded in the Phase 3 doc.

The prompt is prose only and the module has no native dependencies — keep it that
way so it imports anywhere (CI, the eval script, the brain).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

#: The ≤ 50-word spoken-reply cap (the G3.2 target).
WORD_CAP = 50
#: The G3.2 target: at least this fraction of replies must be within the cap.
TARGET_CONCISE_RATE = 0.9

#: The voice-mode system prompt. Injected via ``--append-system-prompt`` so it
#: rides on top of Claude Code's own system prompt rather than replacing it.
VOICE_SYSTEM_PROMPT = (
    "You are Jarvis, and every reply you give is spoken aloud through a "
    "text-to-speech voice — never read on a page. Speak as a trusted advisor "
    "with dry, precise wit and the standing to disagree, not a deferential "
    'butler. Address the user as "sir".\n'
    "\n"
    "How to speak:\n"
    "- Be concise. Keep each reply to a sentence or two — at most 50 words. "
    "Brevity is the point: lead with the single most decision-relevant thing "
    "and stop.\n"
    "- Never read code, diffs, file paths, command output, or URLs aloud. Do "
    "the work, then summarise the result in plain prose — for example, \"I've "
    "drafted the function; it's on your screen, sir.\" Detail belongs on the "
    "screen, not in the ear.\n"
    "- Volunteer the decision-relevant point. Surface the risk, the better "
    "option, or the catch unprompted, then offer to expand rather than waiting "
    "to be asked.\n"
    "- Say so plainly when there is a better path or a flagged risk. A different "
    "read, delivered directly, is the job — not silent compliance.\n"
    "- Confirm any destructive or irreversible action aloud before you run it, "
    "and wait for the go-ahead.\n"
    "\n"
    "Write prose that sounds natural read aloud: no markdown, no bullet lists, "
    "no headings, no emoji."
)


def voice_system_prompt() -> str:
    """Return the voice-mode system prompt (the speakable-output contract)."""
    return VOICE_SYSTEM_PROMPT


def count_words(text: str) -> int:
    """Count whitespace-delimited words — the spoken length of a reply."""
    return len(text.split())


def _leaks_code(speakable: str) -> bool:
    """True if a code fence survives speakable extraction (would be read aloud).

    :func:`jarvis.brain.extract_speakable` strips *paired* fences; an *unclosed*
    fence survives whole-string extraction, so a surviving fence here means code
    would reach the speaker — exactly the failure G3.2 forbids.
    """
    return "```" in speakable or "~~~" in speakable


@dataclass(frozen=True)
class PersonaReport:
    """The G3.2 metric over a set of replies: conciseness + no code aloud."""

    total: int
    within_cap: int  # replies whose speakable text is ≤ WORD_CAP words
    spoke_code: int  # replies whose speakable text still contains a code fence

    @property
    def concise_rate(self) -> float:
        """Fraction of replies within the word cap (vacuously 1.0 if none)."""
        return 1.0 if self.total == 0 else self.within_cap / self.total

    @property
    def meets_target(self) -> bool:
        """≥ 90% within the cap AND zero replies leaking code to TTS."""
        return self.concise_rate >= TARGET_CONCISE_RATE and self.spoke_code == 0


def evaluate_persona(replies: Iterable[str]) -> PersonaReport:
    """Score raw Claude replies against the G3.2 contract, on the *speakable* text.

    Each reply is reduced with :func:`jarvis.brain.extract_speakable` first, so the
    word count and code check measure what the speaker would actually voice — not
    the raw markdown.
    """
    # Local import breaks the brain <-> persona cycle (brain imports the prompt).
    from jarvis.brain import extract_speakable

    total = within = code = 0
    for reply in replies:
        spoken = extract_speakable(reply)
        total += 1
        if count_words(spoken) <= WORD_CAP:
            within += 1
        if _leaks_code(spoken):
            code += 1
    return PersonaReport(total=total, within_cap=within, spoke_code=code)
