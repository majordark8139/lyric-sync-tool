"""Soft candidate scoring matcher for comparing audio metadata with lyric search candidates."""

import difflib
from core.normalizer import clean_kugou_query, extract_primary_artist


def score_candidate(
    audio_artist: str,
    audio_title: str,
    audio_duration_ms: int,
    cand_artist: str,
    cand_title: str,
    cand_duration_ms: int,
) -> float:
    """Compute a composite soft match score (0 to 100) between audio track and candidate.

    Scoring criteria:
      1. Duration Score (0 to 40 pts):
         Clamped delta dt = |audio_duration_ms - cand_duration_ms|.
         Full 40 pts if dt <= 1500 ms; linear drop to 0 pts at dt = 4000 ms.
      2. Title Similarity (0 to 40 pts):
         difflib.SequenceMatcher ratio between cleaned titles * 40.
      3. Primary Artist Presence (0 to 20 pts):
         20 pts if extract_primary_artist(audio_artist).lower() appears in cand_artist.lower(),
         otherwise 0 pts.

    Args:
        audio_artist: Original artist tag of the audio file.
        audio_title: Original title tag of the audio file.
        audio_duration_ms: Audio file duration in milliseconds.
        cand_artist: Candidate artist name from search results.
        cand_title: Candidate title from search results.
        cand_duration_ms: Candidate duration in milliseconds.

    Returns:
        Composite score out of 100.0.
    """
    # ── 1. Duration Score (0 to 40 pts) ──
    if audio_duration_ms <= 0 or cand_duration_ms <= 0:
        duration_score = 0.0
    else:
        delta_ms = abs(audio_duration_ms - cand_duration_ms)
        if delta_ms <= 1500:
            duration_score = 40.0
        elif delta_ms >= 4000:
            duration_score = 0.0
        else:
            # Linear decay from 1500 ms (40 pts) to 4000 ms (0 pts)
            duration_score = 40.0 * ((4000 - delta_ms) / 2500.0)

    duration_score = max(0.0, min(40.0, duration_score))

    # ── 2. Title Similarity (0 to 40 pts) ──
    clean_audio = clean_kugou_query(audio_title).lower() or audio_title.strip().lower()
    clean_cand = clean_kugou_query(cand_title).lower() or cand_title.strip().lower()

    if clean_audio and clean_cand:
        ratio = difflib.SequenceMatcher(None, clean_audio, clean_cand).ratio()
        title_score = ratio * 40.0
    elif not clean_audio and not clean_cand:
        title_score = 40.0
    else:
        title_score = 0.0

    title_score = max(0.0, min(40.0, title_score))

    # ── 3. Primary Artist Presence (0 to 20 pts) ──
    primary_artist = extract_primary_artist(audio_artist).strip().lower()
    cand_artist_lower = cand_artist.strip().lower()

    if primary_artist and primary_artist in cand_artist_lower:
        artist_score = 20.0
    else:
        artist_score = 0.0

    total_score = duration_score + title_score + artist_score
    return float(max(0.0, min(100.0, total_score)))
