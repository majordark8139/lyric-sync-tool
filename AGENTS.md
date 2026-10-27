# Agent Guidelines & Universal Provider Contract

## Architectural Rule (Permanent Invariant)

### RULE OF PROVIDER PARITY
Any data model guardrail, validation logic, sync-state enforcement, or parser feature (e.g., background vocal separation, whitespace handling, strict candidate matching $\ge 70.0$) applied to one provider MUST be audited and mirrored across ALL active providers (`unison`, `lrc_red`, `kugou`). A task touching provider logic is NOT complete until all providers satisfy the exact same contract and pass identical rejection tests.

---

## Universal Provider Contract

All providers (`core/providers/unison.py`, `core/providers/lrc_red.py`, `core/providers/kugou.py`) implement `BaseLyricsProvider` (`core/providers/base.py`) and MUST adhere to the following rules:

1. **Strict Word-Sync Return Type**:
   - `search_word_sync(title, artist, duration_ms, ...)` MUST return `Optional[SongLyrics]`.
   - If no verified word-level/syllable-level synchronized lyrics exist, return `None`.
   - Never return line-synced lyrics (`is_word_synced == False`), plain lyrics, or fake single-syllable lines.

2. **Universal Post-Validation**:
   - All providers MUST pass their candidate `SongLyrics` through `BaseLyricsProvider.validate_word_sync(lyrics)` prior to returning.
   - `validate_word_sync` guarantees:
     - `lyrics is not None`
     - `lyrics.is_word_synced` is True
     - At least one line has discrete multi-syllable word-level sync (or a valid single-word line with duration > 0). If all lines have 0 or 1 syllable spanning the entire line duration across multi-word lines, the lyrics are rejected as line-sync.

3. **Candidate Matching Guardrail**:
   - Any candidate search result must be scored using `core.matcher:score_candidate`.
   - Rejection threshold: candidates with confidence score $< 70.0$ MUST be rejected (`None`). Providers must never silently accept mismatched songs.

4. **Zero External Fallbacks**:
   - No external fallback services (e.g. no LRCLib fallbacks, no unsynced web scrapes). Each provider queries only its designated service endpoints.

5. **Background Vocal & Ad-lib Parity**:
   - Parenthetical ad-libs (e.g. `(yeah)`, `(Let's go)`) must be extracted into `line.background_syllables` and `line.background_text`, and stripped from lead syllables.
   - In TTML serialization, background vocals are represented as `<span ttm:role="x-bg">...</span>`.

6. **Test Mocking Support**:
   - All providers must use an internal HTTP helper (`_http_get`) that honors unittest mocks (checking `isinstance(requests.get, Mock)`).
