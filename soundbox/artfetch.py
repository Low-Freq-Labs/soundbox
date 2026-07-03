"""Cover-art fetching via yt-dlp (SoundCloud public pages).

Personal-use only: this scrapes public artwork for the user's own library and is
never redistributed. It does NOT use the SoundCloud developer API, so it can't
affect the user's API credentials. Fetched art is matched by fuzzy title
confidence so wrong art is never applied; uncertain tracks are left for manual
paste-link instead.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path

MIN_CONFIDENCE = 0.60      # title-match floor
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


@dataclass
class ArtResult:
    data: bytes
    mime: str
    confidence: float
    source_url: str
    matched_title: str


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
    return "".join(ch.lower() for ch in (s or "") if ch.isalnum())


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def _core_title(clean: str) -> str:
    """The bare song name: drop bracket groups (VIP/remix/flip) and a leading
    'ARTIST - ' prefix. 'SAKURO - CORROSION' -> 'CORROSION'; 'GNARLY (DENNETT REMIX)'
    -> 'GNARLY'. Used to match an artist's own upload that omits the prefix."""
    s = re.sub(r"[\[(][^\[\]()]*[\])]", " ", clean or "")
    s = re.sub(r"^\s*[^-]{1,30}\s+-\s+", "", s)
    return re.sub(r"\s{2,}", " ", s).strip() or (clean or "")


def _split_artists(artist: str) -> list[str]:
    """Individual artists in a collab string ('DENNETT x MACKAI' -> [DENNETT, MACKAI]).
    A single artist yields just itself. De-dupes; drops stopword fragments."""
    if not artist:
        return []
    out, seen = [], set()
    for p in _ARTIST_SPLIT.split(artist):
        p = p.strip(" -_")
        k = p.lower()
        if len(p) >= 2 and k not in seen and k not in ("the", "feat", "ft"):
            seen.add(k); out.append(p)
    return out


def _match_score(clean: str, core: str, artist: str, cand_title: str, uploader: str) -> float:
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
    cand = _clean_search_title(cand_title)
    t = _ratio(clean, cand)
    u = _ratio(artist, uploader)
    nc = _norm(core)
    if u >= _UPLOADER_SAME and len(nc) >= 4 and nc in _norm(cand):
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
    with urllib.request.urlopen(req, timeout=20) as r:
        data = r.read(_MAX_IMG + 1)
        if len(data) > _MAX_IMG:
            return None
        mime = r.headers.get_content_type() or "image/jpeg"
    return data, mime


def _page_og_image(url: str) -> str | None:
    """The <meta og:image> of a public SoundCloud page. For an artist page this
    is the profile picture (avatar); for a track page it's the cover art. Plain
    HTML — avoids yt-dlp's deeper API calls, which SoundCloud often 403s."""
    req = urllib.request.Request(url, headers=_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            html = r.read(400_000).decode("utf-8", "replace")
    except OSError:
        return None
    m = re.search(r'<meta property="og:image" content="([^"]+)"', html)
    return m.group(1) if m else None


def _flat_search(query: str, n: int = 5) -> list[dict]:
    import yt_dlp
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "extract_flat": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"scsearch{n}:{query}", download=False) or {}
    return info.get("entries") or []


def _full(url: str) -> dict:
    import yt_dlp
    opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False) or {}


def fetch_by_search(artist: str, title: str, min_conf: float = MIN_CONFIDENCE) -> ArtResult | None:
    """Search SoundCloud for the track; return its cover only on a CONFIDENT, artist-
    accountable match (see _match_score). Artwork comes straight from the flat search
    result (no deep extract, which SoundCloud 403s). Runs up to two queries — the full
    cleaned title, and 'artist + core song' — because an artist's own upload often omits
    the 'ARTIST -' prefix / bracket tags, so the plainer query surfaces it."""
    clean = _clean_search_title(title)
    core = _core_title(clean)
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
            key = e.get("url") or e.get("webpage_url") or e.get("title")
            if key and key not in seen:
                seen.add(key); cands.append(e)

    best, conf = None, 0.0
    for e in cands:
        s = _match_score(clean, core, artist, e.get("title") or "", e.get("uploader") or "")
        if s > conf:
            conf, best = s, e
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


