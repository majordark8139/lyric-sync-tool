"""Soft candidate scoring matcher for comparing audio metadata with lyric search candidates."""

import difflib
import re
from core.normalizer import clean_kugou_query, extract_primary_artist

_FEATURE_DELIM_RE = re.compile(
    r"(?i)\b(?:feat\.?|ft\.?|featuring|with)\b|[&/,;+]"
)


def _compute_artist_similarity(audio_artist: str, cand_artist: str) -> float:
    """Compute artist similarity ratio between 0.0 and 1.0.

    Accounts for featured artists, collaborations, delimiter normalization,
    token sets, and containment.
    """
    raw_audio = audio_artist.strip().lower()
    raw_cand = cand_artist.strip().lower()

    if not raw_audio and not raw_cand:
        return 1.0
    if not raw_audio or not raw_cand:
        return 0.0

    if raw_audio == raw_cand:
        return 1.0

    is_contained = False

    # Check direct containment
    if raw_audio in raw_cand or raw_cand in raw_audio:
        is_contained = True

    # Check primary artist containment
    primary_audio = extract_primary_artist(audio_artist).strip().lower()
    primary_cand = extract_primary_artist(cand_artist).strip().lower()

    if primary_audio and (primary_audio in raw_cand or primary_audio in primary_cand):
        is_contained = True
    if primary_cand and (primary_cand in raw_audio or primary_cand in primary_audio):
        is_contained = True

    # Normalize delimiters: replace feat., ft., &, /, and , with spaces
    norm_audio = _FEATURE_DELIM_RE.sub(" ", raw_audio)
    norm_cand = _FEATURE_DELIM_RE.sub(" ", raw_cand)
    norm_audio = " ".join(norm_audio.split())
    norm_cand = " ".join(norm_cand.split())

    if norm_audio and norm_cand:
        if norm_audio == norm_cand:
            return 1.0
        if norm_audio in norm_cand or norm_cand in norm_audio:
            is_contained = True

    # Token set ratio / token overlap
    tokens_audio = set(norm_audio.split())
    tokens_cand = set(norm_cand.split())

    ratio = 0.0
    if tokens_audio and tokens_cand:
        inter = tokens_audio.intersection(tokens_cand)
        if inter:
            if tokens_audio.issubset(tokens_cand) or tokens_cand.issubset(tokens_audio):
                is_contained = True

            sorted_inter = " ".join(sorted(inter))
            sorted_audio = " ".join(sorted(tokens_audio))
            sorted_cand = " ".join(sorted(tokens_cand))

            r1 = difflib.SequenceMatcher(None, sorted_inter, sorted_audio).ratio()
            r2 = difflib.SequenceMatcher(None, sorted_inter, sorted_cand).ratio()
            r3 = difflib.SequenceMatcher(None, sorted_audio, sorted_cand).ratio()
            ratio = max(r1, r2, r3)

            token_overlap = (2.0 * len(inter)) / (len(tokens_audio) + len(tokens_cand))
            ratio = max(ratio, token_overlap)
    else:
        ratio = difflib.SequenceMatcher(None, norm_audio, norm_cand).ratio()

    # If either artist string contains the other, set artist similarity to at least 0.90 (90%)
    if is_contained:
        ratio = max(ratio, 0.90)

    return float(max(0.0, min(1.0, ratio)))


def compute_title_similarity(audio_title: str, cand_title: str) -> float:
    """Compute normalized title similarity between audio track title and candidate title."""
    clean_audio = clean_kugou_query(audio_title).lower() or audio_title.strip().lower()
    clean_cand = clean_kugou_query(cand_title).lower() or cand_title.strip().lower()

    if clean_audio and clean_cand:
        return float(difflib.SequenceMatcher(None, clean_audio, clean_cand).ratio())
    elif not clean_audio and not clean_cand:
        return 1.0
    return 0.0


def score_candidate(
    audio_artist: str,
    audio_title: str,
    audio_duration_ms: int = 0,
    cand_artist: str = "",
    cand_title: str = "",
    cand_duration_ms: int = 0,
    **kwargs,
) -> float:
    """Compute a composite soft match score (0 to 100) between audio track and candidate.

    Scoring criteria:
      1. Duration Score (0 to 40 pts):
         Clamped delta dt = |audio_duration_ms - cand_duration_ms|.
         Full 40 pts if dt <= 1500 ms; linear drop to 0 pts at dt = 4000 ms.
      2. Title Similarity (0 to 40 pts):
         Hard rejection floor: if compute_title_similarity < 0.60, returns 0.0 immediately.
         Otherwise title_similarity * 40 pts.
      3. Primary Artist Presence & Collaboration Similarity (0 to 20 pts):
         _compute_artist_similarity * 20 pts.

    Args:
        audio_artist: Original artist tag of the audio file.
        audio_title: Original title tag of the audio file.
        audio_duration_ms: Audio file duration in milliseconds.
        cand_artist: Candidate artist name from search results.
        cand_title: Candidate title from search results.
        cand_duration_ms: Candidate duration in milliseconds.
        **kwargs: Optional alias arguments (audio_dur_ms, cand_dur_ms).

    Returns:
        Composite score out of 100.0 (or 0.0 if failing title floor).
    """
    if "audio_dur_ms" in kwargs and not audio_duration_ms:
        audio_duration_ms = kwargs["audio_dur_ms"]
    if "cand_dur_ms" in kwargs and not cand_duration_ms:
        cand_duration_ms = kwargs["cand_dur_ms"]

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

    # ── 2. Title Similarity & Hard Floor (0 to 40 pts) ──
    title_sim = compute_title_similarity(audio_title, cand_title)
    if title_sim < 0.60:
        return 0.0

    title_score = max(0.0, min(40.0, title_sim * 40.0))

    # ── 3. Artist Similarity (0 to 20 pts) ──
    artist_ratio = _compute_artist_similarity(audio_artist, cand_artist)
    audio_album_artist = kwargs.get("album_artist") or kwargs.get("audio_album_artist") or ""
    if audio_album_artist and audio_album_artist.strip().lower() != audio_artist.strip().lower():
        collab_ratio = _compute_artist_similarity(f"{audio_artist} & {audio_album_artist}", cand_artist)
        artist_ratio = max(artist_ratio, collab_ratio)
    artist_score = artist_ratio * 20.0

    total_score = duration_score + title_score + artist_score
    return float(max(0.0, min(100.0, total_score)))
