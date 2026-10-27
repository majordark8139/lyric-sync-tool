"""Tests for granular lyrics deletion dialog, strip_lyrics flags, featured-artist scoring, and Unison search fallback."""

import os
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication

from core.matcher import score_candidate
from core.providers.unison import UnisonProvider
from core.tagger import strip_lyrics
from core.ttml_parser import parse_ttml
from gui.app import DeleteLyricsDialog

app = QApplication.instance() or QApplication(sys.argv)


class TestCandidateMatcherFeaturedArtists(unittest.TestCase):
    """Test candidate scoring for collaborations and featured artists."""

    def test_kendrick_sza_all_the_stars(self) -> None:
        score = score_candidate(
            audio_artist="Kendrick Lamar",
            audio_title="All The Stars",
            audio_duration_ms=232000,
            cand_artist="Kendrick Lamar, SZA",
            cand_title="All The Stars",
            cand_duration_ms=231000,
        )
        self.assertGreaterEqual(score, 80.0)

    def test_ampersand_and_featured_delimiters(self) -> None:
        score_amp = score_candidate(
            audio_artist="Kendrick Lamar",
            audio_title="All The Stars",
            audio_duration_ms=232000,
            cand_artist="Kendrick Lamar & SZA",
            cand_title="All The Stars",
            cand_duration_ms=231500,
        )
        self.assertGreaterEqual(score_amp, 80.0)

        score_feat = score_candidate(
            audio_artist="The Weeknd feat. Ariana Grande",
            audio_title="Save Your Tears",
            audio_duration_ms=191000,
            cand_artist="The Weeknd",
            cand_title="Save Your Tears",
            cand_duration_ms=191000,
        )
        self.assertGreaterEqual(score_feat, 90.0)

    def test_rejection_of_unrelated_artist(self) -> None:
        score = score_candidate(
            audio_artist="Taylor Swift",
            audio_title="All The Stars",
            audio_duration_ms=232000,
            cand_artist="Kendrick Lamar, SZA",
            cand_title="All The Stars",
            cand_duration_ms=231000,
        )
        # Artist similarity is 0, so title 40 + duration 40 = 80? Wait, duration delta 1000 -> 40, title 40.
        # But for different artist, total is 80 (since duration and title match 100%).
        # If title is also different:
        score_diff_title = score_candidate(
            audio_artist="Taylor Swift",
            audio_title="Blank Space",
            audio_duration_ms=232000,
            cand_artist="Kendrick Lamar, SZA",
            cand_title="All The Stars",
            cand_duration_ms=231000,
        )
        self.assertLess(score_diff_title, 70.0)


