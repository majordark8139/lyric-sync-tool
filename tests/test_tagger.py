"""Unit tests for the audio tagging engine in core/tagger.py."""

import os
import pathlib
import struct
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import mutagen
from mutagen.flac import FLAC
from mutagen.id3 import ID3, SYLT, TXXX, USLT
from mutagen.mp3 import MP3

from core.models import LyricLine, SongLyrics, SyllableSpan, SyncState, TaggingConfig
from core.sync_engine import to_enhanced_lrc, to_plain_text, to_ttml
from core.tagger import apply_lyrics, detect_sync_state, read_track_lyrics, strip_lyrics


def _create_minimal_flac(path: str) -> None:
    """Generate a minimal valid FLAC file with a STREAMINFO metadata block."""
    b8 = (44100 << 44) | (1 << 41) | (15 << 36)
    b8_bytes = b8.to_bytes(8, "big")
    md5 = b"\x00" * 16
    streaminfo_data = (
        struct.pack(">HH3s3s", 4096, 4096, b"\x00\x00\x00", b"\x00\x00\x00")
        + b8_bytes
        + md5
    )
    header = b"fLaC\x80\x00\x00\x22" + streaminfo_data
    with open(path, "wb") as f:
        f.write(header)


def _create_minimal_mp3(path: str) -> None:
    """Generate a minimal valid MP3 file with sufficient MPEG sync frames."""
    frame = b"\xff\xfb\x90\x64" + b"\x00" * 413
    with open(path, "wb") as f:
        f.write(frame * 5)


def _sample_song() -> SongLyrics:
    """Build a sample SongLyrics instance with syllables."""
    return SongLyrics(
        metadata={"ti": "Test Song", "ar": "Sample Artist"},
        lines=[
            LyricLine(
                start_ms=1000,
                end_ms=3000,
                syllables=[
                    SyllableSpan(abs_start_ms=1000, abs_end_ms=1500, text="Hel"),
                    SyllableSpan(abs_start_ms=1500, abs_end_ms=2000, text="lo"),
                    SyllableSpan(abs_start_ms=2000, abs_end_ms=3000, text="World"),
                ],
            ),
            LyricLine(
                start_ms=3500,
                end_ms=5000,
                syllables=[
                    SyllableSpan(abs_start_ms=3500, abs_end_ms=4200, text="Good"),
                    SyllableSpan(abs_start_ms=4200, abs_end_ms=5000, text="bye"),
                ],
            ),
        ],
    )


