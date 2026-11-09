"""Existing SoundCloud discovery for the optional personal module.

Not cleared for public distribution. Packaging does not waive service terms.
Returns candidates/results only; core owns persistence and worker lifetimes.
"""

from __future__ import annotations

import difflib
import importlib
import logging
import re
import unicodedata
import urllib.request
from functools import lru_cache
from typing import cast

from soundbox.art_sources import excluded_source
from soundbox.metadata import editor_credits
from . import operations
from .operations import ScopedResponse, bounded_operation, checkpoint, read_response, request_timeout

operation_scope = operations.operation_scope


# Compatibility re-exports; local storage has no discovery dependency.
from soundbox import artwork

ArtCache = artwork.ArtCache
ArtResult = artwork.ArtResult

LOG = logging.getLogger("soundbox.artfetch")

MIN_CONFIDENCE = 0.80      # title-match floor; wrong art is worse than no art
_UPLOADER_MATCH = 0.7      # uploader name ~ artist => it really is them (avatar path)
_UPLOADER_SAME = 0.90      # uploader IS the artist's own account => trust their upload
# Collab separators — "DENNETT x MACKAI", "A & B", "FLY VS HORSE". Split so we can try
# each artist alone (one of them posted it) for both the cover and the avatar floor.
_ARTIST_SPLIT = re.compile(
    r"\s+(?:x|&|vs\.?|\+|feat\.?|ft\.?|w/)\s+|\s*,\s*", re.I)
_MAX_IMG = 6 * 1024 * 1024  # 6 MB safety cap
# A browser-like UA — SoundCloud's HTML pages (used for the avatar og:image) and
# the image CDN both serve fine with this; a bare bot UA can get 403'd.
_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Soundbox/1.0"}
_EXCLUDED_ACCOUNTS: frozenset[str] = frozenset()


def prepare_runtime() -> None:
    """Prepare admitted search code at startup without requests or credentials."""
    for name in ('yt_dlp', 'yt_dlp.networking.common', 'yt_dlp.extractor.soundcloud'):
        importlib.import_module(name)


def configure_source_exclusions(accounts) -> None:
    """Core supplies immutable local preferences; discovery never writes them."""
    global _EXCLUDED_ACCOUNTS
    _EXCLUDED_ACCOUNTS = frozenset(accounts)


def _excluded(entry: dict) -> bool:
    return excluded_source(entry.get("uploader") or "",
                           entry.get("webpage_url") or entry.get("url") or "",
                           uploader_url=entry.get("uploader_url") or "",
                           excluded_accounts=_EXCLUDED_ACCOUNTS)


def _version_artists(title: str) -> list[str]:
    return list(dict.fromkeys(name for names, _ in editor_credits(title)
                              for name in _split_artists(names)))


def _publisher_rank(artist: str, title: str, uploader: str, score: float) -> int:
    if score < MIN_CONFIDENCE:
        return 0
    if any(_ratio(name, uploader) >= _UPLOADER_SAME for name in _version_artists(title)):
        return 2
    return int(any(_ratio(name, uploader) >= _UPLOADER_SAME
                   for name in dict.fromkeys([artist, *_split_artists(artist)])))


def _artist_upload(artist: str, uploader: str, score: float, title: str = "") -> bool:
    return _publisher_rank(artist, title, uploader, score) > 0


# Version / format / annotation junk DJs add to working files (v7, FINAL, [140 BPM],
# "(1)", wav…). Stripped from the SEARCH QUERY ONLY — never from the displayed or
# exported title. Makes the fuzzy title match land the real cover instead of falling
# back to the artist avatar.
_JUNK_BPMKEY = re.compile(r"[\[(][^\[\]()]*\bbpm\b[^\[\]()]*[\])]", re.I)  # [140 BPM _ D# MINOR]
# A whole bracket group whose ENTIRE content is a version/promo marker (never part of a
# real title — anchoring on the full content protects "(FREE BIRD)", "(DENNETT REMIX)",
# "(SAKURO VIP)", etc.). Optional leading "<n>k" catches download-count promos like
# "(4K FREE DL)". Also swallows a bare bracketed number: "(1)", "(140)".
_JUNK_BRACKET = re.compile(
    r"[\[(]\s*(?:\d+k?\s+)?(?:"
    r"final(?:\s*v?\d+)?|master(?:ed)?|remaster(?:ed)?|full|"
    r"free\s*(?:d\s*/?\s*l|download)?|d\s*/?\s*l|download|out\s*now|buy\s*now|"
    r"original\s*mix|extended\s*mix|radio\s*(?:edit|mix)|"
    r"playable|snippet|preview|teaser|plate|dub\s*plate|wip|draft|rough|\d+)"
    r"\s*[\])]", re.I)