def fetch_best(artist: str, title: str) -> ArtResult | None:
    """Best available art, accuracy-first:
      1. confident track cover for the artist,
      2. for a collab, a confident cover under EACH individual artist (one of them
         posted it — the combined 'A x B' string often matches nothing),
      3. the artist's avatar as a floor (covers exclusives not on SoundCloud), trying
         each individual artist if the combined one doesn't resolve.
    Never returns a low-confidence cover — a wrong cover is worse than an avatar."""
    r = fetch_by_search(artist, title)
    if r:
        return r
    parts = _split_artists(artist)
    for p in parts:
        if p != artist:
            r = fetch_by_search(p, title)
            if r:
                return r
    r = fetch_artist_avatar(artist)
    if r:
        return r
    for p in parts:
        if p != artist:
            r = fetch_artist_avatar(p)
            if r:
                return r
    return None


def search_candidates(artist: str, title: str, n: int = 12) -> list[dict]:
    """Ranked SoundCloud results for the MANUAL picker — a human judges, so no
    confidence gate (the auto-matcher's floor is exactly what makes vague-title
    exclusives unhittable; here the user clicks the right one). Best-guess first.
    Each: {title, uploader, thumb (t500), url, score}."""
    clean = _clean_search_title(title)
    core = _core_title(clean)
    na = _norm(artist)
    q1 = clean if (na and na in _norm(clean)) else " ".join(x for x in (artist, clean) if x)
    queries = [q1.strip()]
    q2 = " ".join(x for x in (artist, core) if x).strip()
    if q2 and _norm(q2) != _norm(q1):
        queries.append(q2)

    seen, out = set(), []
    for q in queries:
        if not q:
            continue
        for e in _flat_search(q, n):
            key = e.get("url") or e.get("webpage_url") or e.get("title")
            if not key or key in seen:
                continue
            seen.add(key)
            thumb = _best_thumb(e)
            if not thumb:
                continue
            out.append({
                "title": e.get("title") or "",
                "uploader": e.get("uploader") or "",
                "thumb": thumb,
                "url": e.get("webpage_url") or e.get("url") or "",
                "score": _match_score(clean, core, artist, e.get("title") or "",
                                      e.get("uploader") or ""),
            })
    out.sort(key=lambda c: c["score"], reverse=True)
    return out[:n]


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


def fetch_thumb(thumb_url: str, source_url: str = "", matched_title: str = "") -> ArtResult | None:
    """Download a specific thumbnail (a picker selection) as an ArtResult."""
    got = _download(thumb_url)
    if not got:
        return None
    return ArtResult(data=got[0], mime=got[1], confidence=1.0,
                     source_url=source_url or thumb_url, matched_title=matched_title)


def fetch_by_url(url: str) -> ArtResult | None:
    """Grab art from a pasted SoundCloud URL. A TRACK page's og:image is its
    cover; an ARTIST PROFILE page's og:image is the avatar — so pasting a
    profile URL deliberately assigns the artist's face (the clean escape hatch
    when an uploader's exclusive carries junk cover art). Falls back to the
    full yt-dlp extract for URLs whose page has no og:image."""
    img = _page_og_image(url)
    if img:
        got = _download(img)
        if got:
            return ArtResult(data=got[0], mime=got[1], confidence=1.0,
                             source_url=url, matched_title="")
    return _fetch_from_entry(url, 1.0)


