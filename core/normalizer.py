"""Metadata normalizer and Cascading Query Ladder generator."""

import re
from typing import List, Tuple


def normalize_punctuation(text: str) -> str:
    """Normalize curly quotes, full-width brackets, long dashes, and whitespace.

    Args:
        text: Input string.

    Returns:
        Normalized string with standard ASCII punctuation and single spaces.
    """
    if not text:
        return ""

    # Normalize curly/slant single quotes and grave accents
    text = re.sub(r"[\u2018\u2019\u201A\u201B\u00B4\`\']", "'", text)
    # Normalize curly/low double quotes
    text = re.sub(r"[\u201C\u201D\u201E\u201F\"]", '"', text)

    # Full-width brackets and long dashes table
    trans_table = str.maketrans({
        "（": "(", "）": ")",
        "［": "[", "］": "]",
        "【": "[", "】": "]",
        "｛": "{", "｝": "}",
        "《": "<", "》": ">",
        "—": "-", "–": "-", "―": "-", "−": "-", "～": "-",
        "\u3000": " ", "\u00A0": " ",
    })
    text = text.translate(trans_table)
    # Collapse multiple whitespace characters into a single space
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def strip_features(title: str) -> str:
    """Strip featured artist annotations from track title while protecting remixes and edits.

    Preserves annotations such as (Remix), (VIP), (Acoustic), (Live), (Club Mix).

    Args:
        title: Song title string.

    Returns:
        Title with feature patterns removed.
    """
    if not title:
        return ""

    # Match (feat. X), [feat. X], (with Y), [with Y], (ft. Z), etc.
    feature_bracket_re = re.compile(
        r"\s*[\(\[](?:(?:\b(?:feat|ft)\.?)|(?:\b(?:with|featuring)\b))\s*[^()\[\]]+[\)\]]",
        re.IGNORECASE,
    )
    result = feature_bracket_re.sub("", title)

    # Match trailing - feat. X / - with Y
    feature_dash_re = re.compile(
        r"\s+[-–—]\s*(?:(?:\b(?:feat|ft)\.?)|(?:\b(?:with|featuring)\b))\s*.*$",
        re.IGNORECASE,
    )
    result = feature_dash_re.sub("", result)

    result = re.sub(r"\s+", " ", result)
    return result.strip()


def strip_reissue_and_bonus(title: str) -> str:
    """Strip release bloat such as bonus tracks, remasters, deluxe editions, clean/explicit tags.

    Leaves valid mix types such as (Remix), (VIP), (Acoustic), (Live) intact.

    Args:
        title: Song title string.

    Returns:
        Title without release bloat.
    """
    if not title:
        return ""

    patterns = [
        # Dash-separated: - Bonus Track, - 2020 Remaster, - Deluxe Edition, - Single Version
        r"\s*[-–—]\s*(?:\d{4}\s+)?(?:Bonus(?:\s+Track)?|Remaster(?:ed)?|Deluxe(?:\s+Edition)?|Single\s+Version|Album\s+Version)\b[^-–—()\[\]]*",
        # Bracketed: (Bonus Track), [2020 Remaster], (Deluxe Edition), (Remastered), [Clean], (Explicit)
        r"\s*[\(\[](?:(?:\d{4}\s+)?(?:Bonus(?:\s+Track)?|Remaster(?:ed)?|Deluxe(?:\s+Edition)?|Single\s+Version|Album\s+Version)|Clean|Explicit)\b[^()\[\]]*[\)\]]",
    ]

    result = title
    for pat in patterns:
        result = re.sub(pat, "", result, flags=re.IGNORECASE)

    result = re.sub(r"\s+", " ", result)
    return result.strip()