class TestDetectSyncState(unittest.TestCase):
    """Tests for detect_sync_state across file formats, tags, and sidecars."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = pathlib.Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # ── NONE detection ─────────────────────────────────────────────────────

    def test_detect_none_when_no_tags_and_no_sidecars(self) -> None:
        """Audio file with no lyrics tags and no sidecars returns SyncState.NONE."""
        flac_path = str(self.dir_path / "empty.flac")
        _create_minimal_flac(flac_path)
        self.assertEqual(detect_sync_state(flac_path), SyncState.NONE)

    def test_detect_none_for_plain_text_in_vorbis(self) -> None:
        """Vorbis comment with plain un-timed lyrics returns SyncState.NONE."""
        flac_path = str(self.dir_path / "plain_vorbis.flac")
        _create_minimal_flac(flac_path)
        audio = FLAC(flac_path)
        audio["LYRICS"] = ["Hello world\nThis is a plain lyric line\nNo timestamps here"]
        audio.save()
        self.assertEqual(detect_sync_state(flac_path), SyncState.NONE)

    def test_detect_none_for_plain_text_in_uslt(self) -> None:
        """ID3 USLT frame with plain un-timed lyrics returns SyncState.NONE."""
        mp3_path = str(self.dir_path / "plain_uslt.mp3")
        _create_minimal_mp3(mp3_path)
        audio = MP3(mp3_path)
        audio.add_tags()
        audio.tags.add(USLT(encoding=3, lang="eng", desc="", text="Hello world\nPlain text lyrics"))
        audio.save(v2_version=3)
        self.assertEqual(detect_sync_state(mp3_path), SyncState.NONE)

    # ── LINE detection ─────────────────────────────────────────────────────

    def test_detect_line_from_lrc_sidecar(self) -> None:
        """Sidecar .lrc with standard [mm:ss.xx] line tags returns SyncState.LINE."""
        track_path = str(self.dir_path / "track.flac")
        _create_minimal_flac(track_path)
        lrc_path = self.dir_path / "track.lrc"
        lrc_path.write_text("[ti:Test]\n[00:01.50]Line one\n[00:03.20]Line two\n", encoding="utf-8")
        self.assertEqual(detect_sync_state(track_path), SyncState.LINE)

    def test_detect_line_from_ttml_sidecar(self) -> None:
        """Sidecar .ttml with <p begin=...> lines (no word spans) returns SyncState.LINE."""
        track_path = str(self.dir_path / "track.mp3")
        _create_minimal_mp3(track_path)
        ttml_path = self.dir_path / "track.ttml"
        ttml_path.write_text(
            '<?xml version="1.0"?>\n<tt>\n  <body>\n    <div>\n'
            '      <p begin="00:00:01.000" end="00:00:03.000">Line one</p>\n'
            '    </div>\n  </body>\n</tt>',
            encoding="utf-8",
        )
        self.assertEqual(detect_sync_state(track_path), SyncState.LINE)

    def test_detect_line_from_vorbis_lyrics(self) -> None:
        """Vorbis LYRICS tag with standard [mm:ss.xx] lines returns SyncState.LINE."""
        flac_path = str(self.dir_path / "line_vorbis.flac")
        _create_minimal_flac(flac_path)
        audio = FLAC(flac_path)
        audio["LYRICS"] = ["[00:01.00]First line\n[00:03.50]Second line"]
        audio.save()
        self.assertEqual(detect_sync_state(flac_path), SyncState.LINE)

    def test_detect_line_from_id3_uslt(self) -> None:
        """ID3 USLT frame containing [mm:ss.xx] line tags returns SyncState.LINE."""
        mp3_path = str(self.dir_path / "line_uslt.mp3")
        _create_minimal_mp3(mp3_path)
        audio = MP3(mp3_path)
        audio.add_tags()
        audio.tags.add(USLT(encoding=3, lang="eng", desc="", text="[00:01.23]Line one\n[00:04.56]Line two"))
        audio.save(v2_version=3)
        self.assertEqual(detect_sync_state(mp3_path), SyncState.LINE)

    def test_detect_line_from_id3_sylt(self) -> None:
        """ID3 SYLT frame with line-delimited entries returns SyncState.LINE."""
        mp3_path = str(self.dir_path / "line_sylt.mp3")
        _create_minimal_mp3(mp3_path)
        audio = MP3(mp3_path)
        audio.add_tags()
        # Each entry ends with a newline, indicating line-level synchronization
        audio.tags.add(SYLT(
            encoding=3, lang="eng", format=2, type=1, desc="",
            text=[("Line one\n", 1000), ("Line two\n", 3000)],
        ))
        audio.save(v2_version=3)
        self.assertEqual(detect_sync_state(mp3_path), SyncState.LINE)

    # ── WORD detection ─────────────────────────────────────────────────────

    def test_detect_word_from_enhanced_lrc_sidecar(self) -> None:
        """Sidecar .lrc with Enhanced LRC <mm:ss.xx> tags returns SyncState.WORD."""
        track_path = str(self.dir_path / "enhanced.flac")
        _create_minimal_flac(track_path)
        lrc_path = self.dir_path / "enhanced.lrc"
        lrc_path.write_text("[00:01.00]<00:01.00>Hel <00:01.50>lo <00:02.00>", encoding="utf-8")
        self.assertEqual(detect_sync_state(track_path), SyncState.WORD)

    def test_detect_word_from_ttml_sidecar(self) -> None:
        """Sidecar .ttml with Apple itunes:timing="Word" and <span> tags returns SyncState.WORD."""
        track_path = str(self.dir_path / "track_word.mp3")
        _create_minimal_mp3(track_path)
        ttml_path = self.dir_path / "track_word.ttml"
        ttml_path.write_text(to_ttml(_sample_song()), encoding="utf-8")
        self.assertEqual(detect_sync_state(track_path), SyncState.WORD)

    def test_detect_word_from_vorbis_lyrics(self) -> None:
        """Vorbis LYRICS tag with Enhanced LRC syllable tags returns SyncState.WORD."""
        flac_path = str(self.dir_path / "word_vorbis.flac")
        _create_minimal_flac(flac_path)
        audio = FLAC(flac_path)
        audio["LYRICS"] = [to_enhanced_lrc(_sample_song())]
        audio.save()
        self.assertEqual(detect_sync_state(flac_path), SyncState.WORD)

    def test_detect_word_from_id3_txxx_lyrics(self) -> None:
        """ID3 TXXX:LYRICS frame with Enhanced LRC returns SyncState.WORD."""
        mp3_path = str(self.dir_path / "word_txxx.mp3")
        _create_minimal_mp3(mp3_path)
        audio = MP3(mp3_path)
        audio.add_tags()
        audio.tags.add(TXXX(encoding=3, desc="LYRICS", text=to_enhanced_lrc(_sample_song())))
        audio.save(v2_version=3)
        self.assertEqual(detect_sync_state(mp3_path), SyncState.WORD)

    def test_detect_word_from_id3_sylt(self) -> None:
        """ID3 SYLT frame with multiple syllable/word tokens per line returns SyncState.WORD."""
        mp3_path = str(self.dir_path / "word_sylt.mp3")
        _create_minimal_mp3(mp3_path)
        audio = MP3(mp3_path)
        audio.add_tags()
        # Words do not end with newlines, representing intra-line syllables
        audio.tags.add(SYLT(
            encoding=3, lang="eng", format=2, type=1, desc="",
            text=[("Hel", 1000), ("lo ", 1500), ("World\n", 2000)],
        ))
        audio.save(v2_version=3)
        self.assertEqual(detect_sync_state(mp3_path), SyncState.WORD)

    def test_detect_word_from_kugou_krc_syntax(self) -> None:
        """Kugou KRC syntax <0,350,-1> in LYRICS Vorbis tag returns SyncState.WORD."""
        flac_path = str(self.dir_path / "krc_tag.flac")
        _create_minimal_flac(flac_path)
        audio = FLAC(flac_path)
        audio["LYRICS"] = ["[1000,2000]<0,350,-1>Hel<350,300,-1>lo"]
        audio.save()
        self.assertEqual(detect_sync_state(flac_path), SyncState.WORD)

    def test_word_sync_precedence_over_plain_and_line(self) -> None:
        """When an MP3 has both plain USLT and word-synced TXXX:LYRICS, WORD takes precedence."""
        mp3_path = str(self.dir_path / "hybrid.mp3")
        _create_minimal_mp3(mp3_path)
        audio = MP3(mp3_path)
        audio.add_tags()
        audio.tags.add(USLT(encoding=3, lang="eng", desc="", text="Plain text without timing"))
        audio.tags.add(TXXX(encoding=3, desc="LYRICS", text="[00:01.00]<00:01.00>Hel <00:01.50>lo"))
        audio.save(v2_version=3)
        self.assertEqual(detect_sync_state(mp3_path), SyncState.WORD)


class TestApplyLyrics(unittest.TestCase):
    """Tests for apply_lyrics: sidecar generation and embedded metadata."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = pathlib.Path(self.temp_dir.name)
        self.song = _sample_song()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_apply_sidecars_only(self) -> None:
        """write_sidecars=True writes valid Enhanced LRC and TTML files."""
        fake_audio = str(self.dir_path / "track.mp3")
        apply_lyrics(fake_audio, self.song, embed=False, write_sidecars=True)

        lrc_path = self.dir_path / "track.lrc"
        ttml_path = self.dir_path / "track.ttml"

        self.assertTrue(lrc_path.is_file())
        self.assertTrue(ttml_path.is_file())

        expected_lrc = to_enhanced_lrc(self.song)
        expected_ttml = to_ttml(self.song)

        self.assertEqual(lrc_path.read_text(encoding="utf-8"), expected_lrc)
        self.assertEqual(ttml_path.read_text(encoding="utf-8"), expected_ttml)

        # Sidecar detection returns SyncState.WORD
        self.assertEqual(detect_sync_state(fake_audio), SyncState.WORD)

    def test_apply_embed_flac(self) -> None:
        """embed=True writes Enhanced LRC into FLAC Vorbis comment LYRICS block."""
        flac_path = str(self.dir_path / "song.flac")
        _create_minimal_flac(flac_path)

        apply_lyrics(flac_path, self.song, embed=True, write_sidecars=False)

        # Sidecars should not have been written
        self.assertFalse((self.dir_path / "song.lrc").exists())
        self.assertFalse((self.dir_path / "song.ttml").exists())

        # Reload FLAC and verify tags
        reloaded = FLAC(flac_path)
        lyrics_tag = reloaded.get("LYRICS")
        self.assertIsNotNone(lyrics_tag)
        expected_lrc = to_enhanced_lrc(self.song)
        self.assertEqual(lyrics_tag[0], expected_lrc)

        # Verify detect_sync_state detects WORD sync
        self.assertEqual(detect_sync_state(flac_path), SyncState.WORD)

    def test_apply_embed_mp3(self) -> None:
        """embed=True writes USLT and TXXX:LYRICS frames to MP3 and saves as ID3v2.3."""
        mp3_path = str(self.dir_path / "song.mp3")
        _create_minimal_mp3(mp3_path)

        apply_lyrics(mp3_path, self.song, embed=True, write_sidecars=True)

        # Check sidecars
        lrc_path = self.dir_path / "song.lrc"
        ttml_path = self.dir_path / "song.ttml"
        self.assertTrue(lrc_path.is_file())
        self.assertTrue(ttml_path.is_file())
        self.assertEqual(lrc_path.read_text(encoding="utf-8"), to_enhanced_lrc(self.song))
        self.assertEqual(ttml_path.read_text(encoding="utf-8"), to_ttml(self.song))

        # Reload MP3 and verify ID3 frames
        reloaded = MP3(mp3_path)
        self.assertEqual(reloaded.tags.version, (2, 3, 0))

        # USLT check
        uslt_frames = reloaded.tags.getall("USLT")
        self.assertEqual(len(uslt_frames), 1)
        self.assertEqual(uslt_frames[0].text, to_plain_text(self.song))
        self.assertIn(uslt_frames[0].encoding, (1, 3, mutagen.id3.Encoding.UTF16, mutagen.id3.Encoding.UTF8))
        self.assertEqual(uslt_frames[0].lang, "eng")

        # TXXX:LYRICS check
        txxx_frame = reloaded.tags.get("TXXX:LYRICS")
        self.assertIsNotNone(txxx_frame)
        self.assertEqual(txxx_frame.text[0], to_enhanced_lrc(self.song))
        self.assertIn(txxx_frame.encoding, (1, 3, mutagen.id3.Encoding.UTF16, mutagen.id3.Encoding.UTF8))

        # Sync state detection
        self.assertEqual(detect_sync_state(mp3_path), SyncState.WORD)

    def test_apply_embed_mp4(self) -> None:
        """embed=True writes Enhanced LRC into MP4 \\xa9lyr atom."""
        mock_audio = MagicMock()
        mock_audio.tags = {}

        def mock_setitem(key, val):
            mock_audio.tags[key] = val

        mock_audio.__setitem__ = MagicMock(sideeffect=mock_setitem)
        mock_audio.get = MagicMock(sideeffect=lambda k: mock_audio.tags.get(k))

        track_m4a = str(self.dir_path / "song.m4a")
        pathlib.Path(track_m4a).touch()

        with patch("mutagen.File", return_value=mock_audio):
            apply_lyrics(track_m4a, self.song, embed=True, write_sidecars=False)

        mock_audio.__setitem__.assert_called_with("\xa9lyr", [to_enhanced_lrc(self.song)])
        mock_audio.save.assert_called_once()

    def test_apply_embed_missing_file_raises_error(self) -> None:
        """embed=True raises FileNotFoundError when the audio file is missing."""
        missing = str(self.dir_path / "nonexistent.mp3")
        with self.assertRaises(FileNotFoundError):
            apply_lyrics(missing, self.song, embed=True, write_sidecars=False)

    def test_purge_stale_line_synced_sidecars(self) -> None:
        """Purge stale line-synced sidecars (.ttml) when writing word-synced .lrc."""
        track_mp3 = str(self.dir_path / "track_stale.mp3")
        _create_minimal_mp3(track_mp3)

        # Existing stale line-synced files
        stale_lrc = self.dir_path / "track_stale.lrc"
        stale_lrc.write_text("[00:01.00] Line one\n[00:02.00] Line two\n", encoding="utf-8")
        stale_ttml = self.dir_path / "track_stale.ttml"
        stale_ttml.write_text(
            '<?xml version="1.0"?>\n<tt><body><div>\n'
            '  <p begin="00:00:01.000" end="00:00:02.000">Line one</p>\n'
            '</div></body></tt>',
            encoding="utf-8",
        )

        # Apply only write_lrc=True (so .ttml is NOT written and should be purged)
        config = TaggingConfig(
            write_lrc=True,
            lrc_extension="lrc",
            write_ttml=False,
            purge_stale_line_sync=True,
            backup_stale_sidecars=False,
            embed_metadata=False,
        )
        res = apply_lyrics(track_mp3, self.song, config=config)

        # Stale .ttml should have been deleted
        self.assertFalse(stale_ttml.exists())
        self.assertIn(str(stale_ttml), res["sidecars_purged"])

        # .lrc should have been updated with word sync
        self.assertTrue(stale_lrc.exists())
        self.assertEqual(stale_lrc.read_text(encoding="utf-8"), to_enhanced_lrc(self.song))

    def test_purge_stale_sidecars_with_backup(self) -> None:
        """Stale sidecars are renamed with .bak suffix when backup_stale_sidecars=True."""
        track_mp3 = str(self.dir_path / "track_bak.mp3")
        _create_minimal_mp3(track_mp3)

        stale_ttml = self.dir_path / "track_bak.ttml"
        stale_ttml.write_text(
            '<?xml version="1.0"?>\n<tt><body><div>\n'
            '  <p begin="00:00:01.000" end="00:00:02.000">Line one</p>\n'
            '</div></body></tt>',
            encoding="utf-8",
        )

        config = TaggingConfig(
            write_lrc=True,
            write_ttml=False,
            purge_stale_line_sync=True,
            backup_stale_sidecars=True,
            embed_metadata=False,
        )
        apply_lyrics(track_mp3, self.song, config=config)

        # Original .ttml deleted, .ttml.bak exists
        self.assertFalse(stale_ttml.exists())
        bak_file = self.dir_path / "track_bak.ttml.bak"
        self.assertTrue(bak_file.exists())

    def test_overwrite_existing_word_sync_guard(self) -> None:
        """Guard prevents overwriting existing word sync when overwrite_existing_word_sync=False."""
        track_flac = str(self.dir_path / "guarded.flac")
        _create_minimal_flac(track_flac)

        # First apply word sync
        apply_lyrics(track_flac, self.song, config=TaggingConfig(write_lrc=True, embed_metadata=True))
        self.assertEqual(detect_sync_state(track_flac), SyncState.WORD)

        # Second apply with overwrite_existing_word_sync=False
        new_song = SongLyrics(metadata={"ti": "Overwritten"}, lines=[])
        res = apply_lyrics(
            track_flac,
            new_song,
            config=TaggingConfig(overwrite_existing_word_sync=False, write_lrc=True, embed_metadata=True),
        )
        self.assertTrue(res["skipped"])
        self.assertEqual(res["reason"], "existing_word_sync")

        # Second apply with overwrite_existing_word_sync=True
        res_overwrite = apply_lyrics(
            track_flac,
            new_song,
            config=TaggingConfig(overwrite_existing_word_sync=True, write_lrc=True, embed_metadata=True),
        )
        self.assertFalse(res_overwrite["skipped"])

    def test_lrc_extension_modes(self) -> None:
        """Verify lrc_extension config option: 'elrc' and 'both'."""
        track_flac = str(self.dir_path / "ext_modes.flac")
        _create_minimal_flac(track_flac)

        # Mode 'elrc'
        config_elrc = TaggingConfig(write_lrc=True, lrc_extension="elrc", write_ttml=False, embed_metadata=False)
        apply_lyrics(track_flac, self.song, config=config_elrc)
        self.assertTrue((self.dir_path / "ext_modes.elrc").is_file())
        self.assertFalse((self.dir_path / "ext_modes.lrc").is_file())

        # Mode 'both'
        config_both = TaggingConfig(
            write_lrc=True,
            lrc_extension="both",
            write_ttml=False,
            embed_metadata=False,
            overwrite_existing_word_sync=True,
        )
        apply_lyrics(track_flac, self.song, config=config_both)
        self.assertTrue((self.dir_path / "ext_modes.elrc").is_file())
        self.assertTrue((self.dir_path / "ext_modes.lrc").is_file())

    def test_strip_lyrics_flac_mp3_and_sidecars(self) -> None:
        """strip_lyrics removes tags from FLAC/MP3 and removes sibling sidecars."""
        # Setup FLAC with lyrics and sidecars
        flac_path = str(self.dir_path / "strip_test.flac")
        _create_minimal_flac(flac_path)
        apply_lyrics(flac_path, self.song, config=TaggingConfig(write_lrc=True, lrc_extension="both", write_ttml=True, embed_metadata=True))
        self.assertEqual(detect_sync_state(flac_path), SyncState.WORD)
        self.assertTrue((self.dir_path / "strip_test.lrc").exists())
        self.assertTrue((self.dir_path / "strip_test.elrc").exists())
        self.assertTrue((self.dir_path / "strip_test.ttml").exists())

        # Strip FLAC
        strip_lyrics(flac_path)
        self.assertFalse((self.dir_path / "strip_test.lrc").exists())
        self.assertFalse((self.dir_path / "strip_test.elrc").exists())
        self.assertFalse((self.dir_path / "strip_test.ttml").exists())
        self.assertEqual(detect_sync_state(flac_path), SyncState.NONE)

        # Setup MP3 with lyrics
        mp3_path = str(self.dir_path / "strip_test.mp3")
        _create_minimal_mp3(mp3_path)
        apply_lyrics(mp3_path, self.song, config=TaggingConfig(write_lrc=True, write_ttml=True, embed_metadata=True))
        self.assertEqual(detect_sync_state(mp3_path), SyncState.WORD)

        # Strip MP3
        strip_lyrics(mp3_path)
        self.assertFalse((self.dir_path / "strip_test.lrc").exists())
        self.assertFalse((self.dir_path / "strip_test.ttml").exists())
        self.assertEqual(detect_sync_state(mp3_path), SyncState.NONE)

    def test_apply_write_lrc_false_writes_zero_lrc_or_elrc(self) -> None:
        """When write_lrc=False, zero .lrc or .elrc sidecars are created regardless of lrc_extension."""
        track = str(self.dir_path / "no_lrc.mp3")
        _create_minimal_mp3(track)
        config = TaggingConfig(
            write_lrc=False,
            lrc_extension="both",
            write_ttml=False,
            embed_metadata=False,
        )
        res = apply_lyrics(track, self.song, config=config)
        self.assertEqual(res["sidecars_written"], [])
        self.assertFalse((self.dir_path / "no_lrc.lrc").exists())
        self.assertFalse((self.dir_path / "no_lrc.elrc").exists())

    def test_consolidation_purges_lingering_elrc(self) -> None:
        """When write_lrc=True and lrc_extension='lrc', lingering .elrc is purged."""
        track = str(self.dir_path / "consolidate.mp3")
        _create_minimal_mp3(track)
        elrc_path = self.dir_path / "consolidate.elrc"
        # 1. Test lingering line-synced ELRC sidecar
        elrc_path.write_text("[00:01.00]Old line-synced ELRC\n[00:02.00]Second line", encoding="utf-8")
        self.assertTrue(elrc_path.is_file())

        config = TaggingConfig(
            write_lrc=True,
            lrc_extension="lrc",
            write_ttml=False,
            embed_metadata=False,
        )
        res = apply_lyrics(track, self.song, config=config)
        self.assertTrue((self.dir_path / "consolidate.lrc").exists())
        self.assertFalse(elrc_path.exists())
        self.assertIn(str(elrc_path), res["sidecars_purged"])

        # 2. Test lingering word-synced ELRC with overwrite_existing_word_sync=True
        elrc_path.write_text("[00:01.00]<00:01.00>Old <00:01.50>ELRC", encoding="utf-8")
        config_overwrite = TaggingConfig(
            write_lrc=True,
            lrc_extension="lrc",
            write_ttml=False,
            embed_metadata=False,
            overwrite_existing_word_sync=True,
        )
        res_overwrite = apply_lyrics(track, self.song, config=config_overwrite)
        self.assertFalse(elrc_path.exists())
        self.assertIn(str(elrc_path), res_overwrite["sidecars_purged"])

    def test_purge_stale_sidecars_lingering_ttml_when_write_ttml_false(self) -> None:
        """When write_ttml=False and purge_stale_line_sync=True, lingering .ttml is purged."""
        track = str(self.dir_path / "purge_ttml.mp3")
        _create_minimal_mp3(track)
        ttml_path = self.dir_path / "purge_ttml.ttml"
        ttml_path.write_text("<tt><body><p begin='00:01.000'>Lingering TTML</p></body></tt>", encoding="utf-8")
        self.assertTrue(ttml_path.is_file())

        config = TaggingConfig(
            write_lrc=True,
            lrc_extension="lrc",
            write_ttml=False,
            purge_stale_line_sync=True,
            embed_metadata=False,
        )
        res = apply_lyrics(track, self.song, config=config)
        self.assertFalse(ttml_path.exists())
        self.assertIn(str(ttml_path), res["sidecars_purged"])