# Promo phrases safe to strip ANYWHERE (also appear un-bracketed: "TRACK -FREE DOWNLOAD-").
_JUNK_PHRASES = re.compile(
    r"\b(?:free\s*(?:download|dl)|out\s*now|buy\s*now|original\s*mix|extended\s*mix|"
    r"radio\s*(?:edit|mix))\b", re.I)
_JUNK_VER = re.compile(r"\bv\.?\d+(?:\.\d+)?\b", re.I)                     # v1, v.7, v2.3
# Single words safe to strip ANYWHERE (bare, not just bracketed). master\d* catches
# "MASTER694"-style mixdown stamps (measured from real-library curation edits).
_JUNK_TERMS = re.compile(
    r"\b(?:final|master(?:ed)?\d*|remaster(?:ed)?|wip|draft|rough|playable|"
    r"wav|flac|mp3|aiff?|alac|lossless|320|256|192|128|24 ?bit|16 ?bit)\b", re.I)
_JUNK_EMPTY_BRACK = re.compile(r"[\[(]\s*[\])]")    # leftover empty () []
# Measured from real-library manual title fixes:
_JUNK_DUR = re.compile(r"^\s*\(\d+\.\d+\)")         # leading "(3.40)" duration stamp
_JUNK_DATE = re.compile(r"\(?\b\d{1,2}-\d{1,2}-\d{2,4}\b\)?")  # "(03-25-25)" WIP stamps
# NOTE: the library owner's OWN edit tags (e.g. "(myname edit)") are deliberately
# NOT stripped for search — a coverless own-edit is the owner's visual cue that a
# track is collection-cleanup bait. A user decision; don't "fix" it.
_JUNK_KEYTOK = re.compile(r"\b\d{1,2}[AB]\b", re.I)  # bare Camelot tokens: 4A, 10a
_JUNK_BPMTOK = re.compile(r"\b1\d{2}\b")            # bare 3-digit BPM: 140, 174
_JUNK_YEAR = re.compile(r"\b(?:19|20)\d{2}\s*$")    # trailing year: "... 2021"


def _clean_search_title(title: str) -> str:
    # Cache immutable text only; retain the original fallback/error behavior
    # for non-string inputs, including falsy unhashable values.
    return _clean_search_text(title) if type(title) is str else _clean_search_text.__wrapped__(title)


@lru_cache(maxsize=8192)
def _clean_search_text(title: str) -> str:
    """Strip DJ version/format/promo junk so the fuzzy match sees the real track name,
    landing the real cover instead of an artist-avatar fallback.

    Whole-bracket junk is anchored on the FULL bracket content, so real tags like
    "(DENNETT REMIX)" / "(SAKURO VIP)" survive; only bare version/format words are
    stripped anywhere. Conservative: leaves bare trailing numbers ('Area 51') alone.
    Falls back to the original if it empties out."""
    s = title or ""
    s = s.replace("_", " ")     # "ABYSS_-_FEINT" / "beanie_god" search terribly raw
    s = _JUNK_DUR.sub(" ", s)
    s = _JUNK_DATE.sub(" ", s)
    s = _JUNK_BPMKEY.sub(" ", s)
    s = _JUNK_BRACKET.sub(" ", s)
    s = _JUNK_PHRASES.sub(" ", s)
    s = _JUNK_VER.sub(" ", s)
    s = _JUNK_TERMS.sub(" ", s)
    s = _JUNK_KEYTOK.sub(" ", s)
    s = _JUNK_BPMTOK.sub(" ", s)
    s = _JUNK_YEAR.sub(" ", s)
    s = _JUNK_EMPTY_BRACK.sub(" ", s)
    s = re.sub(r"\s{2,}", " ", s).strip(" -_([")
    return s or (title or "").strip()


def _norm(s: str) -> str:
    return _normalized_text(s) if type(s) is str else _normalized_text.__wrapped__(s)


@lru_cache(maxsize=8192)
def _normalized_text(s: str) -> str:
    # SoundCloud can return decomposed accents (Ade\u0301la) while local tags
    # use precomposed ADÉLA. Normalize identities before accountability checks.
    return "".join(ch for ch in unicodedata.normalize("NFKD", s or "").casefold()
                   if ch.isalnum())


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def _core_title(clean: str) -> str:
    """The bare song name: drop bracket groups (VIP/remix/flip) and a leading
    'ARTIST - ' prefix. 'SAKURO - CORROSION' -> 'CORROSION'; 'GNARLY (DENNETT REMIX)'
    -> 'GNARLY'. Used to match an artist's own upload that omits the prefix."""
    s = _unbracketed_title(clean)
    s = re.sub(r"^\s*[^-]{1,30}\s+-\s+", "", s)
    return re.sub(r"\s{2,}", " ", s).strip() or (clean or "")


