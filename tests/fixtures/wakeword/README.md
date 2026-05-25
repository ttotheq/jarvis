# Wake-word audio fixtures (Phase 2 goal G2.1)

The live true-accept test (`tests/test_wakeword.py::test_labeled_fixtures_meet_targets`)
runs the real openWakeWord `hey_jarvis` model over labeled recordings and asserts
true-accept ≥ 95% over ≥ 20 utterances and false-accept ≤ 1 per 30 min. It is
**skipped** until `manifest.json` exists here, so CI (no microphone, no model)
stays green; recording the clips is the live verification step.

## Recording the set

1. Install the voice extra and a microphone: `uv sync --extra voice`.
2. Record mono 16 kHz PCM16 WAVs into this directory:
   - **≥ 20 positives** — say "hey jarvis" once per clip, varied distance/tone.
   - **negatives / ambient** — non-wake speech and room noise (any wake fired on
     these is a false accept; their total duration is the false-accept denominator).
3. Write `manifest.json`:

   ```json
   {
     "threshold": 0.5,
     "positives": ["pos01.wav", "pos02.wav", "..."],
     "ambient": ["amb01.wav", "amb02.wav", "..."]
   }
   ```

4. `make test` — the skipped test now runs and gates on the G2.1 targets.

For the longer-running ambient false-accept measurement, prefer the live soak:
`python scripts/soak_wakeword.py --minutes 30`. Both numbers go in the Phase 2 doc
Outcomes table.

The WAVs themselves are intentionally not committed (binary, environment-specific).
