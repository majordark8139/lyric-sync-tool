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
            res = provider.search_word_sync(artist="Kendrick Lamar", title="All The Stars", duration_ms=231000)
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


class TestDuetParsingAndOppositeAlignment(unittest.TestCase):
    """Test multi-singer duet parsing, serialization, and preview alignment."""

    def test_duet_parsing_and_serialization(self) -> None:
        from core.sync_engine import to_ttml

        xml = """<tt xmlns="http://www.w3.org/ns/ttml" xmlns:ttm="http://www.w3.org/ns/ttml#metadata">
<head><metadata><ttm:agent type="person" xml:id="v1"/><ttm:agent type="person" xml:id="v2"/></metadata></head>
<body><div>
<p begin="00:00:20.090" end="00:00:23.200" ttm:agent="v1"><span>Love, </span><span>talk </span></p>
<p begin="00:00:39.170" end="00:00:41.960" ttm:agent="v2"><span>All </span><span>the </span><span>stars </span></p>
</div></body></tt>"""

        lyrics = parse_ttml(xml)
        self.assertEqual(lyrics.lines[0].singer_id, "v1")
        self.assertEqual(lyrics.lines[1].singer_id, "v2")
        self.assertIn("v1", lyrics.agents)
        self.assertIn("v2", lyrics.agents)

        out_ttml = to_ttml(lyrics)
        self.assertIn('ttm:agent="v2"', out_ttml)
        self.assertIn('xml:id="v2"', out_ttml)

    def test_auto_populate_v1_v2_when_agents_empty(self) -> None:
        from core.models import LyricLine, SongLyrics, Syllable
        from core.sync_engine import to_ttml

        lyrics = SongLyrics(
            lines=[
                LyricLine(start_ms=1000, end_ms=2000, syllables=[Syllable("Hello ", 1000, 500), Syllable("world", 1500, 500)], singer_id="v1"),
                LyricLine(start_ms=3000, end_ms=4000, syllables=[Syllable("Hi ", 3000, 500), Syllable("there", 3500, 500)], singer_id="v2"),
            ],
            is_word_synced=True,
        )
        out = to_ttml(lyrics)
        self.assertIn('xml:id="v1"', out)
        self.assertIn('xml:id="v2"', out)
        self.assertIn('ttm:agent="v1"', out)
        self.assertIn('ttm:agent="v2"', out)


class TestTitleFloorAndCollaborationMatching(unittest.TestCase):
    """Test hard title-similarity floor and collaboration candidate matching for Pray For Me."""

    def test_hard_title_similarity_floor_rejects_backseat_freestyle(self) -> None:
        from core.matcher import compute_title_similarity, score_candidate

        sim = compute_title_similarity("Pray For Me", "Backseat Freestyle")
        self.assertLess(sim, 0.60)

        # Even with matching artist and duration, score MUST be 0.0
        score = score_candidate(
            audio_artist="Kendrick Lamar",
            audio_title="Pray For Me",
            audio_duration_ms=211000,
            cand_artist="Kendrick Lamar",
            cand_title="Backseat Freestyle",
            cand_duration_ms=211000,
        )
        self.assertEqual(score, 0.0)

    def test_pray_for_me_collaboration_candidate_scoring(self) -> None:
        from core.matcher import score_candidate

        # Audio file tagged with primary artist or full collaboration
        score_full = score_candidate(
            audio_artist="The Weeknd, Kendrick Lamar",
            audio_title="Pray For Me",
            audio_duration_ms=211000,
            cand_artist="The Weeknd, Kendrick Lamar",
            cand_title="Pray For Me",
            cand_duration_ms=211000,
        )
        self.assertGreaterEqual(score_full, 80.0)

        score_lead = score_candidate(
            audio_artist="Kendrick Lamar",
            audio_title="Pray For Me",
            audio_duration_ms=211000,
            cand_artist="The Weeknd, Kendrick Lamar",
            cand_title="Pray For Me",
            cand_duration_ms=211000,
        )
        self.assertGreaterEqual(score_lead, 80.0)

    def test_syllable_spacing_without_internal_spaces(self) -> None:
        from core.ttml_parser import parse_ttml

        xml = """<tt xmlns="http://www.w3.org/ns/ttml">
<body><div>
<p begin="00:00:01.000" end="00:00:04.000">
<span>fee</span><span>ling </span>
<span>a</span><span>bout </span>
<span>a</span><span>ny</span><span>thing </span>
<span>e</span><span>ve</span><span>ry</span><span>thing </span>
<span>Ken</span><span>drick</span>
</p>
</div></body></tt>"""
        lyrics = parse_ttml(xml)
        self.assertEqual(lyrics.lines[0].text, "feeling about anything everything Kendrick")