def _unbracketed_title(clean: str) -> str:
    return re.sub(r"\s+", " ", re.sub(
        r"[\[(][^\[\]()]*[\])]", " ", clean or "")).strip(" -")


def _core_title_for_artist(clean: str, artist: str) -> str:
    """Return the song side of ``ARTIST - TITLE`` *or* ``TITLE - ARTIST``.

    Working-file names use both orientations.  The older unconditional
    left-side drop turned ``CRASH TEST - WODD`` into the bogus core ``WODD``;
    because WODD was also the uploader, any WODD upload could then receive the
    own-account 0.90 rescue.  Use the effective artist to orient the split and
    retain the conservative legacy fallback when neither edge is identifiable.
    """
    # A remixer inside brackets is not the artist edge of a hyphenated title.
    # Otherwise "Huey - Song (SAINT MILLER REMIX)" yields the bogus core "Huey".
    parts = re.split(r"\s+-\s*|(?<=\w)-\s+", _unbracketed_title(clean), maxsplit=1)
    if len(parts) == 2:
        left, right = (part.strip() for part in parts)
        na = _norm(artist)
        if na:
            left_norm, right_norm = _norm(left), _norm(right)
            if na in left_norm and na not in right_norm:
                return right or _core_title(clean)
            if na in right_norm and na not in left_norm:
                return left or _core_title(clean)
        # Loose "Huey- Song (EDITOR REMIX)" spelling is common in remix
        # filenames. Without explicit edit credits, preserve a potentially
        # meaningful hyphenated song name rather than stripping it blindly.
        if len(left) <= 30 and _edit_signature(clean):
            return right or _core_title(clean)
    return _core_title(clean)


def _split_artists(artist: str) -> list[str]:
    """Individual artists in a collab string ('DENNETT x MACKAI' -> [DENNETT, MACKAI]).
    A single artist yields just itself. De-dupes; drops stopword fragments."""
    if not artist:
        return []
    out, seen = [], set()
    for p in _ARTIST_SPLIT.split(artist):
        p = p.strip(" -_")
        k = _norm(p)
        if len(p) >= 2 and k not in seen and k not in ("the", "feat", "ft"):
            seen.add(k); out.append(p)
    return out


def _edit_signature(title: str) -> tuple:
    """Keep named versions distinct while allowing reordered collab credits."""
    return tuple(sorted((tuple(sorted(_norm(p) for p in _split_artists(names))),
                         {"edition": "edit", "rmx": "remix"}.get(kind.casefold(), kind.casefold()))
                        for names, kind in editor_credits(title)))


def _credited_title_score(clean: str, cand: str, artist: str) -> float:
    """Compare reordered working names only with identical explicit edit credits.

    Both full and prefix-free forms are needed for "EDITOR - SONG Original Artist
    (EDITOR Remix)" versus "Original Artist - SONG (EDITOR Remix)". A separate
    song-name check stops long matching credits from hiding a different song.
    """
    signature = _edit_signature(clean)
    if not signature or signature != _edit_signature(cand):
        return 0.0
    core = _core_title_for_artist(clean, artist)
    other = _core_title_for_artist(cand, artist)
    nc, no = _norm(core), _norm(other)
    if _ratio(core, other) < 0.90:
        # Only tolerate an extra song suffix when the other title explicitly
        # identifies that suffix as its original artist (including minor typos).
        def has_original_suffix(song: str, shorter: str, title: str) -> bool:
            parts = re.split(r"\s+-\s*|(?<=\w)-\s+", _unbracketed_title(title), maxsplit=1)
            return (len(parts) == 2 and len(shorter) >= 6 and song.startswith(shorter)
                    and _ratio(song[len(shorter):], parts[0]) >= 0.90)

        if not (has_original_suffix(nc, no, cand) or has_original_suffix(no, nc, clean)):
            return 0.0

    def forms(title: str, song: str) -> list[str]:
        brackets = " ".join(re.findall(r"[\[(][^\[\]()]*[\])]", title))
        return [" ".join(sorted(re.findall(r"[^\W_]+", s.casefold())))
                for s in (title, f"{song} {brackets}")]

    return max(_ratio(a, b) for a in forms(clean, core) for b in forms(cand, other))


def _artist_hint(name: str, uploader: str) -> bool:
    """Human-review evidence for branded/alternate accounts, not verification."""
    if _ratio(name, uploader) >= _UPLOADER_SAME:
        return True
    key = _norm(name)
    words = [_norm(word) for word in re.findall(r"[^\W_]+", uploader)]
    # Word boundaries matter: Mport's Xports contains Mport, Randomport does not.
    return len(key) >= 4 and key in words