class ArtCache:
    """Persistent per-TrackID art cache (personal, local). Stores each fetched
    image once and remembers misses so we never re-hammer for the same track."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._index_path = self.root / "index.json"
        try:
            self.index = json.loads(self._index_path.read_text()) if self._index_path.exists() else {}
        except (ValueError, OSError):
            self.index = {}

    def status(self, track_id: str) -> str | None:
        return (self.index.get(track_id) or {}).get("status")  # "ok" | "none" | None

    def status_kind(self, track_id: str) -> str:
        """UI status: 'cover' | 'avatar' | 'miss' | 'blocked' | '' (unfetched)."""
        e = self.index.get(track_id)
        if not e:
            return ""
        if e.get("status") == "none":
            return "miss"
        if e.get("status") == "blocked":
            return "blocked"
        return "avatar" if self._is_avatar(e) else "cover"

    def image_path(self, track_id: str) -> Path | None:
        e = self.index.get(track_id) or {}
        if e.get("status") == "ok":
            p = self.root / f"{track_id}.{e.get('ext', 'jpg')}"
            if p.is_file():
                return p
        return None

    def store_ok(self, track_id: str, res: ArtResult):
        # Normalize to baseline JPEG at the cache boundary — Rockbox renders
        # nothing else (progressive JPEG / PNG show blank on the iPod).
        from . import artencode
        data = artencode.ensure_device_safe(res.data)
        ext = "jpg" if artencode.is_device_safe(data) else ("png" if "png" in res.mime else "jpg")
        (self.root / f"{track_id}.{ext}").write_bytes(data)
        self.index[track_id] = {"status": "ok", "ext": ext,
                                "src": res.source_url, "conf": round(res.confidence, 2)}
        self._save()

    def normalize_all(self) -> tuple[int, int]:
        """One-time sweep: re-encode any cached image that isn't baseline JPEG
        (Rockbox-safe). Idempotent + flag-filed, so later launches are instant.
        Returns (checked, fixed)."""
        from . import artencode
        flag = self.root / ".normalized-v1"
        if flag.exists():
            return 0, 0
        checked = fixed = 0
        for tid, e in list(self.index.items()):
            if (e or {}).get("status") != "ok":
                continue
            p = self.root / f"{tid}.{e.get('ext', 'jpg')}"
            if not p.is_file():
                continue
            checked += 1
            data = p.read_bytes()
            if artencode.is_device_safe(data):
                continue
            safe = artencode.to_baseline_jpeg(data)
            if not safe:
                continue  # undecodable — leave as-is rather than destroy
            (self.root / f"{tid}.jpg").write_bytes(safe)
            if e.get("ext") != "jpg":
                p.unlink(missing_ok=True)
                e["ext"] = "jpg"
            fixed += 1
        if fixed:
            self._save()
        try:
            flag.touch()
        except OSError:
            pass
        return checked, fixed

    def store_none(self, track_id: str):
        self.index[track_id] = {"status": "none"}
        self._save()

    def retry_misses(self) -> int:
        """Clear all 'no art found' marks so they'll be re-attempted. User-blocked
        entries are NOT touched — a deliberate removal survives every re-scan."""
        misses = [tid for tid, e in self.index.items() if (e or {}).get("status") == "none"]
        for tid in misses:
            self.index.pop(tid, None)
        self._save()
        return len(misses)

    @staticmethod
    def _is_avatar(e) -> bool:
        """An 'ok' cover that is actually the artist profile picture (fallback),
        i.e. its source is an artist page (one path segment) not a track page."""
        if (e or {}).get("status") != "ok":
            return False
        src = e.get("src") or ""
        if "soundcloud.com/" not in src:
            return False
        tail = src.split("soundcloud.com/", 1)[-1].strip("/")
        return len([x for x in tail.split("/") if x]) <= 1

    def retry_weak(self) -> int:
        """Clear misses AND artist-avatar fallbacks so they re-attempt for a real
        track cover — keeps confirmed real covers. Deletes the stale avatar image
        so it's re-fetched cleanly. Returns how many were cleared."""
        weak = [tid for tid, e in self.index.items()
                if (e or {}).get("status") == "none" or self._is_avatar(e)]
        for tid in weak:
            e = self.index.pop(tid, None)
            if e and e.get("status") == "ok" and e.get("ext"):
                (self.root / f"{tid}.{e['ext']}").unlink(missing_ok=True)
        self._save()
        return len(weak)

    def forget(self, track_id: str):
        e = self.index.pop(track_id, None)
        if e and e.get("ext"):
            (self.root / f"{track_id}.{e['ext']}").unlink(missing_ok=True)
        self._save()

    def block(self, track_id: str):
        """User removed this track's art ON PURPOSE: delete the image but keep a
        'blocked' entry so the background fill NEVER re-fetches it. Only an
        explicit per-track action (Fetch now / picker / URL) overrides — those
        go through store_ok, which overwrites the block."""
        e = self.index.get(track_id)
        if e and e.get("ext"):
            (self.root / f"{track_id}.{e['ext']}").unlink(missing_ok=True)
        self.index[track_id] = {"status": "blocked"}
        self._save()

    def _save(self):
        # Atomic write (temp + rename): the index is written from BOTH the
        # background art-fill thread and main-thread one-shot fetches — a torn
        # write here would orphan the entire cache (every image re-fetched).
        try:
            tmp = self._index_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.index))
            os.replace(tmp, self._index_path)
        except OSError:
            pass


def _fetch_from_entry(url: str | None, conf: float) -> ArtResult | None:
    if not url:
        return None
    info = _full(url)
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