def extract_collaborators(artist: str, title: str) -> Tuple[str, List[str]]:
    """Extract lead artist and featured collaborators from artist string and title tags.

    Args:
        artist: Artist name metadata.
        title: Song title metadata.

    Returns:
        A tuple of (primary_lead_artist, [featured_artist_1, ...]).
    """
    featured: List[str] = []

    # 1. Extract from title tags: (feat. X), (with Y), [ft. Z]
    title_pattern = re.compile(
        r"[\(\[](?:(?:\b(?:feat|ft)\.?)|(?:\b(?:with|featuring)\b))\s*([^()\[\]]+)[\)\]]",
        re.IGNORECASE,
    )
    for match in title_pattern.finditer(title):
        raw_collaborators = match.group(1).strip()
        parts = re.split(r"\s*(?:,|;|&|\band\b)\s*", raw_collaborators, flags=re.IGNORECASE)
        for part in parts:
            clean = part.strip()
            if clean and clean not in featured:
                featured.append(clean)

    # 2. Extract from artist string
    # Delimiters: comma, semicolon, ampersand, slash, vs, feat, ft, with, x
    artist_delim = re.compile(
        r"\s*(?:,|;|&|/|\b(?:feat|ft|vs)\.?|\bwith\b|\bfeaturing\b|\s+[xX]\s+)\s*",
        re.IGNORECASE,
    )
    artist_parts = [p.strip() for p in artist_delim.split(artist) if p.strip()]

    lead_artist = artist_parts[0] if artist_parts else artist.strip()

    for extra in artist_parts[1:]:
        if extra and extra not in featured and extra.lower() != lead_artist.lower():
            featured.append(extra)

    return lead_artist, featured


def sanitize_search_query(text: str) -> str:
    """Replace punctuation characters (), [], {}, -, _, /, \\ with single spaces, collapse spaces, and trim ends.

    Args:
        text: Input query string.

    Returns:
        Punctuation-sanitized query string.
    """
    if not text:
        return ""
    result = re.sub(r"[()[\]{}\-_/\\]", " ", text)
    result = re.sub(r"\s+", " ", result)
    return result.strip()


def generate_query_ladder(artist: str, title: str) -> List[Tuple[str, str]]:
    """Generate cascading query candidates in prioritized order with deduplication.

    Prioritized query order:
      1. Exact raw tags: (artist, title)
      2. Punctuation-sanitized full title: (sanitize_search_query(artist), sanitize_search_query(title))
      3. Feature-stripped: (artist, strip_features(title))
      4. Sanitized feature-stripped: (sanitize_search_query(artist), sanitize_search_query(strip_features(title)))
      5. Collaborators & Reissue variants (preserves collaborator search and bonus tracks)
      6. Title-only full sanitized: ("", sanitize_search_query(title))
      7. Title-only feature-stripped: ("", sanitize_search_query(strip_features(title)))

    Args:
        artist: Artist string.
        title: Title string.

    Returns:
        List of (query_artist, query_title) candidate tuples.
    """
    ladder: List[Tuple[str, str]] = []

    def _add(a: str, t: str) -> None:
        cand = (a.strip(), t.strip())
        if cand[1] and cand not in ladder:
            ladder.append(cand)

    # 1. Exact raw tags
    _add(artist, title)

    # 2. Punctuation-sanitized full title
    _add(sanitize_search_query(artist), sanitize_search_query(title))

    # Feature stripping and collaborators
    feat_stripped_title = strip_features(title)
    primary_artist, featured_artists = extract_collaborators(artist, title)

    # 3. Feature-stripped
    _add(artist, feat_stripped_title)
    if primary_artist and primary_artist != artist:
        _add(primary_artist, feat_stripped_title)

    # 4. Sanitized feature-stripped
    _add(sanitize_search_query(artist), sanitize_search_query(feat_stripped_title))
    if primary_artist and primary_artist != artist:
        _add(sanitize_search_query(primary_artist), sanitize_search_query(feat_stripped_title))

    # Collaborators in artist & reissue stripped
    for feat in featured_artists:
        _add(f"{primary_artist} {feat}", feat_stripped_title)
    clean_title = strip_reissue_and_bonus(feat_stripped_title)
    if primary_artist:
        _add(primary_artist, clean_title)

    # 5. Title-only full sanitized
    _add("", sanitize_search_query(title))

    # 6. Title-only feature-stripped
    _add("", sanitize_search_query(feat_stripped_title))
    if clean_title != feat_stripped_title:
        _add("", sanitize_search_query(clean_title))

    return ladder