def _song_words(title: str) -> tuple[str, ...]:
    words = []
    for word in re.findall(r"[^\W_]+", unicodedata.normalize("NFKD", title).casefold()):
        word = _norm(word)
        if word in {"n", "and", "the", "a", "s"}:
            continue
        if len(word) >= 6 and word.endswith("ing"):
            word = word[:-3]
        elif len(word) >= 5 and word.endswith("in"):
            word = word[:-2]
        words.append(word)
    return tuple(words)


def _review_score(clean: str, core: str, artist: str, entry: dict) -> tuple[float, str]:
    """Broader visible possibilities require a publisher/credit anchor and song evidence.

    These never enter unattended fetching. Explicit different version credits
    remain a hard boundary even when title/uploader similarity is otherwise high.
    """
    title, uploader = entry.get("title") or "", entry.get("uploader") or ""
    score = _match_score(clean, core, artist, title, uploader, entry.get("artists"), allow_excerpt=True)
    if score >= MIN_CONFIDENCE:
        return score, ""
    ours, theirs = _edit_signature(clean), _edit_signature(_clean_search_title(title))
    if (theirs and ours != theirs or
            bool(re.search(r"\bvip\b", clean, re.I)) != bool(re.search(r"\bvip\b", title, re.I))):
        return 0.0, ""
    anchors = _version_artists(clean) if ours else list(dict.fromkeys([artist, *_split_artists(artist)]))
    if not any(_artist_hint(name, uploader) for name in anchors):
        return score, ""
    candidate_core = _core_title_for_artist(_clean_search_title(title), artist)
    a, b = _song_words(core), _song_words(candidate_core)
    similarity = _ratio(" ".join(a), " ".join(b))
    if a and b and similarity >= .90 and len(_norm(" ".join(a))) >= 3:
        return max(score, .72), "Possible match: artist name appears in uploader; title wording or account differs"
    return score, ""


def _search_queries(artist: str, title: str) -> tuple[str, str, list[str]]:
    clean = _clean_search_title(title)
    core = _core_title_for_artist(clean, artist)
    q1 = clean if _norm(artist) and _norm(artist) in _norm(clean) else f"{artist} {clean}"
    queries = [q1.strip()]
    for name in [*reversed(_version_artists(clean)), artist]:
        query = f"{name} {core}".strip()
        if query and all(_norm(query) != _norm(q) for q in queries):
            queries.append(query)
    # Colloquial two-word titles often interchange Wind/Grind and Windin/Grindin.
    # Expand only that shape, and only issue the extra query if earlier results
    # do not already contain a strong artist-owned match.
    words = re.findall(r"[^\W_]+", core)
    main = [word for word in words if word.casefold() not in {"n", "and", "s"}]
    if len(main) == 2 and any(word.casefold() in {"n", "and"} for word in words):
        def variant(word):
            if word.casefold().endswith("ing"):
                return word[:-1]
            if word.casefold().endswith("in"):
                return word[:-2]
            return word + "in"
        query = f"{artist} {variant(main[0])} n {variant(main[1])}"
        if all(_norm(query) != _norm(q) for q in queries):
            queries.append(query)
    return clean, core, queries[:4]