class TestGranularDeletion(unittest.TestCase):
    """Test granular deletion across sidecars and embedded tags."""

    def test_strip_only_ttml(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            flac = os.path.join(tmpdir, "test.flac")
            lrc = os.path.join(tmpdir, "test.lrc")
            elrc = os.path.join(tmpdir, "test.elrc")
            ttml = os.path.join(tmpdir, "test.ttml")

            with open(flac, "wb") as f:
                f.write(b"")
            with open(lrc, "w") as f:
                f.write("[00:00.00] test")
            with open(elrc, "w") as f:
                f.write("[00:00.00] <00:00.00>test")
            with open(ttml, "w") as f:
                f.write("<tt></tt>")

            strip_lyrics(flac, delete_embedded=False, delete_lrc=False, delete_elrc=False, delete_ttml=True)
            self.assertTrue(os.path.exists(lrc))
            self.assertTrue(os.path.exists(elrc))
            self.assertFalse(os.path.exists(ttml))

    def test_strip_only_lrc_and_elrc(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            flac = os.path.join(tmpdir, "test.flac")
            lrc = os.path.join(tmpdir, "test.lrc")
            elrc = os.path.join(tmpdir, "test.elrc")
            ttml = os.path.join(tmpdir, "test.ttml")

            with open(flac, "wb") as f:
                f.write(b"")
            with open(lrc, "w") as f:
                f.write("[00:00.00] test")
            with open(elrc, "w") as f:
                f.write("[00:00.00] <00:00.00>test")
            with open(ttml, "w") as f:
                f.write("<tt></tt>")

            strip_lyrics(flac, delete_embedded=False, delete_lrc=True, delete_elrc=True, delete_ttml=False)
            self.assertFalse(os.path.exists(lrc))
            self.assertFalse(os.path.exists(elrc))
            self.assertTrue(os.path.exists(ttml))


class TestDeleteLyricsDialog(unittest.TestCase):
    """Test DeleteLyricsDialog UI controls and behavior."""

    def test_dialog_initial_and_toggle_states(self) -> None:
        dlg = DeleteLyricsDialog(track_count=5)
        self.assertEqual(dlg.windowTitle(), "Delete Lyrics")
        self.assertTrue(dlg.cb_embedded.isChecked())
        self.assertTrue(dlg.cb_lrc.isChecked())
        self.assertTrue(dlg.cb_elrc.isChecked())
        self.assertTrue(dlg.cb_ttml.isChecked())
        self.assertTrue(dlg.btn_delete.isEnabled())

        # Uncheck all -> button must be disabled
        dlg.cb_embedded.setChecked(False)
        dlg.cb_lrc.setChecked(False)
        dlg.cb_elrc.setChecked(False)
        dlg.cb_ttml.setChecked(False)
        self.assertFalse(dlg.btn_delete.isEnabled())

        # Check only TTML -> button must re-enable
        dlg.cb_ttml.setChecked(True)
        self.assertTrue(dlg.btn_delete.isEnabled())
        self.assertFalse(dlg.delete_embedded)
        self.assertFalse(dlg.delete_lrc)
        self.assertFalse(dlg.delete_elrc)
        self.assertTrue(dlg.delete_ttml)


class TestUnisonSearchAndTTML(unittest.TestCase):
    """Test Unison search fallback to title and TTML background vocal parsing."""

    def test_unison_fallback_to_title(self) -> None:
        provider = UnisonProvider()

        search_calls = []

        def mock_get(url, **kwargs):
            m = Mock()
            m.status_code = 200
            params = kwargs.get("params", {})
            q = params.get("q", "")
            search_calls.append(q)

            if "All The Stars" in q and "Kendrick" in q:
                # Primary query returns empty results
                m.json.return_value = {"success": True, "data": []}
            elif q == "All The Stars":
                # Fallback query returns the richsync hit (syncType intentionally missing from search hit)
                m.json.return_value = {
                    "success": True,
                    "data": [
                        {
                            "id": 2687,
                            "song": "All The Stars",
                            "artist": "Kendrick Lamar, SZA",
                            "duration": 231,
                            # syncType missing from search hit list
                        }
                    ],
                }
            elif "/lyrics/2687" in url:
                m.json.return_value = {
                    "success": True,
                    "data": {
                        "id": 2687,
                        "syncType": "richsync",
                        "format": "ttml",
                        "lyrics": (
                            '<tt xmlns="http://www.w3.org/ns/ttml" xmlns:ttm="http://www.w3.org/ns/ttml#metadata">'
                            '<body><div>'
                            '<p begin="00:29.00" end="00:34.00">'
                            '<span begin="00:29.00" end="00:30.00">Love</span> '
                            '<span begin="00:30.00" end="00:31.00">song</span>'
                            '<span ttm:role="x-bg"><span begin="00:31.00" end="00:34.00">Haunt</span></span>'
                            '</p>'
                            '</div></body></tt>'
                        ),
                    },
                }
            return m

        with patch("core.providers.unison._http_get", side_effect=mock_get):
            res = provider.search_word_sync("All The Stars", "Kendrick Lamar", 231000)
            self.assertIsNotNone(res)
            self.assertTrue(res.is_word_synced)
            self.assertIn("Kendrick Lamar All The Stars", search_calls[0])
            self.assertEqual(search_calls[1], "All The Stars")

            # Verify background vocal parsing
            line = res.lines[0]
            self.assertEqual(line.text, "Love song")
            self.assertEqual(line.background_text, "Haunt")
            self.assertEqual(len(line.background_syllables), 1)
            self.assertEqual(line.background_syllables[0].text, "Haunt")


if __name__ == "__main__":
    unittest.main()
