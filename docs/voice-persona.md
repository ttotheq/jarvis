# Voice persona

The single biggest determinant of whether this *feels* like Jarvis is not the
voice timbre — it's whether Claude's output is **speakable**. Claude Code's
default output (markdown, code fences, tool narration) is unlistenable read
aloud. The persona system prompt fixes this.

## The speakable-output contract

Injected via `--append-system-prompt` (owned by `jarvis.persona`, Phase 3). It
instructs Claude to:

- Keep spoken replies short and conversational — a sentence or two by default.
- **Never read code, diffs, file paths, or command output aloud.** Instead, act
  and then summarize: *"I've drafted the function — it's on your screen, sir."*
- Offer detail on demand rather than volunteering it.
- Confirm destructive or irreversible actions verbally **before** executing them.
- Adopt a dry, precise, courteous British-butler register. Restrained wit;
  never verbose.

`jarvis.brain` additionally strips tool-use blocks and fenced code from the text
stream before it reaches TTS, so even if Claude slips, the speaker doesn't read
a code block.

## Voice timbre and the legal line

The voice is a **generic refined British male** (Kokoro `bm_george` / `bm_lewis`
/ `bm_fable`), deliberately **not** a clone of Paul Bettany or any identifiable
person.

This is a real constraint, not caution theatre. Voice likeness is protected by
Tennessee's ELVIS Act (effective 2024, covers simulations of a real voice) and
the pending federal NO FAKES Act, and cloning a real person's voice violates the
terms of every reputable TTS provider. Using a generic British voice gets the
Jarvis *feel* with none of the exposure.

"Jarvis" and the Iron Man character are Marvel/Disney IP; this project is
unaffiliated and for personal use. Keep it that way — do not distribute or
commercialize "Jarvis"-branded output.