def _match_score(clean: str, core: str, artist: str, cand_title: str, uploader: str,
                 credited_artists: list[str] | None = None, *, allow_excerpt: bool = False) -> float:
    """Confidence that a search result IS our track — accuracy-first (wrong art is worse
    than none). Two ways to earn it:
      1. Uploader IS the artist's own account AND the core song name is in the candidate
         → trust it even at a low full ratio (our title carried an 'ARTIST -' prefix the
         upload lacks). This is the 'CORROSION [6K FREEBIE] by Sakuro' case.
      2. Otherwise a strong full (cleaned-both) title match, but ONLY if the artist is
         accounted for (uploader ~ artist, or the artist name is genuinely in the title).
         The accountability gate stops a different artist's same-named track ('Gas Leak')
         from being grabbed. A low-ratio megamix that merely lists the artist in a
         tracklist is rejected by the ratio floor, not accepted."""
    if excluded_source(uploader):
        return 0.0
    excerpt = r"\b(?:clip|snippet|preview|teaser)\b"
    if not allow_excerpt and re.search(excerpt, cand_title, re.I) and not re.search(excerpt, clean, re.I):
        return 0.0  # an excerpt is not a complete recording
    if allow_excerpt:
        cand_title = re.sub(r"[\[(]\s*(?:clip|snippet|preview|teaser)\s*[\])]", "", cand_title, flags=re.I)
    cand = _clean_search_title(cand_title)
    ours, theirs = _edit_signature(clean), _edit_signature(cand)
    version_owner = max((_ratio(name, uploader) for name in _version_artists(clean)), default=0.0)
    if ((ours != theirs and not (len(ours) == 1 and not theirs and version_owner >= _UPLOADER_SAME))
            or bool(re.search(r"\bvip\b", clean, re.I))
            != bool(re.search(r"\bvip\b", cand, re.I))):
        return 0.0
    t = _ratio(clean, cand)
    u = max([_ratio(artist, uploader), version_owner,
             *(_ratio(name, uploader) for name in _split_artists(artist))])
    nc = _norm(core)
    other_core = _core_title_for_artist(cand, artist)
    # Label uploads often have only the song name in the title. SoundCloud's
    # structured artist credit supplies accountability; arbitrary descriptions
    # and tracklists never do. Require the complete song core, not containment.
    expected = {_norm(p) for p in _split_artists(artist)}
    credited = {_norm(p) for name in (credited_artists or []) for p in _split_artists(name)}
    if expected and expected <= credited and len(nc) >= 4 and nc == _norm(other_core):
        return max(t, 0.95)
    if u >= _UPLOADER_SAME and ours and theirs:
        credit_score = _credited_title_score(clean, cand, artist)
        if credit_score >= 0.95:
            return max(t, 0.9)
        if not credit_score:
            return 0.0
    if u >= _UPLOADER_SAME and len(nc) >= 3 and nc == _norm(other_core):
        return max(t, 0.9)
    na = _norm(artist)
    accountable = u >= 0.6 or (len(na) >= 3 and na in _norm(cand_title))
    return t if accountable else 0.0


def _best_thumb(info: dict) -> str | None:
    thumbs = info.get("thumbnails") or []
    if thumbs:
        best = max(thumbs, key=lambda t: (t.get("width") or t.get("preference") or 0))
        if best.get("url"):
            return str(best["url"])
    t = info.get("thumbnail")
    return str(t).replace("-large", "-t500x500") if t else None


def _download(url: str) -> tuple[bytes, str] | None:
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=request_timeout(20)) as r:
        data = read_response(r,_MAX_IMG + 1,timeout=20)
        if len(data) > _MAX_IMG:
            return None
        mime = r.headers.get_content_type() or "image/jpeg"
    return data, mime


def _page_og_image(url: str) -> str | None:
    """The <meta og:image> of a public SoundCloud page. For an artist page this
    is the profile picture (avatar); for a track page it's the cover art. Plain
    HTML — avoids yt-dlp's deeper API calls, which SoundCloud often 403s."""
    if _excluded({"url": url}):
        return None
    req = urllib.request.Request(url, headers=_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=request_timeout(15)) as r:
            if _excluded({"url": getattr(r, "geturl", lambda: url)()}):
                return None
            html = read_response(r,400_000,timeout=15).decode("utf-8", "replace")
    except OSError as exc:
        LOG.warning("art: page fetch failed (%s): %s", exc, url)
        return None
    m = re.search(r'<meta property="og:image" content="([^"]+)"', html)
    if not m:
        LOG.warning("art: page has no og:image (blocked/challenge or not public?): %s", url)
    return m.group(1) if m else None


@bounded_operation
def _extract(url, *, flat=False) -> dict:
    import yt_dlp
    from yt_dlp.networking.common import Request

    class ScopedYoutubeDL(yt_dlp.YoutubeDL):
        def urlopen(self, request):
            checkpoint()
            if isinstance(request,str):
                request = Request(request)
            if isinstance(request,Request):
                request = request.copy()
                original_timeout = request.extensions.get('timeout')
                request.extensions['timeout'] = min(request_timeout(15),original_timeout) if isinstance(original_timeout,(int,float)) else request_timeout(15)
            result = super().urlopen(request)
            try:
                checkpoint()
            except BaseException:
                result.close()
                raise
            return ScopedResponse(result)

    opts = {"quiet": True, "no_warnings": True, "skip_download": True,
            "socket_timeout": request_timeout(15), "retries": 1, "extractor_retries": 1}
    if flat:
        opts['extract_flat'] = True
    with ScopedYoutubeDL(opts) as ydl:
        result = ydl.extract_info(url,download=False) or {}
    checkpoint()
    return cast(dict,result)


def _flat_search(query: str, n: int = 5) -> list[dict]:
    info = _extract(f"scsearch{n}:{query}",flat=True)
    return [entry for entry in (info.get("entries") or []) if not _excluded(entry)]