class TestReadTrackLyrics(unittest.TestCase):
    """Tests for read_track_lyrics deserializing sidecars and embedded metadata tags."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = pathlib.Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_read_from_lrc_sidecar(self) -> None:
        track = str(self.dir_path / "song.mp3")
        _create_minimal_mp3(track)
        lrc_path = self.dir_path / "song.lrc"
        lrc_path.write_text("[00:01.00]First line\n[00:02.50]Second line\n", encoding="utf-8")

        lyrics = read_track_lyrics(track)
        self.assertIsNotNone(lyrics)
        self.assertEqual(len(lyrics.lines), 2)
        self.assertEqual(lyrics.lines[0].start_ms, 1000)
        self.assertFalse(lyrics.is_word_synced)

    def test_read_from_elrc_sidecar(self) -> None:
        track = str(self.dir_path / "song.mp3")
        _create_minimal_mp3(track)
        elrc_path = self.dir_path / "song.elrc"
        elrc_path.write_text("[00:01.00]<00:01.00>Hel<00:01.50>lo <00:02.00>World\n", encoding="utf-8")

        lyrics = read_track_lyrics(track)
        self.assertIsNotNone(lyrics)
        self.assertEqual(len(lyrics.lines), 1)
        self.assertEqual(len(lyrics.lines[0].syllables), 3)
        self.assertTrue(lyrics.is_word_synced)

    def test_read_from_flac_vorbis(self) -> None:
        track = str(self.dir_path / "song.flac")
        _create_minimal_flac(track)
        flac = FLAC(track)
        flac["LYRICS"] = ["[00:01.00]<00:01.00>Flac <00:01.50>Audio\n"]
        flac.save()

        lyrics = read_track_lyrics(track)
        self.assertIsNotNone(lyrics)
        self.assertEqual(len(lyrics.lines), 1)
        self.assertTrue(lyrics.is_word_synced)

    def test_read_from_mp3_txxx(self) -> None:
        track = str(self.dir_path / "song.mp3")
        _create_minimal_mp3(track)
        mp3 = MP3(track)
        if mp3.tags is None:
            mp3.add_tags()
        mp3.tags.add(TXXX(encoding=3, desc="LYRICS", text="[00:01.00]<00:01.00>MP3 <00:01.50>Lyrics\n"))
        mp3.save(v2_version=3)

        lyrics = read_track_lyrics(track)
        self.assertIsNotNone(lyrics)
        self.assertEqual(len(lyrics.lines), 1)
        self.assertTrue(lyrics.is_word_synced)

    def test_read_from_mp3_uslt(self) -> None:
        track = str(self.dir_path / "song.mp3")
        _create_minimal_mp3(track)
        mp3 = MP3(track)
        if mp3.tags is None:
            mp3.add_tags()
        mp3.tags.add(USLT(encoding=3, lang="eng", desc="", text="[00:01.00]Line In USLT\n[00:03.00]Second Line\n"))
        mp3.save(v2_version=3)

        lyrics = read_track_lyrics(track)
        self.assertIsNotNone(lyrics)
        self.assertEqual(len(lyrics.lines), 2)
        self.assertFalse(lyrics.is_word_synced)

    def test_read_returns_none_when_no_lyrics(self) -> None:
        track = str(self.dir_path / "empty.mp3")
        _create_minimal_mp3(track)
        self.assertIsNone(read_track_lyrics(track))


if __name__ == "__main__":
    unittest.main()
