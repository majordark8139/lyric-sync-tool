"""Unit tests for metadata normalizer and Cascading Query Ladder in core/normalizer.py."""

import unittest

from core.normalizer import (
    extract_collaborators,
    extract_primary_artist,
    generate_query_ladder,
    normalize_punctuation,
    strip_features,
    strip_reissue_and_bonus,
)


class TestNormalizerAndLadder(unittest.TestCase):
    """Test suite for metadata normalization and cascading search ladder generation."""

    def test_strip_reissue_and_bonus_cleans_bonus_track_protects_remix(self) -> None:
        """Verify strip_reissue_and_bonus cleans '- Bonus Track' while protecting '(Remix)'."""
        # Bonus track variations
        self.assertEqual(
            strip_reissue_and_bonus("Final Lullaby - Bonus Track"),
            "Final Lullaby",
        )
        self.assertEqual(
            strip_reissue_and_bonus("Song Title (Bonus Track)"),
            "Song Title",
        )
        self.assertEqual(
            strip_reissue_and_bonus("Track [Bonus Track]"),
            "Track",
        )

        # Remaster and Deluxe variations
        self.assertEqual(
            strip_reissue_and_bonus("Starboy - 2020 Remaster"),
            "Starboy",
        )
        self.assertEqual(
            strip_reissue_and_bonus("After Hours (Deluxe Edition)"),
            "After Hours",
        )
        self.assertEqual(
            strip_reissue_and_bonus("Explicit Song [Clean]"),
            "Explicit Song",
        )

        # Protection: Remix, VIP, Acoustic, Live must NOT be stripped
        self.assertEqual(
            strip_reissue_and_bonus("Save Your Tears (Remix)"),
            "Save Your Tears (Remix)",
        )
        self.assertEqual(
            strip_reissue_and_bonus("In Your Eyes (Acoustic)"),
            "In Your Eyes (Acoustic)",
        )
        self.assertEqual(
            strip_reissue_and_bonus("Blinding Lights (Live)"),
            "Blinding Lights (Live)",
        )
        self.assertEqual(
            strip_reissue_and_bonus("Bassline (VIP Mix)"),
            "Bassline (VIP Mix)",
        )

    def test_generate_query_ladder_with_featured_artist_and_remix(self) -> None:
        """Verify generate_query_ladder on 'The Weeknd', 'Save Your Tears (Remix) (With Ariana Grande)'."""
        artist = "The Weeknd"
        title = "Save Your Tears (Remix) (With Ariana Grande)"

        ladder = generate_query_ladder(artist, title)

        # 1. Raw query must be first
        self.assertEqual(ladder[0], ("The Weeknd", "Save Your Tears (Remix) (With Ariana Grande)"))

        # 2. Ladder must contain feature-stripped title with primary artist
        self.assertIn(("The Weeknd", "Save Your Tears (Remix)"), ladder)

        # 3. Ladder must contain collaborator promoted into artist
        self.assertIn(("The Weeknd Ariana Grande", "Save Your Tears (Remix)"), ladder)

        # 4. Check deduplication and ordering
        self.assertEqual(len(ladder), len(set(ladder)))
        idx_raw = ladder.index(("The Weeknd", "Save Your Tears (Remix) (With Ariana Grande)"))
        idx_stripped = ladder.index(("The Weeknd", "Save Your Tears (Remix)"))
        idx_collab = ladder.index(("The Weeknd Ariana Grande", "Save Your Tears (Remix)"))
        self.assertTrue(idx_raw < idx_stripped < idx_collab)

    def test_generate_query_ladder_with_bonus_track(self) -> None:
        """Verify generate_query_ladder on 'The Weeknd', 'Final Lullaby - Bonus Track'."""
        artist = "The Weeknd"
        title = "Final Lullaby - Bonus Track"

        ladder = generate_query_ladder(artist, title)

        # Raw query first
        self.assertEqual(ladder[0], ("The Weeknd", "Final Lullaby - Bonus Track"))

        # Reissue / bonus stripped query must be present
        self.assertIn(("The Weeknd", "Final Lullaby"), ladder)

        # Verify deduplication
        self.assertEqual(len(ladder), len(set(ladder)))

    def test_normalize_punctuation(self) -> None:
        """Verify normalization of curly quotes, full-width brackets, and long dashes."""
        raw = "‘Artist’ — “Title” （feat. Singer） 【VIP】"
        expected = "'Artist' - \"Title\" (feat. Singer) [VIP]"
        self.assertEqual(normalize_punctuation(raw), expected)

    def test_strip_features(self) -> None:
        """Verify feature stripping while protecting remixes."""
        self.assertEqual(
            strip_features("Save Your Tears (Remix) (With Ariana Grande)"),
            "Save Your Tears (Remix)",
        )
        self.assertEqual(
            strip_features("Song Title (feat. Drake & Future)"),
            "Song Title",
        )
        self.assertEqual(
            strip_features("Love Me [ft. Sia]"),
            "Love Me",
        )
        self.assertEqual(
            strip_features("Track - feat. Guest"),
            "Track",
        )

    def test_extract_collaborators(self) -> None:
        """Verify extraction of primary artist and collaborators from both fields."""
        lead, featured = extract_collaborators(
            "The Weeknd, Daft Punk", "I Feel It Coming (feat. Future)"
        )
        self.assertEqual(lead, "The Weeknd")
        self.assertIn("Daft Punk", featured)
        self.assertIn("Future", featured)

    def test_sanitize_search_query(self) -> None:
        """Verify replacement of (), [], {}, -, _, /, \\ with spaces, space collapsing, and trimming."""
        from core.normalizer import sanitize_search_query
        raw = " (Artist) - [Song_Title] {Remix} / Mix\\ "
        expected = "Artist Song Title Remix Mix"
        self.assertEqual(sanitize_search_query(raw), expected)
        self.assertEqual(sanitize_search_query(""), "")

    def test_extract_primary_artist(self) -> None:
        """Verify extraction of lead primary artist across multi-artist delimiters."""
        self.assertEqual(extract_primary_artist("Don Toliver; Kali Uchis"), "Don Toliver")
        self.assertEqual(extract_primary_artist("Lana Del Rey; The Weeknd"), "Lana Del Rey")
        self.assertEqual(extract_primary_artist("A / B \\ C"), "A")
        self.assertEqual(extract_primary_artist("Don Toliver; Justin Bieber; Future"), "Don Toliver")
        self.assertEqual(extract_primary_artist("The Weeknd feat. Daft Punk"), "The Weeknd")
        self.assertEqual(extract_primary_artist("Artist1 & Artist2"), "Artist1")
        self.assertEqual(extract_primary_artist("Solo Artist"), "Solo Artist")
        self.assertEqual(extract_primary_artist(""), "")

    def test_generate_query_ladder(self) -> None:
        """Verify generate_query_ladder prioritizes primary artist on multi-artist tracks."""
        ladder_1 = generate_query_ladder("Don Toliver; Kali Uchis", "4 Me (feat. Kali Uchis)")
        self.assertIn(("Don Toliver", "4 Me"), ladder_1)
        self.assertEqual(ladder_1[0], ("Don Toliver", "4 Me"))

        ladder_2 = generate_query_ladder("Lana Del Rey; The Weeknd", "Lust for Life")
        self.assertIn(("Lana Del Rey", "Lust for Life"), ladder_2)
        self.assertEqual(ladder_2[0], ("Lana Del Rey", "Lust for Life"))