def _full(url: str) -> dict:
    return _extract(url)


@bounded_operation
def fetch_by_search(artist: str, title: str, min_conf: float = MIN_CONFIDENCE) -> ArtResult | None:
    """Search SoundCloud for the track; return its cover only on a CONFIDENT, artist-
    accountable match (see _match_score). Artwork comes straight from the flat search
    result (no deep extract, which SoundCloud 403s). Runs up to two queries — the full
    cleaned title, and 'artist + core song' — because an artist's own upload often omits
    the 'ARTIST -' prefix / bracket tags, so the plainer query surfaces it."""
    clean = _clean_search_title(title)
    core = _core_title_for_artist(clean, artist)
    na = _norm(artist)
    q1 = clean if (na and na in _norm(clean)) else " ".join(x for x in (artist, clean) if x)
    queries = [q1.strip()]
    q2 = " ".join(x for x in (artist, core) if x).strip()
    if q2 and _norm(q2) != _norm(q1):
        queries.append(q2)

    seen, cands = set(), []
    for q in queries:
        if not q:
            continue
        for e in _flat_search(q, 8):
            if _excluded(e):
                continue
            key = e.get("url") or e.get("webpage_url") or e.get("title")
            if key and key not in seen:
                seen.add(key); cands.append(e)

    best, conf, rank = None, 0.0, (0, 0.0)
    for e in cands:
        s = _match_score(clean, core, artist, e.get("title") or "", e.get("uploader") or "",
                         e.get("artists"))
        candidate_rank = (_publisher_rank(artist, clean, e.get("uploader") or "", s), s)
        if candidate_rank > rank:
            conf, best, rank = s, e, candidate_rank
    if not best or conf < min_conf:
        return None  # fall through to the artist avatar — never grab wrong art
    thumb = _best_thumb(best)
    if not thumb:
        return None
    got = _download(thumb)
    if not got:
        return None
    return ArtResult(data=got[0], mime=got[1], confidence=round(min(conf, 1.0), 2),
                     source_url=best.get("webpage_url") or best.get("url") or "",
                     matched_title=best.get("title") or "")


@bounded_operation
def fetch_artist_avatar(artist: str, min_conf: float = _UPLOADER_MATCH) -> ArtResult | None:
    """Fallback when the exact track cover isn't found: the artist's SoundCloud
    profile picture. Finds the artist's page via a track search (whose flat result
    carries a reliable uploader + uploader_url), then reads the page's og:image
    (the avatar). Powered by curated artist names — returns None if no confident
    artist match. Uses the HTML page, not yt-dlp's user API (which SoundCloud 403s)."""
    a = (artist or "").strip()
    if not a:
        return None
    for e in _flat_search(a, 6):
        if _excluded(e):
            continue
        up = e.get("uploader") or ""
        uurl = e.get("uploader_url")
        if not uurl or _ratio(a, up) < min_conf:
            continue
        img = _page_og_image(uurl)
        if not img or "avatar" not in img:  # a track's og:image is cover art, skip
            continue
        got = _download(img)
        if got:
            return ArtResult(data=got[0], mime=got[1], confidence=_ratio(a, up),
                             source_url=uurl, matched_title=f"{up} (avatar)")
    return None


@bounded_operation
def fetch_profile_art(artist: str) -> ArtResult | None:
    """Strict artist-profile fallback for excluded sources, never another track."""
    from soundbox.metadata import UNKNOWN_ARTIST
    if _norm(artist) == _norm(UNKNOWN_ARTIST):
        return None
    for name in dict.fromkeys([artist, *_split_artists(artist)]):
        result = fetch_artist_avatar(name, min_conf=_UPLOADER_SAME)
        if result:
            return result
    return None


