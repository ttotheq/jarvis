# Wake-word audio fixtures (Phase 2 goal G2.1)

The accuracy test (`tests/test_wakeword.py::test_labeled_fixtures_meet_targets`)
runs the real openWakeWord `hey_jarvis` model over labeled clips and asserts
true-accept ≥ 95% over ≥ 20 utterances and false-accept ≤ 1 per 30 min. It is
**skipped** until `manifest.json` + the WAVs exist here, so CI (no microphone, no
voice extra) stays green. The audio is git-ignored and regenerable.

## Quick path — synthesize the set (recommended)

```sh
uv sync --extra voice                       # Kokoro, openWakeWord, scipy, …
python scripts/gen_wakeword_fixtures.py     # writes WAVs + manifest.json, prints metrics
make test                                   # the skipped accuracy test now runs and gates
```

The generator uses the same TTS that voices Jarvis to build 24 "hey jarvis"
positives + 30 min of near-miss-laden ambient, then measures. The numbers (and the
synthetic-vs-live caveat) are in the Phase 2 doc Outcomes.

## Gold-standard path — record real audio

1. Install the voice extra and a microphone: `uv sync --extra voice`.
2. Record mono 16 kHz PCM16 WAVs into this directory:
   - **≥ 20 positives** — say "hey jarvis" once per clip, varied distance/tone.
   - **negatives / ambient** — non-wake speech and room noise (any wake fired on
     these is a false accept; their total duration is the false-accept denominator).
3. Write `manifest.json`:

   ```json
   {
     "threshold": 0.9,
     "positives": ["pos01.wav", "pos02.wav", "..."],
     "ambient": ["amb01.wav", "amb02.wav", "..."]
   }
   ```

4. `make test` — the skipped test now runs and gates on the G2.1 targets.

For the longer-running ambient false-accept measurement, prefer the live soak:
`python scripts/soak_wakeword.py --minutes 30`. Both numbers go in the Phase 2 doc
Outcomes table.

The WAVs themselves are intentionally not committed (binary, environment-specific).
