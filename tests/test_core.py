"""Unit tests for core KRC decryption, reconciliation, and export routines."""

import base64
import re
import unittest
import zlib

from core.models import LyricLine, SongLyrics, SyllableSpan
from core.providers.kugou import _KRC_XOR_KEY, _WORD_SYNC_RE, unpack_krc
from core.sync_engine import (
    reconcile_line,
    to_enhanced_lrc,
    to_plain_text,
    to_ttml,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_krc_payload(krc_text: str) -> str:
    """Synthesise a valid Base64-encoded KRC payload from *krc_text*.

    Pipeline (inverse of :func:`~core.providers.kugou.unpack_krc`):
      1. Encode *krc_text* as UTF-8.
      2. ``zlib.compress`` the bytes.
      3. XOR-mask the compressed stream with :data:`~core.providers.kugou._KRC_XOR_KEY`.
      4. Prepend the ``b"krc1"`` magic header.
      5. Base64-encode and return as an ASCII string.
    """
    compressed: bytes = zlib.compress(krc_text.encode("utf-8"))
    key_len = len(_KRC_XOR_KEY)
    masked = bytearray(len(compressed))
    for i, byte in enumerate(compressed):
        masked[i] = byte ^ _KRC_XOR_KEY[i & (key_len - 1)]
    raw = b"krc1" + bytes(masked)
    return base64.b64encode(raw).decode("ascii")


def _simple_song() -> SongLyrics:
    """Build a two-line :class:`~core.models.SongLyrics` fixture."""
    return SongLyrics(
        metadata={"ti": "Test Song", "ar": "Artist"},
        lines=[
            LyricLine(
                start_ms=1000,
                end_ms=3000,
                syllables=[
                    SyllableSpan(abs_start_ms=1000, abs_end_ms=1400, text="Hel"),
                    SyllableSpan(abs_start_ms=1400, abs_end_ms=1800, text="lo"),
                    SyllableSpan(abs_start_ms=2000, abs_end_ms=2500, text="World"),
                ],
            ),
            LyricLine(
                start_ms=3500,
                end_ms=5500,
                syllables=[
                    SyllableSpan(abs_start_ms=3500, abs_end_ms=4000, text="Good"),
                    SyllableSpan(abs_start_ms=4000, abs_end_ms=4600, text="bye"),
                ],
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestKrcUnpackAndValidation(unittest.TestCase):
    """Tests for :func:`~core.providers.kugou.unpack_krc` and the
    word-sync validation regex."""

    _WORD_SYNC_LINE = "[12000,2000]<0,350,-1>Hel<350,300,-1>lo <650,600,-1>World"
    _LINE_ONLY_LINE = "[12000,2000]Hello World"

    def test_roundtrip_word_synced_payload(self) -> None:
        """unpack_krc decrypts a synthesised payload back to the original text."""
        krc_text = (
            "[ti:Test]\n"
            "[ar:Artist]\n"
            f"{self._WORD_SYNC_LINE}\n"
        )
        payload = _make_krc_payload(krc_text)
        result = unpack_krc(payload)
        self.assertEqual(result, krc_text)

    def test_bad_magic_raises_value_error(self) -> None:
        """unpack_krc raises ValueError when the 4-byte header is wrong."""
        bad_raw = b"BAD!" + zlib.compress(b"data")
        bad_b64 = base64.b64encode(bad_raw).decode("ascii")
        with self.assertRaises(ValueError):
            unpack_krc(bad_b64)

    def test_word_sync_regex_matches_valid_line(self) -> None:
        """_WORD_SYNC_RE matches a well-formed syllable-timed line."""
        self.assertIsNotNone(_WORD_SYNC_RE.search(self._WORD_SYNC_LINE))

    def test_word_sync_regex_rejects_line_only(self) -> None:
        """_WORD_SYNC_RE does NOT match a plain line-timed line (no <> tags)."""
        self.assertIsNone(_WORD_SYNC_RE.search(self._LINE_ONLY_LINE))

    def test_line_only_payload_would_return_none(self) -> None:
        """A payload with only line-synced lyrics fails the validation gate.

        We verify that _WORD_SYNC_RE finds no match in a line-only KRC text,
        mirroring the behaviour of search_word_sync's validation step.
        """
        krc_text = "[ti:Test]\n[ar:Artist]\n[12000,2000]Hello World\n"
        payload = _make_krc_payload(krc_text)
        decrypted = unpack_krc(payload)
        # Validation gate: no word-sync line → provider returns None
        self.assertIsNone(_WORD_SYNC_RE.search(decrypted))


class TestReconcileTypoPreservesTiming(unittest.TestCase):
    """Rule A: a ≤1-token typo preserves all micro-syllable timestamps."""

    def _build_line(self) -> LyricLine:
        """Three-syllable line: 'Hello World bye'."""
        return LyricLine(
            start_ms=0,
            end_ms=4000,
            syllables=[
                SyllableSpan(abs_start_ms=0,    abs_end_ms=800,  text="Hello"),
                SyllableSpan(abs_start_ms=1000, abs_end_ms=2000, text="World"),
                SyllableSpan(abs_start_ms=2500, abs_end_ms=3200, text="bye"),
            ],
        )

    def test_typo_preserves_timestamps(self) -> None:
        """Fixing one word keeps every syllable's abs_start_ms / abs_end_ms."""
        line = self._build_line()
        # Change "bye" → "bye!" — single-token diff, same word count
        result = reconcile_line(line, "Hello World bye!")

        self.assertEqual(len(result.syllables), 3)
        # First two syllables must be bit-for-bit identical in timing
        for orig, new in zip(line.syllables[:2], result.syllables[:2]):
            self.assertEqual(new.abs_start_ms, orig.abs_start_ms)
            self.assertEqual(new.abs_end_ms,   orig.abs_end_ms)
        # Third syllable timing preserved, only text updated
        self.assertEqual(result.syllables[2].abs_start_ms, 2500)
        self.assertEqual(result.syllables[2].abs_end_ms,   3200)
        self.assertEqual(result.syllables[2].text, "bye!")

    def test_zero_diff_is_rule_a(self) -> None:
        """Identical text (0 diff) still triggers Rule A."""
        line = self._build_line()
        result = reconcile_line(line, "Hello World bye")
        for orig, new in zip(line.syllables, result.syllables):
            self.assertEqual(new.abs_start_ms, orig.abs_start_ms)
            self.assertEqual(new.abs_end_ms,   orig.abs_end_ms)

    def test_line_bounds_unchanged_rule_a(self) -> None:
        """start_ms and end_ms are preserved under Rule A."""
        line = self._build_line()
        result = reconcile_line(line, "Hello World bye!")
        self.assertEqual(result.start_ms, line.start_ms)
        self.assertEqual(result.end_ms,   line.end_ms)


class TestReconcileRewriteLinearInterpolation(unittest.TestCase):
    """Rule B: a major rewrite distributes character-weighted timestamps."""

    def _build_line(self) -> LyricLine:
        """Line spanning [1000, 5000] ms with two syllables."""
        return LyricLine(
            start_ms=1000,
            end_ms=5000,
            syllables=[
                SyllableSpan(abs_start_ms=1000, abs_end_ms=2500, text="Old"),
                SyllableSpan(abs_start_ms=2500, abs_end_ms=5000, text="Lyrics"),
            ],
        )

    def test_rewrite_discards_old_syllables(self) -> None:
        """After a rewrite the syllable count equals the new word count."""
        line = self._build_line()
        result = reconcile_line(line, "Brand new text here")
        self.assertEqual(len(result.syllables), 4)

    def test_first_syllable_starts_at_line_start(self) -> None:
        """The first syllable's abs_start_ms equals original_line.start_ms."""
        line = self._build_line()
        result = reconcile_line(line, "Brand new text here")
        self.assertEqual(result.syllables[0].abs_start_ms, line.start_ms)

    def test_last_syllable_snapped_to_line_end(self) -> None:
        """The last syllable's abs_end_ms is snapped to original_line.end_ms."""
        line = self._build_line()
        result = reconcile_line(line, "Brand new text here")
        self.assertEqual(result.syllables[-1].abs_end_ms, line.end_ms)

    def test_timestamps_are_non_decreasing(self) -> None:
        """Syllable timestamps must be monotonically non-decreasing."""
        line = self._build_line()
        result = reconcile_line(line, "Brand new text here")
        for i in range(len(result.syllables) - 1):
            self.assertLessEqual(
                result.syllables[i].abs_end_ms,
                result.syllables[i + 1].abs_start_ms,
            )

    def test_character_weighted_distribution(self) -> None:
        """Verify character-proportional duration for a simple two-word case.

        Words: "A" (1 char), "BBB" (3 chars) → total 4 chars, line 1000–5000 ms
        (4000 ms duration).
          - "A"   → dur = int(1/4 * 4000) = 1000 ms  → [1000, 2000)
          - "BBB" → snapped end → [2000, 5000]
        """
        line = LyricLine(
            start_ms=1000,
            end_ms=5000,
            syllables=[
                SyllableSpan(abs_start_ms=1000, abs_end_ms=3000, text="Old"),
                SyllableSpan(abs_start_ms=3000, abs_end_ms=5000, text="Line"),
            ],
        )
        result = reconcile_line(line, "A BBB")
        self.assertEqual(result.syllables[0].abs_start_ms, 1000)
        self.assertEqual(result.syllables[0].abs_end_ms,   2000)  # 1 char → 1000 ms
        self.assertEqual(result.syllables[1].abs_start_ms, 2000)
        self.assertEqual(result.syllables[1].abs_end_ms,   5000)  # snapped

    def test_line_bounds_unchanged_rule_b(self) -> None:
        """start_ms and end_ms are locked to the original under Rule B."""
        line = self._build_line()
        result = reconcile_line(line, "Brand new text here")
        self.assertEqual(result.start_ms, line.start_ms)
        self.assertEqual(result.end_ms,   line.end_ms)


class TestExporters(unittest.TestCase):
    """Tests for :func:`~core.sync_engine.to_enhanced_lrc` and
    :func:`~core.sync_engine.to_ttml`."""

    def setUp(self) -> None:
        self.song = _simple_song()

    # ── Enhanced LRC ────────────────────────────────────────────────────────

    def test_enhanced_lrc_contains_line_timestamp(self) -> None:
        """Each line begins with an [mm:ss.xx] tag."""
        lrc = to_enhanced_lrc(self.song)
        # First content line (after metadata) must have a bracket timestamp
        content_lines = [l for l in lrc.split("\n") if l.startswith("[")]
        # Metadata lines + lyric lines
        self.assertTrue(
            any(re.match(r"\[\d{2}:\d{2}\.\d{2}\]", l) for l in content_lines)
        )

    def test_enhanced_lrc_contains_syllable_tags(self) -> None:
        """Syllable inline tags of the form <mm:ss.xx> appear in output."""
        lrc = to_enhanced_lrc(self.song)
        self.assertRegex(lrc, r"<\d{2}:\d{2}\.\d{2}>")

    def test_enhanced_lrc_first_line_format(self) -> None:
        """First lyric line: [00:01.00]<00:01.00>Hel <00:01.40>lo <00:02.00>World<00:02.50>"""
        lrc = to_enhanced_lrc(self.song)
        # Find the first lyric line (not a metadata tag)
        lyric_lines = [
            l for l in lrc.split("\n")
            if re.match(r"\[\d{2}:\d{2}\.\d{2}\]", l)
        ]
        self.assertTrue(len(lyric_lines) >= 1)
        first = lyric_lines[0]
        self.assertTrue(first.startswith("[00:01.00]"))
        self.assertIn("<00:01.00>Hel", first)
        self.assertIn("<00:01.40>lo", first)
        self.assertIn("<00:02.00>World", first)
        self.assertIn("<00:02.50>", first)  # trailing end tag

    def test_enhanced_lrc_metadata_lines(self) -> None:
        """Metadata entries appear as [key:value] lines before lyric lines."""
        lrc = to_enhanced_lrc(self.song)
        self.assertIn("[ti:Test Song]", lrc)
        self.assertIn("[ar:Artist]", lrc)

    # ── TTML ────────────────────────────────────────────────────────────────

    def test_ttml_xml_declaration(self) -> None:
        """Output begins with an XML declaration."""
        ttml = to_ttml(self.song)
        self.assertTrue(ttml.startswith('<?xml version="1.0" encoding="UTF-8"?>'))

    def test_ttml_itunes_timing_attribute(self) -> None:
        """itunes:timing="Word" attribute is present in the root element."""
        ttml = to_ttml(self.song)
        self.assertIn('itunes:timing="Word"', ttml)

    def test_ttml_contains_span_elements(self) -> None:
        """Each syllable produces a <span> element with begin/end attributes."""
        ttml = to_ttml(self.song)
        self.assertRegex(ttml, r'<span begin="\d{2}:\d{2}:\d{2}\.\d{3}"')

    def test_ttml_xml_entity_escaping(self) -> None:
        """Text containing '&' or '<' is escaped in TTML output."""
        song = SongLyrics(
            metadata={},
            lines=[
                LyricLine(
                    start_ms=0,
                    end_ms=1000,
                    syllables=[
                        SyllableSpan(abs_start_ms=0, abs_end_ms=500, text="R&B"),
                        SyllableSpan(abs_start_ms=500, abs_end_ms=1000, text="<3"),
                    ],
                )
            ],
        )
        ttml = to_ttml(song)
        self.assertIn("R&amp;B", ttml)
        self.assertIn("&lt;3", ttml)
        self.assertNotIn("R&B", ttml)    # unescaped must not appear
        self.assertNotIn("<3", ttml)     # unescaped must not appear (except in tags)

    def test_ttml_p_element_timestamps(self) -> None:
        """<p> elements carry begin/end in HH:MM:SS.mmm format."""
        ttml = to_ttml(self.song)
        self.assertIn('begin="00:00:01.000"', ttml)   # line 1 start_ms=1000
        self.assertIn('end="00:00:03.000"', ttml)     # line 1 end_ms=3000

    def test_ttml_background_text_export(self) -> None:
        """Background text is serialized into ttm:role='x-bg' spans with ttm namespace."""
        song = SongLyrics(
            metadata={},
            lines=[
                LyricLine(
                    start_ms=1000,
                    end_ms=4000,
                    _text="Lead vocal",
                    background_text="(Background)",
                )
            ],
        )
        ttml = to_ttml(song)
        self.assertIn('xmlns:ttm="http://www.w3.org/ns/ttml#metadata"', ttml)
        self.assertIn('<span ttm:role="x-bg">', ttml)
        self.assertIn("(Background)", ttml)
        self.assertIn("Lead vocal", ttml)

    def test_ttml_background_syllables_export(self) -> None:
        """Background syllables generate timed child spans inside ttm:role='x-bg'."""
        song = SongLyrics(
            metadata={},
            lines=[
                LyricLine(
                    start_ms=1000,
                    end_ms=5000,
                    syllables=[
                        SyllableSpan(abs_start_ms=1000, abs_end_ms=2500, text="Lead "),
                        SyllableSpan(abs_start_ms=2500, abs_end_ms=3500, text="vocal"),
                    ],
                    background_syllables=[
                        SyllableSpan(abs_start_ms=3500, abs_end_ms=4200, text="(Back"),
                        SyllableSpan(abs_start_ms=4200, abs_end_ms=5000, text="ground)"),
                    ],
                )
            ],
        )
        ttml = to_ttml(song)
        self.assertIn('xmlns:ttm="http://www.w3.org/ns/ttml#metadata"', ttml)
        self.assertIn('<span ttm:role="x-bg">', ttml)
        self.assertIn('<span begin="00:00:03.500" end="00:00:04.200">(Back</span>', ttml)
        self.assertIn('<span begin="00:00:04.200" end="00:00:05.000">ground)</span>', ttml)



class TestPlainText(unittest.TestCase):
    """Tests for :func:`~core.sync_engine.to_plain_text`."""

    def test_strips_all_timestamps(self) -> None:
        """Output contains no bracket or angle-bracket timing tags."""
        song = _simple_song()
        result = to_plain_text(song)
        self.assertNotIn("[", result)
        self.assertNotIn("<", result)

    def test_correct_text_content(self) -> None:
        """Syllable texts are concatenated correctly per line."""
        song = _simple_song()
        result = to_plain_text(song)
        lines = result.split("\n")
        self.assertEqual(lines[0], "HelloWorld")
        self.assertEqual(lines[1], "Goodbye")

    def test_line_count_matches(self) -> None:
        """Number of output lines equals number of LyricLines."""
        song = _simple_song()
        result = to_plain_text(song)
        self.assertEqual(len(result.split("\n")), len(song.lines))


class TestCandidateMatcher(unittest.TestCase):
    """Tests for core/matcher.py score_candidate."""

    def test_duration_scoring(self) -> None:
        from core.matcher import score_candidate
        # Delta <= 1500 ms -> full 40 pts
        s_full = score_candidate("Artist", "Title", 100000, "Artist", "Title", 101000)
        self.assertEqual(s_full, 100.0)

        # Delta = 2750 ms -> 20 pts (midpoint of [1500, 4000])
        s_mid = score_candidate("Artist", "Title", 100000, "Artist", "Title", 102750)
        self.assertAlmostEqual(s_mid, 80.0, places=1)

        # Delta >= 4000 ms -> 0 pts for duration
        s_zero = score_candidate("Artist", "Title", 100000, "Artist", "Title", 105000)
        self.assertAlmostEqual(s_zero, 60.0, places=1)

    def test_primary_artist_and_title(self) -> None:
        from core.matcher import score_candidate
        # Multi-artist Vorbis delimiter extraction
        score = score_candidate(
            audio_artist="Don Toliver; Kali Uchis",
            audio_title="4 Me (feat. Kali Uchis)",
            audio_duration_ms=160000,
            cand_artist="Don Toliver",
            cand_title="4 Me",
            cand_duration_ms=160500,
        )
        self.assertGreaterEqual(score, 90.0)


class TestSettingsManager(unittest.TestCase):
    """Tests for core/config.py Settings and SettingsManager."""

    def test_load_and_save_persistence(self) -> None:
        from core.config import SettingsManager, Settings
        sm = SettingsManager(organization="lyric-sync-tool-test", application="UnitTestConfig")
        settings = sm.load_settings()
        self.assertIsInstance(settings, Settings)

        settings.active_provider = "Unison"
        settings.duration_tolerance_ms = 3456
        settings.purge_stale = False
        settings.create_backup = True
        settings.force_overwrite = True
        settings.theme = "OLED Dark"

        sm.save_settings(settings)
        reloaded = sm.load_settings()

        self.assertEqual(reloaded.active_provider, "Unison")
        self.assertEqual(reloaded.duration_tolerance_ms, 3456)
        self.assertFalse(reloaded.purge_stale)
        self.assertTrue(reloaded.create_backup)
        self.assertTrue(reloaded.force_overwrite)
        self.assertEqual(reloaded.theme, "OLED Dark")


class TestMultiProviders(unittest.TestCase):
    """Tests for LrcRedProvider and UnisonProvider."""

    def test_provider_names_and_classes(self) -> None:
        from core.providers.kugou import KugouProvider
        from core.providers.lrc_red import LrcRedProvider
        from core.providers.unison import UnisonProvider

        p1 = KugouProvider()
        p2 = LrcRedProvider()
        p3 = UnisonProvider()

        self.assertEqual(p1.name, "kugou")
        self.assertEqual(p2.name, "lrc.red")
        self.assertEqual(p3.name, "Unison")

    def test_lrc_red_and_unison_parse_synced_lyrics(self) -> None:
        from unittest.mock import MagicMock, patch
        from core.providers.lrc_red import LrcRedProvider
        from core.providers.unison import UnisonProvider

        fake_lrc = "[00:01.00]<00:01.00>Hel<00:01.40>lo [00:02.00]<00:02.00>World\n"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"syncedLyrics": fake_lrc}

        # Test LrcRedProvider
        p_lrc = LrcRedProvider()
        with patch("core.providers.lrc_red._HTTP_SESSION.get", return_value=mock_resp):
            res = p_lrc.search_word_sync("Song", "Artist", 120000)
            self.assertIsNotNone(res)
            self.assertGreater(len(res.lines), 0)

        # Test UnisonProvider
        p_unison = UnisonProvider()
        with patch("core.providers.unison._HTTP_SESSION.get", return_value=mock_resp):
            res = p_unison.search_word_sync("Song", "Artist", 120000)
            self.assertIsNotNone(res)
            self.assertGreater(len(res.lines), 0)


if __name__ == "__main__":
    unittest.main()