@bounded_operation
def fetch_best(artist: str, title: str) -> ArtResult | None:
    """Best available art, accuracy-first:
      1. confident track cover for the artist,
      2. for a collab, a confident cover under EACH individual artist (one of them
         posted it — the combined 'A x B' string often matches nothing),
      3. the artist's avatar as a floor (covers exclusives not on SoundCloud), trying
         each individual artist if the combined one doesn't resolve.
    Never returns a low-confidence cover — a wrong cover is worse than an avatar."""
    # A title without a real artist is not accountable enough for unattended
    # discovery.  In particular, searching for the literal placeholder used to
    # select unrelated same-title uploads and even random "Unknown Artist"
    # profile avatars.  The manual picker/file/URL paths remain available.
    from soundbox.metadata import UNKNOWN_ARTIST
    if _norm(artist) == _norm(UNKNOWN_ARTIST):
        return None
    liked = _liked_candidates(artist, title)
    clear_liked = bool(liked and liked[0]["score"] >= MIN_CONFIDENCE and
        (len(liked) == 1 or
         liked[0].get("publisher_rank", int(bool(liked[0].get("artist_upload")))) >
         liked[1].get("publisher_rank", int(bool(liked[1].get("artist_upload")))) or
         liked[0]["score"] - liked[1]["score"] >= .04))
    if clear_liked and liked[0].get("artist_upload"):
        best = liked[0]
        result = fetch_thumb(best["thumb"], best["url"], best["title"])
        if result:
            result.confidence = round(best["score"], 2)
            return result
    def search(name: str) -> ArtResult | None:
        try:
            return fetch_by_search(name, title)
        except Exception:
            if clear_liked:
                return None  # cached accountable likes survive online-search failure
            raise

    r = search(artist)
    if r:
        return r
    parts = _split_artists(artist)
    for p in parts:
        if p != artist:
            r = search(p)
            if r:
                return r
    if clear_liked:
        best = liked[0]
        result = fetch_thumb(best["thumb"], best["url"], best["title"])
        if result:
            result.confidence = round(best["score"], 2)
            return result
    r = fetch_artist_avatar(artist)
    if r:
        return r
    for p in parts:
        if p != artist:
            r = fetch_artist_avatar(p)
            if r:
                return r
    return None


@bounded_operation
def search_candidates(artist: str, title: str, n: int = 12) -> list[dict]:
    """Ranked SoundCloud results for the MANUAL picker — a human judges, so no
    confidence gate (the auto-matcher's floor is exactly what makes vague-title
    exclusives unhittable; here the user clicks the right one). Best-guess first.
    Each: {title, uploader, thumb (t500), url, score}."""
    liked = [candidate for candidate in _liked_candidates(artist, title, allow_excerpt=True)
             if candidate["score"] >= MIN_CONFIDENCE or candidate.get("possible")]
    clean, core, queries = _search_queries(artist, title)

    seen = {candidate["url"] for candidate in liked}
    out = list(liked)
    for q in queries:
        if not q:
            continue
        try:
            candidates = _flat_search(q, n)
        except Exception:
            if liked:
                break  # cached liked candidates remain useful when online search fails
            raise
        for e in candidates:
            if _excluded(e):
                continue
            key = e.get("url") or e.get("webpage_url") or e.get("title")
            if not key or key in seen:
                continue
            seen.add(key)
            thumb = _best_thumb(e)
            if not thumb:
                continue
            score, reason = _review_score(clean, core, artist, e)
            if score == 0:
                continue
            out.append({
                "title": e.get("title") or "",
                "uploader": e.get("uploader") or "",
                "thumb": thumb,
                "url": e.get("webpage_url") or e.get("url") or "",
                "score": score,
                "possible": bool(reason),
                "reason": reason,
                "artist_upload": _artist_upload(artist, e.get("uploader") or "", score, clean),
                "version_upload": _publisher_rank(artist, clean, e.get("uploader") or "", score) == 2,
                "publisher_rank": _publisher_rank(artist, clean, e.get("uploader") or "", score),
                "excerpt": bool(re.search(r"\b(?:clip|snippet|preview|teaser)\b", e.get("title") or "", re.I)),
            })
        if any(c.get("publisher_rank", 0) > 0 and c["score"] >= .9 for c in out):
            break
    out.sort(key=lambda c: (c.get("publisher_rank", 0), bool(c.get("liked")), c["score"]), reverse=True)
    return out[:n]


def _liked_candidates(artist: str, title: str, *, allow_excerpt: bool = False) -> list[dict]:
    from .connection import liked_entries
    clean = _clean_search_title(title)
    artists = list(dict.fromkeys([artist, *_split_artists(artist)]))
    cores = [(name, _core_title_for_artist(clean, name)) for name in artists]
    out = []
    for entry in liked_entries():
        if _excluded(entry):
            continue
        reason = ""
        if allow_excerpt:
            score, reason = max((_review_score(clean, core, name, entry) for name, core in cores),
                                default=(0.0, ""), key=lambda row: row[0])
        else:
            score = max((_match_score(clean, core, name, entry.get("title") or "",
                                     entry.get("uploader") or "", entry.get("artists"))
                         for name, core in cores), default=0.0)
        thumb = _best_thumb(entry)
        if score >= 0.60 and thumb:
            out.append({"title": entry["title"], "uploader": entry["uploader"],
                        "thumb": thumb, "url": entry["webpage_url"], "score": score,
                        "liked": True,
                        "possible": bool(reason), "reason": reason,
                        "excerpt": bool(re.search(r"\b(?:clip|snippet|preview|teaser)\b", entry["title"], re.I)),
                        "artist_upload": _artist_upload(artist, entry["uploader"], score, clean),
                        "version_upload": _publisher_rank(artist, clean, entry["uploader"], score) == 2,
                        "publisher_rank": _publisher_rank(artist, clean, entry["uploader"], score)})
    out.sort(key=lambda c: (c["publisher_rank"], c["score"]), reverse=True)
    return out