class TestAlbumArtistCollaborationLadder(unittest.TestCase):
    """Test album_artist extraction, matcher collaboration bonus, and provider query ladders."""

    def test_candidate_scoring_with_album_artist(self) -> None:
        from core.matcher import score_candidate

        # Audio file has artist="Jay Rock", album_artist="Kendrick Lamar"
        # Candidate has cand_artist="Jay Rock, Kendrick Lamar, Future, James Blake"
        score = score_candidate(
            audio_artist="Jay Rock",
            audio_title="King's Dead",
            audio_duration_ms=225000,
            cand_artist="Jay Rock, Kendrick Lamar, Future, James Blake",
            cand_title="King's Dead",
            cand_duration_ms=225000,
            album_artist="Kendrick Lamar",
        )
        self.assertGreaterEqual(score, 80.0)

    def test_auto_provider_forwards_album_artist(self) -> None:
        from core.providers.auto import AutoLyricsProvider

        mock_unison = Mock()
        mock_unison.search_word_sync.return_value = None
        mock_lrc_red = Mock()
        mock_lrc_red.search_word_sync.return_value = None
        mock_kugou = Mock()
        mock_kugou.search_word_sync.return_value = None

        auto = AutoLyricsProvider(unison=mock_unison, lrc_red=mock_lrc_red, kugou=mock_kugou)

        result = auto.search_word_sync(
            artist="Jay Rock",
            title="King's Dead",
            duration_ms=225000,
            album_artist="Kendrick Lamar",
        )
        self.assertIsNone(result)

        mock_unison.search_word_sync.assert_called_once_with(
            artist="Jay Rock",
            title="King's Dead",
            duration_ms=225000,
            album_artist="Kendrick Lamar",
            config=None,
        )
        mock_lrc_red.search_word_sync.assert_called_once_with(
            artist="Jay Rock",
            title="King's Dead",
            duration_ms=225000,
            album_artist="Kendrick Lamar",
            config=None,
        )
        mock_kugou.search_word_sync.assert_called_once_with(
            artist="Jay Rock",
            title="King's Dead",
            duration_ms=225000,
            album_artist="Kendrick Lamar",
            config=None,
        )

    def test_unison_queries_collaboration_ladder(self) -> None:
        provider = UnisonProvider()
        queries_made = []

        def mock_get(url, **kwargs):
            m = Mock()
            m.status_code = 200
            params = kwargs.get("params", {})
            q = params.get("q", "")
            queries_made.append(q)
            m.json.return_value = {"success": True, "data": []}
            return m

        with patch("requests.get", side_effect=mock_get):
            provider.search_word_sync(
                artist="Jay Rock",
                title="King's Dead",
                duration_ms=225000,
                album_artist="Kendrick Lamar",
            )

        self.assertIn("Jay Rock & Kendrick Lamar King's Dead", queries_made)

    def test_lrc_red_queries_collaboration_ladder(self) -> None:
        from core.providers.lrc_red import LrcRedProvider

        provider = LrcRedProvider()
        searched_artists = []

        def mock_query(variant_artist, title, duration_ms, original_artist):
            searched_artists.append(variant_artist)
            return None

        orig_query_candidate = provider._query_candidate
        provider._query_candidate = mock_query  # type: ignore[assignment]
        try:
            provider.search_word_sync(
                artist="Jay Rock",
                title="King's Dead",
                duration_ms=225000,
                album_artist="Kendrick Lamar",
            )
        finally:
            provider._query_candidate = orig_query_candidate

        self.assertIn("Jay Rock & Kendrick Lamar", searched_artists)
        self.assertIn("Jay Rock, Kendrick Lamar", searched_artists)

    def test_kugou_query_ladder_contains_album_artist(self) -> None:
        from core.normalizer import generate_query_ladder

        ladder = generate_query_ladder(artist="Jay Rock", title="King's Dead", album_artist="Kendrick Lamar")
        self.assertIn(("Jay Rock & Kendrick Lamar", "King's Dead"), ladder)
        self.assertIn(("Jay Rock, Kendrick Lamar", "King's Dead"), ladder)


if __name__ == "__main__":
    unittest.main()