class TestKugouLadderSearch(unittest.TestCase):
    """Test KugouProvider search ladder logic, candidate ranking, and fallback."""

    def test_smart_fallback_disabled_queries_only_raw(self) -> None:
        """When smart_fallback is False, only the exact raw query is searched."""
        from unittest.mock import patch
        from core.models import TaggingConfig
        from core.providers.kugou import KugouProvider

        provider = KugouProvider()
        queries_made = []

        def mock_make_request(url: str):
            queries_made.append(url)
            return {"candidates": []}

        config = TaggingConfig(smart_fallback=False)
        with patch("core.providers.kugou._make_request", side_effect=mock_make_request):
            provider.search_word_sync(
                title="Save Your Tears (Remix) (With Ariana Grande)",
                artist="The Weeknd",
                duration_ms=210000,
                config=config,
            )

        # Only one query was executed
        self.assertEqual(len(queries_made), 1)
        self.assertIn("Save+Your+Tears+%28Remix%29+%28With+Ariana+Grande%29", queries_made[0])

    def test_ladder_fallback_and_krctype_prioritization(self) -> None:
        """Verify search steps fallback and prioritize krctype=2 candidate."""
        from unittest.mock import patch
        from tests.test_core import _make_krc_payload
        from core.providers.kugou import KugouProvider

        provider = KugouProvider()
        valid_krc = (
            "[ti:Test]\n[ar:Artist]\n"
            "[1000,2000]<0,350,-1>Hel<350,300,-1>lo\n"
        )
        payload = _make_krc_payload(valid_krc)

        download_ids_requested = []

        def mock_make_request(url: str):
            if "lyrics.kugou.com/search" in url:
                if "Save+Your+Tears+%28Remix%29" in url and "Ariana" not in url:
                    # Return candidates: one krctype=1 and one krctype=2
                    return {
                        "candidates": [
                            {"id": "c1", "accesskey": "k1", "duration": 210000, "score": 100, "krctype": 1},
                            {"id": "c2", "accesskey": "k2", "duration": 210000, "score": 90, "krctype": 2},
                        ]
                    }
                return {"candidates": []}
            elif "lyrics.kugou.com/download" in url:
                if "id=c2" in url:
                    download_ids_requested.append("c2")
                    return {"content": payload}
                elif "id=c1" in url:
                    download_ids_requested.append("c1")
                    return {"content": payload}
            return None

        with patch("core.providers.kugou._make_request", side_effect=mock_make_request):
            res = provider.search_word_sync(
                title="Save Your Tears (Remix) (With Ariana Grande)",
                artist="The Weeknd",
                duration_ms=210000,
            )

        self.assertIsNotNone(res)
        # krctype=2 (id c2) must have been chosen first despite lower score
        self.assertEqual(download_ids_requested[0], "c2")

    def test_title_only_fallback_candidates_in_ladder(self) -> None:
        """Verify title-only fallback candidates are at the end of the ladder."""
        from core.normalizer import generate_query_ladder
        ladder = generate_query_ladder("Coldplay", "Yellow (Live) - Bonus Track")
        # Check that empty artist candidates exist
        empty_artist_cands = [cand for cand in ladder if cand[0] == ""]
        self.assertTrue(len(empty_artist_cands) >= 1)
        self.assertIn(("", "Yellow Live Bonus Track"), empty_artist_cands)
        self.assertIn(("", "Yellow Live"), empty_artist_cands)
        # The last candidates in the ladder should be title-only
        self.assertEqual(ladder[-1][0], "")

    def test_kugou_search_with_empty_artist_keyword_format(self) -> None:
        """When q_artist is empty, the Kugou keyword is formatted as just the title without leading dash."""
        from unittest.mock import patch
        from core.providers.kugou import KugouProvider

        provider = KugouProvider()
        queries_made = []

        def mock_make_request(url: str):
            queries_made.append(url)
            return {"candidates": []}

        with patch("core.providers.kugou._make_request", side_effect=mock_make_request):
            provider.search_word_sync(
                title="A Sky Full of Stars",
                artist="",
                duration_ms=260000,
            )

        self.assertTrue(len(queries_made) > 0)
        # Verify query param has keyword=A+Sky+Full+of+Stars and does not have keyword=-+
        first_query = queries_made[0]
        self.assertIn("keyword=A+Sky+Full+of+Stars", first_query)
        self.assertNotIn("keyword=-+", first_query)
        self.assertNotIn("keyword=%20-+", first_query)


if __name__ == "__main__":
    unittest.main()