@bounded_operation
def suggest_artist(title: str, min_ratio: float = 0.62,
                   min_hits: int = 2) -> tuple[str, float] | None:
    """Uploader-consensus artist suggestion for an Unknown-Artist track: search
    the cleaned title; if the top results AGREE on one uploader with a strong
    title match, that uploader is very likely the artist. NEVER auto-applied —
    the human accepts each one (accuracy-first). Returns (name, confidence)."""
    clean = _clean_search_title(title)
    if len(_norm(clean)) < 4:
        return None  # too vague to trust any consensus
    by_up: dict[str, dict] = {}
    for e in _flat_search(clean, 6):
        if _excluded(e):
            continue
        up = (e.get("uploader") or "").strip()
        if not up:
            continue
        r = _ratio(clean, _clean_search_title(e.get("title") or ""))
        if r < min_ratio:
            continue
        d = by_up.setdefault(up.casefold(), {"n": 0, "best": 0.0, "name": up})
        d["n"] += 1
        d["best"] = max(d["best"], r)
    if not by_up:
        return None
    ranked = sorted(by_up.values(), key=lambda d: (d["n"], d["best"]), reverse=True)
    best = ranked[0]
    # Consensus = repeat hits AND no rival with the same count (a tie means the
    # scene disagrees — stay silent rather than guess).
    if best["n"] >= min_hits and (len(ranked) == 1 or ranked[1]["n"] < best["n"]):
        return best["name"], round(best["best"], 2)
    # DOMINANT single match: one near-perfect hit with no real competition is
    # stronger than two mediocre agreements ("Dimension Expander" → GRiZ @ 1.0
    # vs a 0.27 mix). Rival edits of the same song (many uploaders ~0.7) never
    # pass the dominance margin — those stay silent, correctly.
    top = max(by_up.values(), key=lambda d: d["best"])
    rivals = [d["best"] for d in by_up.values() if d is not top]
    if top["best"] >= 0.88 and (not rivals or max(rivals) <= top["best"] - 0.15):
        return top["name"], round(top["best"], 2)
    return None


@bounded_operation
def fetch_thumb(thumb_url: str, source_url: str = "", matched_title: str = "") -> ArtResult | None:
    """Download a specific thumbnail (a picker selection) as an ArtResult."""
    if _excluded({"url": source_url or thumb_url}):
        return None
    got = _download(thumb_url)
    if not got:
        return None
    return ArtResult(data=got[0], mime=got[1], confidence=1.0,
                     source_url=source_url or thumb_url, matched_title=matched_title)


@bounded_operation
def fetch_by_url(url: str) -> ArtResult | None:
    """Grab art from a pasted SoundCloud URL. A TRACK page's og:image is its
    cover; an ARTIST PROFILE page's og:image is the avatar — so pasting a
    profile URL deliberately assigns the artist's face (the clean escape hatch
    when an uploader's exclusive carries junk cover art). Falls back to the
    full yt-dlp extract for URLs whose page has no og:image."""
    if _excluded({"url": url}):
        return None
    img = _page_og_image(url)
    if img:
        got = _download(img)
        if got:
            return ArtResult(data=got[0], mime=got[1], confidence=1.0,
                             source_url=url, matched_title="")
        LOG.warning("art: og:image found but image download failed: %s", img)
    LOG.info("art: falling back to full extract for %s", url)
    return _fetch_from_entry(url, 1.0)


def _fetch_from_entry(url: str | None, conf: float) -> ArtResult | None:
    if not url or _excluded({"url": url}):
        return None
    info = _full(url)
    if _excluded(info) or _excluded({"url": url}):
        return None
    thumb = _best_thumb(info)
    if not thumb:
        return None
    got = _download(thumb)
    if not got:
        return None
    data, mime = got
    return ArtResult(data=data, mime=mime, confidence=conf,
                     source_url=info.get("webpage_url") or url,
                     matched_title=info.get("title") or "")


@bounded_operation
def preview_image(candidate: dict) -> bytes | None:
    """Small preview bytes; provider-specific CDN sizing stays outside core."""
    if _excluded(candidate):
        return None
    got = _download(candidate["thumb"].replace("t500x500", "t200x200"))
    return got[0] if got and len(got[0]) <= 512 * 1024 else None
