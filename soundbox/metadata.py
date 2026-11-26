"""Best-effort metadata derivation.

This NEVER modifies source files or tags. It only computes a display/foldering
value from the loaded library or imported records.

Artist derivation strategy, in priority order:
  1. A named EDITOR/REMIXER in brackets — for DJ edits the artist you care about
     is the person who flipped/remixed it, which sits in (parens) or [brackets]
     right before a transformation word (flip, remix, bootleg, edit, VIP, mashup…).
     This explicit credit outranks an original-artist tag, so tagged and untagged
     copies of the same edit resolve consistently.
     If there are several (a VIP of a remix), the LAST one wins — the most recent
     transformation (ZEYDAX VIP of a POLKLYPZ remix → ZEYDAX).
  2. The rekordbox Artist tag, if set.
  3. "Artist - Title" → the part before the first ' - ' (original artist).
  3.5 DJ hyphen styles ("CALCIUM- GUTTER", "Arbityr -Badboy", "ABYSS_-_FEINT",
     single-token "Adele-Fire…") — conservative; hyphenated artists (JAY-Z,
     K-NINE) and multi-hyphen strings never split (registry rescue covers them).
  4. "Unknown Artist".

(A manual override, applied by effective_artist, beats all of the above.)

Generic descriptors like "Radio Edit" / "Club Edit" are rejected so they don't
get mistaken for an editor name.
"""

from __future__ import annotations
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

import re
from collections import Counter
from functools import lru_cache

TAG = "tag"          # came straight from rekordbox Artist field
NAME = "name"        # parsed from the track Name
UNKNOWN = "unknown"  # could not determine

UNKNOWN_ARTIST = "Unknown Artist"
_MAX_ARTIST_LEN = 60

_FEATURE_MARKER = re.compile(r"\s+(?P<bracket>[([])?\b(?:feat(?:uring)?|ft)(?:\.\s*|\s+)", re.IGNORECASE)


def cleanup_credit_pair(name: str, artist: str, *, registry=None) -> tuple[str, str]:
    """Move an explicit featured artist into the title without losing credit.

    Direct collaborations stay intact. A title prefix can supply the original
    credit when the native artist was originally empty. Call before freezing
    either desired native field; this function grants no write authority.
    """
    credited = artist
    from_name = False
    if not _FEATURE_MARKER.search(credited):
        if not _FEATURE_MARKER.search(name):
            return name, artist
        candidate, _origin = derive_artist(name, artist)
        if not _FEATURE_MARKER.search(candidate):
            return name, artist
        credited = candidate
        from_name = True
    marker = _FEATURE_MARKER.search(credited)
    if marker is None:
        return name, artist
    primary = credited[:marker.start()].strip()
    featured = credited[marker.end():].strip()
    if marker['bracket']:
        closing = ')' if marker['bracket'] == '(' else ']'
        if not featured.endswith(closing):
            return name, artist
        featured = featured[:-1].rstrip()
    if not primary or not featured:
        return name, artist
    if from_name and registry is not None and not editor_credits(name):
        reverse = _flip_candidate(name)
        if reverse and registry.is_trusted(reverse) and not registry.is_trusted(primary):
            if ' - ' in name:
                title = name.rsplit(' - ', 1)[0].strip()
            else:
                separators = list(_HYPHEN_SEP.finditer(name))
                title = name[:separators[0].start()].strip() if len(separators) == 1 else name
            return canonical_title_credits(title, registry), registry.canonical(reverse)
    editors = editor_credits(name)
    title = (name if editors and _fold(editors[-1][0]) != _fold(credited)
             else neat_title(name, credited))
    # Accept an existing ft/feat/featuring spelling in brackets or an edition
    # credit, preserving its exact wording and position rather than duplicating.
    escaped = re.escape(featured).replace(r"\ ", r"\s+")
    existing = re.search(r"\b(?:feat(?:uring)?|ft)(?:\.\s*|\s+)" + escaped + r"(?!\w)", title, re.IGNORECASE)
    if not existing:
        title = f'{title} (feat. {featured})'
    if registry is not None:
        title = canonical_title_credits(title, registry)
        primary = registry.canonical(primary)
    return title, primary


_CREDIT_SEPARATOR = re.compile(
    r"(\s+(?:x|&|vs\.?|\+|w/)\s+|,\s*|\s+(?:feat(?:uring)?|ft)(?:\.\s*|\s+))", re.I)


def _credit_parts(name: str) -> list[str]:
    return _CREDIT_SEPARATOR.split(name)


def _ambiguous_group(parts: list[str]) -> bool:
    return any('&' in separator or ',' in separator for separator in parts[1::2])


def _canonical_credit(name: str, canon: Mapping[str, str], components: Mapping[str, str],
                      independent: frozenset[str], protected: frozenset[str]) -> str:
    selected = canon.get(_fold(name)) or clean_artist(name)
    if _fold(selected) in protected:
        return selected
    parts = _credit_parts(selected)
    # Ampersands/commas also occur in indivisible group names. Require every
    # constituent to have independent solo/x/feature evidence before changing
    # any constituent; an explicit whole-group alias always stays whole.
    if len(parts) == 1:
        return components.get(_fold(selected)) or selected
    if (_ambiguous_group(parts) and not all(_fold(part) in independent for part in parts[::2])):
        return selected
    if _fold(selected) == _fold(name):
        parts = _credit_parts(name.strip())
    for index in range(0, len(parts), 2):
        part = parts[index]
        replacement = components.get(_fold(part))
        if replacement:
            parts[index] = part[:len(part) - len(part.lstrip())] + replacement + part[len(part.rstrip()):]
    return ''.join(parts)

# Separator-ish junk an artist name sometimes carries on its edges (e.g. "Dennett_",
# " - Dennett", "Dennett ~"). Trimmed from the ENDS only, never from inside a name.
_JUNK_EDGE = r"[\s_\-–—|~]+"
_JUNK_LEAD = re.compile(r"^" + _JUNK_EDGE)
_JUNK_TAIL = re.compile(_JUNK_EDGE + r"$")


def clean_artist(name: str) -> str:
    """Trim edge junk/whitespace and collapse inner runs of spaces. Leaves the
    real name (and any internal punctuation) intact."""
    s = (name or "").strip()
    s = _JUNK_LEAD.sub("", s)
    s = _JUNK_TAIL.sub("", s)
    return re.sub(r"\s+", " ", s).strip()


def _fold(name: str) -> str:
    """Case/space-insensitive key so 'DENNETT', 'dennett' and 'Dennett ' collapse
    to one artist — while 'DENNETT x MACKAI' stays a distinct (collab) key."""
    return clean_artist(name).casefold()


class ArtistRegistry:
    """Per-library canonical artist map, built once at load. Two jobs:
      • dedup: map any spelling of an artist to one canonical form (folder-safe).
      • rescue: for an Unknown track, find a known artist named in its title
        (survives junk chars, e.g. 'sicko mode (dennett_ flip)' -> 'Dennett').
    Collaboration identities remain whole; proven constituent spellings share
    their per-track majority across credits. Never touches
    rekordbox; a manual override always wins upstream in effective_artist."""

    def __init__(self, canon_by_fold: dict[str, str], trusted_folds=frozenset(), *,
                 cleanup_enabled: bool = True, aliases=None, components=None, independent=frozenset()):
        self._canon = canon_by_fold
        self.cleanup_enabled = cleanup_enabled
        self._explicit_aliases = dict(aliases or {})
        self._components = dict(components or {})
        self._independent = frozenset(independent)
        self._protected_groups = frozenset(_fold(value) for value in self._explicit_aliases.values()
                                          if len(_credit_parts(value)) > 1)
        # TRUSTED names: rekordbox-tagged artists + the user's own override values.
        # These are human-confirmed, so they can gate risky moves (the reversed
        # "TITLE - ARTIST" flip) that raw derived names must never gate — every
        # derived left-side lands in _canon, so _canon membership proves nothing.
        self._trusted = set(trusted_folds)
        # Rescue candidates: real names >= 3 chars, matched longest-first so the
        # most specific artist wins ("Dirt Monkey" before "Dirt"). Alias KEYS are
        # included so junk spellings in titles ("unownn") still rescue to their
        # merge target (the canon lookup resolves them).
        self._rescue_names = sorted({c for c in canon_by_fold.values() if len(c) >= 3}
                       | {k for k in canon_by_fold if len(k) >= 3},
                       key=len, reverse=True)
        if not cleanup_enabled:
            self._rescue_names = []
        self._rescue_re: re.Pattern[str] | None = None

    def canonical(self, name: str) -> str:
        if not self.cleanup_enabled:
            return self.explicit_alias(name)
        return _canonical_credit(name, self._canon, self._components, self._independent, self._protected_groups)

    def explicit_alias(self, name: str) -> str:
        return _explicit_alias(name, self._explicit_aliases)

    def names(self) -> list:
        """All canonical artist spellings (for pickers/completion)."""
        return sorted({self.canonical(value) for value in self._canon.values()}, key=str.casefold)

    def is_trusted(self, name: str) -> bool:
        return _fold(name) in self._trusted

    def rescue_from_title(self, title: str) -> str | None:
        if not title or not self._rescue_names:
            return None
        if self._rescue_re is None:
            # Cached browser rows need no title rescue. Compile only when a
            # genuinely missing/changed row needs this exact existing policy.
            self._rescue_re = re.compile(
                r"(?<![^\W_])(" + "|".join(re.escape(n) for n in self._rescue_names)
                + r")(?![^\W_])", re.IGNORECASE)
        m = self._rescue_re.search(title)
        if not m:
            return None
        return self.canonical(m.group(1))


@dataclass(frozen=True,slots=True)
class FrozenArtistRegistry:
    """Worker-built immutable lookup context, shared by dirty scalar batches."""
    canon: Mapping[str,str]
    trusted: frozenset[str]
    rescue: re.Pattern[str] | None
    cleanup_enabled: bool = True
    aliases: Mapping[str, str] | None = None
    components: Mapping[str, str] | None = None
    independent: frozenset[str] = frozenset()
    protected_groups: frozenset[str] = frozenset()

    def canonical(self,name):
        return (_canonical_credit(name, self.canon, self.components or {}, self.independent, self.protected_groups)
                if self.cleanup_enabled else self.explicit_alias(name))
    def explicit_alias(self,name):return _explicit_alias(name,self.aliases or {})
    def is_trusted(self,name):return _fold(name) in self.trusted
    def rescue_from_title(self,title):
        found=self.rescue.search(title) if title and self.rescue else None
        return self.canonical(found.group(1)) if found else None


def freeze_registry(registry):
    """Call once in the existing refresh worker, never on a head/timer tick."""
    if registry is None:return FrozenArtistRegistry(MappingProxyType({}),frozenset(),None)
    if isinstance(registry,FrozenArtistRegistry):return registry
    regex=(re.compile(r"(?<![^\W_])("+'|'.join(re.escape(name) for name in registry._rescue_names)+r")(?![^\W_])",
                      re.IGNORECASE) if registry._rescue_names else None)
    return FrozenArtistRegistry(MappingProxyType(dict(registry._canon)),frozenset(registry._trusted),regex,
        registry.cleanup_enabled,MappingProxyType(dict(registry._explicit_aliases)),
        MappingProxyType(dict(registry._components)),registry._independent,registry._protected_groups)


def registry_context_equal(first, second):
    """Compare global lookup policy without deriving rows or compiling regexes."""
    if first is second:
        return True
    if isinstance(first, ArtistRegistry) and isinstance(second, ArtistRegistry):
        return (first._canon == second._canon and first._trusted == second._trusted
                and first._rescue_names == second._rescue_names
                and first.cleanup_enabled == second.cleanup_enabled
                and first._explicit_aliases == second._explicit_aliases
                and first._components == second._components and first._independent == second._independent)
    if isinstance(first, FrozenArtistRegistry) and isinstance(second, FrozenArtistRegistry):
        return (first.canon == second.canon and first.trusted == second.trusted
                and first.cleanup_enabled == second.cleanup_enabled and (first.aliases or {}) == (second.aliases or {})
                and (first.components or {}) == (second.components or {}) and first.independent == second.independent
                and (first.rescue.pattern if first.rescue else None)
                    == (second.rescue.pattern if second.rescue else None))
    return False


def build_registry(base_artists, trusted_names=(), aliases=None, *, cleanup_enabled=True) -> ArtistRegistry:
    """Fold all real (non-Unknown) artist strings into canonical spellings. The
    winning spelling per artist is the most common one seen (tiebreak: longer,
    then alphabetical — deterministic). An artist can therefore never appear
    under two spellings — CAPS/lowercase merge to whichever has more tracks.

    `base_artists` supplies one selected credit per track (manual before
    automatic). Each constituent votes once per track. `trusted_names` supplies
    confidence/fallback names, never a second vote for the same source row.
    `aliases` = user merges {variant: target} applied on top (typo dedup)."""
    if not cleanup_enabled:
        names = [*base_artists, *trusted_names, *(aliases or {}).values()]
        return ArtistRegistry({name: name for name in names if name},
                              cleanup_enabled=False, aliases=aliases)
    groups: dict[str, Counter] = {}
    component_groups: dict[str, Counter] = {}
    independent = set()
    trusted_folds = set()
    base_artists = list(base_artists)
    trusted_names = list(trusted_names)
    for a in trusted_names:
        ca = clean_artist(a)
        if ca and ca != UNKNOWN_ARTIST:
            trusted_folds.add(_fold(ca))
    for a in base_artists:
        ca = clean_artist(a)
        # A name with no letters ("2021", "3", "140") is title junk that leaked
        # through derivation, never an artist — keeping it would poison both
        # canonicalization and the rescue regex (real bug: tracks 'rescued' to
        # the artist "2021").
        if not ca or ca == UNKNOWN_ARTIST or not re.search(r"[^\W\d_]", ca):
            continue
        groups.setdefault(_fold(ca), Counter())[ca] += 1
        parts = _credit_parts(ca)
        seen = set()
        for component in parts[::2]:
            component = clean_artist(component)
            fold = _fold(component)
            if (not component or fold in seen or not re.search(r"[^\W\d_]", component)):
                continue
            seen.add(fold)
            component_groups.setdefault(fold, Counter())[component] += 1
            if not _ambiguous_group(parts):
                independent.add(fold)
    for a in trusted_names:
        ca = clean_artist(a)
        if ca and ca != UNKNOWN_ARTIST and re.search(r"[^\W\d_]", ca):
            groups.setdefault(_fold(ca), Counter()).setdefault(ca, 0)
    canon: dict[str, str] = {}
    for fold, ctr in groups.items():
        canon[fold] = sorted(ctr.items(), key=lambda kv: (-kv[1], -len(kv[0]), kv[0]))[0][0]
    components = {fold: sorted(ctr.items(), key=lambda kv: (-kv[1], -len(kv[0]), kv[0]))[0][0]
                  for fold, ctr in component_groups.items()}
    _resolve_aliases(canon,trusted_folds,aliases or {})
    _resolve_aliases(components,set(),aliases or {})
    return ArtistRegistry(canon, trusted_folds, cleanup_enabled=cleanup_enabled, aliases=aliases,
                          components=components, independent=independent)


def _explicit_alias(name: str, aliases: Mapping[str, str]) -> str:
    """Keep-originals mode follows only the user's exact saved alias edges."""
    seen = set()
    while name in aliases and name not in seen:
        seen.add(name)
        name = aliases[name]
    return name


def _resolve_aliases(canon, trusted, aliases):
    """Resolve a functional graph iteratively, including legacy cycles.

    Normal terminals retain the existing majority/case winner. A legacy cycle
    uses its smallest folded name and that name's existing spelling winner.
    Conflicting folded keys choose a stable target instead of JSON key order.
    Every result is a fixed point, including after native artist readback.
    """
    choices:dict[str,list[tuple[str,str,str]]]={}
    spellings:dict[str,Counter]={}
    for alias,target in aliases.items():
        source=clean_artist(alias);destination=clean_artist(target)
        af,tf=_fold(source),_fold(destination)
        if not af or not tf or af==tf:continue
        choices.setdefault(af,[]).append((tf,destination,source))
        spellings.setdefault(af,Counter())[source]+=1
        spellings.setdefault(tf,Counter())[destination]+=1
    edges={source:min(candidates)[0] for source,candidates in choices.items()}
    resolved:dict[str,tuple[str,bool]]={}
    def label(node):
        if node in canon:return canon[node]
        return min(spellings[node],key=lambda name:(-spellings[node][name],-len(name),name))
    for start in sorted(edges):
        if start in resolved:continue
        path:list[str]=[];positions:dict[str,int]={};node=start
        while node in edges and node not in resolved and node not in positions:
            positions[node]=len(path);path.append(node);node=edges[node]
        if node in resolved:
            destination,inherited=resolved[node]
        elif node in positions:
            cycle=path[positions[node]:]
            destination=label(min(cycle));inherited=any(value in trusted for value in cycle)
        else:
            destination=label(node);inherited=node in trusted
        for source in reversed(path):
            inherited=inherited or source in trusted
            resolved[source]=(destination,inherited);canon[source]=destination
            if inherited:trusted.add(source)


def suggest_merges(artist_counts: dict, min_ratio: float = 0.85) -> list:
    """Likely duplicate artist SPELLINGS across the library ("unownn" vs
    "UNOWN"), for the human-gated merge window. Local + fuzzy: alnum-normalized
    edit ratio ≥ min_ratio between canonical names. The variant with FEWER
    tracks merges into the one with more (most-tracks-wins, same rule as case
    folding). Collabs never merge into their components (ratio too low).
    Returns [(variant, target, ratio)] sorted by confidence."""
    import difflib

    def norm(n):
        return re.sub(r"[^a-z0-9]", "", n.casefold())

    names = []
    for name in artist_counts:
        if name and name != UNKNOWN_ARTIST:
            normalized = norm(name)
            if len(normalized) >= 4:names.append((name, normalized))
    # Keep the second sequence fixed so SequenceMatcher reuses its character
    # positions/counts. Its ratio is asymmetric: never swap the original pair.
    by_length: dict[int, list[tuple[int, str, str]]] = {}
    best: dict[str, tuple[tuple[float, int, int, int], tuple[str, str, float]]] = {}
    for j, (b, nb) in enumerate(names):
        sm = difflib.SequenceMatcher(None, '', nb)
        for length in range(len(nb)-3, len(nb)+4):
            for i, a, na in by_length.get(length, ()):
                sm.set_seq1(na)
                if sm.real_quick_ratio() < min_ratio:continue
                ceiling = sm.quick_ratio()
                if ceiling < min_ratio:continue
                v, t = (a, b) if artist_counts[a] <= artist_counts[b] else (b, a)
                ceiling_rank = (-round(ceiling, 2), -artist_counts[t], i, j)
                if v in best and ceiling_rank >= best[v][0]:
                    continue  # Even this proven upper bound cannot replace it.
                r = sm.ratio()
                if r < min_ratio:continue
                ratio = round(r, 2)
                # The old confidence sort was stable in original (i,j) order.
                # Keeping just its first row per variant bounds retained output
                # without changing ties, target choice or final suggestion order.
                rank = (-ratio, -artist_counts[t], i, j)
                if v not in best or rank < best[v][0]:best[v] = (rank, (v, t, ratio))
        by_length.setdefault(len(nb), []).append((j, b, nb))
    return [row for _rank, row in sorted(best.values())]


# DJ-style hyphen separators seen in real untagged files (no ' - '): "CALCIUM- GUTTER",
# "Arbityr -Badboy", "ABYSS_-_FEINT", "KrabbeR-_DATA". Underscores double as spaces.
_HYPHEN_SEP = re.compile(r"(?:_-_|_-|-_|-\s|\s-)")
# A right side that's just a version marker means "TITLE-v2", not "ARTIST-TITLE".
_VERSION_JUNK = re.compile(r"^(?:v?\d+|final|master(?:ed)?|wip|draft|slwmo)\b", re.I)
# What an artist string may look like (word chars + the few symbols real names use).
_NAMEISH = re.compile(r"^[\w$&.'!\s]+$")
_HAS_LETTER = re.compile(r"[A-Za-z]")


def _split_hyphen_artist(n: str) -> str | None:
    """Artist from a hyphen-separated untagged name, conservatively:
      • exactly ONE spaced/underscored separator ("X- ", " -X", "_-_") → left side
        (may be multi-word: "DACK JANIELS & GRAVEROT- DEATH MOTH");
      • else exactly ONE bare hyphen → left side ONLY if it's a single token
        ("Adele-Fire To The Rain" yes; "EVERY NIGHT-BADTRIP" no — reversed style).
    Hyphenated ARTISTS stay safe: "K-NINE New Castle" (left too short) and
    multi-hyphen strings ("INFEKT-FEARLESS-THE-DOT") never split — those fall
    through to the registry rescue, which knows real names like JAY-Z/T-PAIN."""
    seps = list(_HYPHEN_SEP.finditer(n))
    if len(seps) == 1:
        left, right = n[:seps[0].start()], n[seps[0].end():]
    elif not seps and n.count("-") == 1:
        left, right = n.split("-", 1)
        if " " in clean_artist(left.replace("_", " ")):
            return None  # bare split only trusts a single-token left
    else:
        return None
    left = clean_artist(left.replace("_", " "))
    right = clean_artist(right.replace("_", " "))
    if (len(left) >= 2 and _ok(left) and _NAMEISH.match(left)
            and _HAS_LETTER.search(left) and right and not _VERSION_JUNK.match(right)):
        return left
    return None


# Words that mean "someone transformed this track" — the name before them is the editor.
_EDIT_KW = r"(?:flip|remix|bootleg|edit|edition|vip|mashup|rework|refix|blend|rmx|reboot|redux|version)"
# Style words that ride along with an editor name ("Netgate TECHNO Edit") — part of
# the edit's description, not the person. Trimmed from the captured name's tail.
_STYLE = {"techno", "house", "dubstep", "riddim", "trap", "dnb", "halftime",
          "drumstep", "tearout", "brostep", "bass"}
# Match (name <kw>) or [name <kw>]; capture the name before the keyword.
_EDITOR_RE = re.compile(
    r"[(\[]\s*([^()\[\]]+?)\s+(" + _EDIT_KW + r")\b[^()\[\]]*[)\]]",
    re.IGNORECASE,
)
# Generic descriptors that precede a transformation word but aren't a person.
_GENERIC = {
    "radio", "clean", "dirty", "short", "extended", "club", "original",
    "instrumental", "acapella", "intro", "outro", "album", "single", "main",
    "explicit", "quick", "rough", "final", "master", "vocal", "dub", "re",
    "official", "deluxe", "special", "limited", "anniversary", "expanded",
    "standard", "alternate", "alternative", "bonus", "international",
}
# Delivery/release labels can follow a genuine named remix. They describe the
# release rather than a later creator, but remain valid words inside a name
# such as "DJ Ride". Do not use these to reject ordinary tagged artist names.
_RELEASE_DESCRIPTORS = {"ep", "dj", "streaming"}


def _ok(name: str) -> bool:
    return bool(name) and name.lower() not in _GENERIC and len(name) <= _MAX_ARTIST_LEN


def editor_credits(title: str) -> tuple[tuple[str, str], ...]:
    """Named version creators shared by artist derivation and artwork matching.

    Preserve order: the final transformation identifies the effective artist.
    Generic release descriptors never become people.
    """
    out = []
    for name, kind in _EDITOR_RE.findall(title or ""):
        words = name.strip(" -–—|").split()
        while words and words[-1].casefold() in _STYLE:
            words.pop()
        candidate = " ".join(words)
        generic = all(word.casefold() in _GENERIC
                      or kind.casefold() in {"version", "edition"} and word.casefold() in _RELEASE_DESCRIPTORS
                      or re.fullmatch(r"\d+(?:st|nd|rd|th)?", word)
                      for word in words)
        if _ok(candidate) and not generic:
            out.append((candidate, kind.casefold()))
    return tuple(out)


def _canonical_credit_fragment(fragment, registry, *, trailing_kind=False):
    words = list(re.finditer(r'\S+', fragment))
    first = 0; last = len(words)
    while first < last and words[first][0].casefold() in _STYLE:
        first += 1
    while last > first and (words[last - 1][0].casefold() in _STYLE
            or trailing_kind and re.fullmatch(_EDIT_KW, words[last - 1][0], re.I)):
        last -= 1
    if first == last:
        return fragment
    start, end = words[first].start(), words[last - 1].end()
    original = fragment[start:end]
    aliased = registry.explicit_alias(original)
    if aliased == original:
        parts = _credit_parts(original)
        for index in range(0, len(parts), 2):
            value = parts[index]
            replacement = registry.explicit_alias(value.strip())
            parts[index] = value[:len(value) - len(value.lstrip())] + replacement + value[len(value.rstrip()):]
        aliased = ''.join(parts)
    replacement = registry.canonical(aliased)
    # Title credit spelling/ornamentation is musical text. Only letter case or
    # an explicit alias edge may change it; artist-edge junk trimming does not
    # authorize removing a quote/underscore from a title's named version.
    if replacement.casefold() != aliased.casefold():
        replacement = aliased
    return fragment[:start] + replacement + fragment[end:]


def canonical_title_credits(title, registry):
    """Canonicalize explicit creator spans, never arbitrary song-title text."""
    if registry is None or not registry.cleanup_enabled:
        return title
    feature = re.compile(r'\b(?:feat(?:uring)?|ft)(?:\.\s*|\s+)([^()\[\]{}]+)', re.I)
    def replace_feature(match):
        start, end = match.span(1)
        return (match[0][:start - match.start()]
                + _canonical_credit_fragment(match[1], registry, trailing_kind=True)
                + match[0][end - match.start():])
    title = feature.sub(replace_feature, title)
    def replace_editor(match):
        if not editor_credits(match[0]):
            return match[0]
        start, end = match.span(1)
        return (match[0][:start - match.start()]
                + _canonical_credit_fragment(match[1], registry)
                + match[0][end - match.start():])
    return _EDITOR_RE.sub(replace_editor, title)


# Junk that trails an artist name at the END of a "TITLE - ARTIST junk" string
# ("ZONDO final", "WODD REMIX", "FURTHA v3"): version/format words, bare BPMs,
# Camelot keys, years. Stripped token-by-token from the right.
_TAIL_JUNK = re.compile(
    r"^(?:final|master(?:ed)?\d*|wip|draft|remix|flip|edit|vip|v\d+(?:\.\d+)?|"
    r"1\d{2}|\d{1,2}[ab]|(?:19|20)\d{2}|[\d.]+)$", re.IGNORECASE)


def _flip_candidate(name: str) -> str:
    """The would-be artist if a title is actually 'TITLE - ARTIST' (reversed —
    the scene is inconsistent): the text after the LAST separator, with trailing
    junk tokens stripped. Caller must gate this against TRUSTED artists."""
    n = (name or "").strip()
    if " - " in n:
        right = n.rsplit(" - ", 1)[1]
    else:
        m = list(_HYPHEN_SEP.finditer(n))
        if len(m) != 1:
            return ""
        right = n[m[0].end():]
    words = clean_artist(right.replace("_", " ")).split()
    while words and _TAIL_JUNK.match(words[-1]):
        words.pop()
    cand = clean_artist(" ".join(words))
    return cand if len(cand) >= 2 else ""


# Collab separators inside an artist string ("1SEC x Strange Language").
_COLLAB_SPLIT = re.compile(r"\s+(?:x|&|vs\.?|\+|feat\.?|ft\.?|w/)\s+|\s*,\s*", re.I)


def _artist_set(a: str) -> set:
    return {_fold(p) for p in _COLLAB_SPLIT.split(a or "") if p.strip()}


def _compile_artist_edges(artist: str) -> tuple[re.Pattern[str], re.Pattern[str]]:
    escaped = re.escape(artist)
    return (re.compile(rf"^\s*{escaped}\s*[-–—_:]+\s*", re.IGNORECASE),
            re.compile(rf"\s*[-–—_:]+\s*{escaped}\s*$", re.IGNORECASE))


_cached_artist_edges = lru_cache(maxsize=1024)(_compile_artist_edges)


def neat_title(name: str, artist: str) -> str:
    """Display/export title cleanup: drop a redundant KNOWN-artist name glued to
    the title's edge ('Zero - Enter The Dragon' by Zero → 'Enter The Dragon';
    reversed 'GARDEN - WODD' by WODD → 'GARDEN'). Collab-aware: the edge segment
    matches as a SET of artists, so '1SEC x Strange Language - Darkness' strips
    even if the artist field says '&' instead of 'x' or reverses the order.
    Only at the edges, only with a separator, so mid-title tags like
    '(WODD Remix)' always survive. Falls back to the original if stripping would
    leave nothing real. Pure display/export transform — overrides beat it,
    rekordbox never touched."""
    ca = clean_artist(artist)
    if not name or len(ca) < 2 or ca == UNKNOWN_ARTIST:
        return name
    # The stdlib regex cache holds only 512 patterns. Two dynamic edge
    # expressions per artist evict each other in an ordinary large library.
    # Retain bounded pure text/compiled patterns, never source or row authority.
    # Oversized external strings keep the exact transform without residency.
    prefix, suffix = (_cached_artist_edges(ca) if len(ca) <= 256
                      else _compile_artist_edges(ca))
    out = prefix.sub("", name.strip())
    out = suffix.sub("", out)
    aset = _artist_set(ca)
    if out == name.strip() and " - " in out and aset:
        # Exact edge missed — try the segments as artist SETS (handles collab
        # separator/order mismatches between the title and the artist field).
        left, rest = out.split(" - ", 1)
        lset = _artist_set(left)
        if lset and lset <= aset:
            out = rest
        else:
            head, _, tail = out.rpartition(" - ")
            tset = _artist_set(tail)
            if head and tset and tset <= aset:
                out = head
    out = out.strip(" -–—_")
    return out if len(out) >= 2 else name


# --- Display-grade junk. DELIBERATELY TINY: only high-confidence production
# markers auto-drop. Years, WIP/draft labels, durations, format notes, and other
# ambiguous text stay MANUAL. Mixdown markers are edge-scoped so names such as
# "Los Master Plus" and mid-title uses survive. ---
# --- Display/export title junk — NAMED, user-toggleable rules (Settings →
# TITLE CLEANUP). Nothing hard-coded away from the user: each built-in shows by
# NAME with a checkbox; custom plain words can be added. A plain word drops only
# from the END of a title or as a whole-(bracket)-group — never mid-title, so
# "The Final Countdown" survives "final". ---
_MIXDOWN_TOKEN = (
    r"(?:final|master(?:ed)?(?:\s*(?:v?\d+|update))?|"
    r"mstr(?:\s*\d+)?|m\d+)"
)
_MIXDOWN_GROUP = (
    rf"(?:\(\s*{_MIXDOWN_TOKEN}(?:[\s_-]+{_MIXDOWN_TOKEN})*[\s_]*\)|"
    rf"\[\s*{_MIXDOWN_TOKEN}(?:[\s_-]+{_MIXDOWN_TOKEN})*[\s_]*\]|"
    rf"\{{\s*{_MIXDOWN_TOKEN}(?:[\s_-]+{_MIXDOWN_TOKEN})*[\s_]*\}})"
)
_MIXDOWN_PATTERN = (
    rf"(?:{_MIXDOWN_GROUP}|(?<!\w){_MIXDOWN_TOKEN}"
    rf"(?:[\s_-]+{_MIXDOWN_TOKEN})*[\s_]*$)"
)

_VERSION_TOKEN = r"v\s*\.?\s*\d+(?:\.\d+)?"
_VERSION_PATTERN = (
    # Copy suffixes in this library are single-digit Finder/browser duplicates.
    # Larger bracketed numbers are years, BPM, or bitrate—not versions.
    rf"(?:\(\s*(?:{_VERSION_TOKEN}|[1-9])[\s_]*\)|"
    rf"\[\s*(?:{_VERSION_TOKEN}|[1-9])[\s_]*\]|"
    rf"\{{\s*(?:{_VERSION_TOKEN}|[1-9])[\s_]*\}}|"
    # Consume a punctuation separator with a version so `.v3`, `-v4` inside a
    # meaningful bracket, and ` - V7 [credit]` do not leave punctuation shells.
    rf"(?:\s*[-_.]\s*)?\b{_VERSION_TOKEN}\b)"
)

_CAMELOT_TOKEN = r"\d{1,2}[ab]"
_BPM_NUMBER = r"\d{2,3}(?:\.\d+)?"
_BPM_WORD = rf"{_BPM_NUMBER}\s*bpm"
_BPM_KEY_PATTERN = (
    # Exact leading "4A - 140 -" and "145 - 2A -" metadata prefixes.
    rf"(?:^\s*(?:(?:{_CAMELOT_TOKEN})\s*-\s*(?:{_BPM_NUMBER})|"
    rf"(?:{_BPM_NUMBER})\s*-\s*(?:{_CAMELOT_TOKEN}))\s*-\s*|"
    # Whole paired bracket groups containing BPM data, plus key-only groups.
    rf"\([^()\[\]{{}}]*\bbpm\b[^()\[\]{{}}]*\)|"
    rf"\[[^()\[\]{{}}]*\bbpm\b[^()\[\]{{}}]*\]|"
    rf"\{{[^()\[\]{{}}]*\bbpm\b[^()\[\]{{}}]*\}}|"
    rf"\(\s*{_CAMELOT_TOKEN}\s*\)|\[\s*{_CAMELOT_TOKEN}\s*\]|"
    rf"\{{\s*{_CAMELOT_TOKEN}\s*\}}|"
    # A bare 1xx in brackets is BPM metadata; 320/years remain untouched.
    r"\(\s*1\d{2}\s*\)|\[\s*1\d{2}\s*\]|\{\s*1\d{2}\s*\}|"
    # Explicit "150bpm" anywhere, or bare trailing BPM/Camelot tokens.
    rf"(?<!\w){_BPM_WORD}(?!\w)|"
    rf"(?:\s+(?:1\d{{2}}|{_CAMELOT_TOKEN}))+\s*$)"
)

_SAMPLE_RATE = r"(?:41|44(?:\.1)?|48)\s*k(?:hz)?"
_BIT_DEPTH = rf"(?:16|24)\s*bit(?:[\s_-]+{_SAMPLE_RATE}(?:[\s_-]+sq)?)?"
_RENDER_TOKEN = (
    rf"(?:mp3|wav|aiff?|flac|{_BIT_DEPTH}|"
    rf"(?:128|192|256|320)(?:\s*kbps)?)"
)
_RENDER_PATTERN = (
    rf"(?:\(\s*{_RENDER_TOKEN}\s*\)|\[\s*{_RENDER_TOKEN}\s*\]|"
    rf"\{{\s*{_RENDER_TOKEN}\s*\}}|(?:[\s._-]+){_RENDER_TOKEN}\s*$)"
)

BUILTIN_TITLE_RULES = (
    # (id, concise user-facing name, case-insensitive regex)
    ("mixdown", "Mixdown Label", _MIXDOWN_PATTERN),
    ("version", "Copy / Version tags", _VERSION_PATTERN),
    ("bpmkey", "BPM / Key tags",
     _BPM_KEY_PATTERN),
    ("render", "Render / File tags", _RENDER_PATTERN),
    ("drop", 'Drop "(Original Mix)"', "(Original Mix)"),
)

# Users may have disabled one of the old implementation-shaped rows. Merging
# rows must never silently re-enable cleanup, so any disabled legacy component
# conservatively disables its new combined rule.
_LEGACY_TITLE_RULES = {
    "mixdown": {"final", "mastern"},
    "version": {"verbrack", "vnum"},
    "bpmkey": {"keybpm", "bpmbrack", "bpmword", "tailbpm"},
}


def normalize_title_rules_off(rules_off=()) -> list[str]:
    """Migrate disabled IDs from the former eight-row cleanup UI."""
    off = set(rules_off or ())
    for current, legacy in _LEGACY_TITLE_RULES.items():
        if off & legacy:
            off.add(current)
        off.difference_update(legacy)
    known = {rid for rid, _name, _pattern in BUILTIN_TITLE_RULES}
    return sorted(off & known)


def junk_patterns(rules_off=(), words=()) -> list:
    """The effective pattern list from the user's rule toggles + custom words."""
    off = set(normalize_title_rules_off(rules_off))
    pats = [pat for rid, _name, pat in BUILTIN_TITLE_RULES if rid not in off]
    return pats + [w.strip() for w in (words or ()) if w and w.strip()
                   and not is_default_title_drop(w)]


def is_default_title_drop(word: str) -> bool:
    """Legacy custom copies belong to the default toggle; retain saved data."""
    return bool(re.fullmatch(r"\(\s*Original\s+Mix\s*\)", word.strip(), re.I))


DEFAULT_TITLE_JUNK = tuple(junk_patterns())

_PLAIN_WORD = re.compile(r"[\w\s'&.!$+-]+$")


def _literal_bracket_entry(entry: str) -> tuple[str, str, str] | None:
    """A user-friendly exact bracket tag such as ``(Original Mix)``.

    Parentheses normally mean regex grouping.  When they contain only a plain
    phrase, though, the settings UI's natural reading is an exact bracketed tag.
    Preserve actual regex syntax such as ``(mix|edit)`` for power users.
    """
    e = (entry or "").strip()
    if len(e) < 3 or (e[0], e[-1]) not in (
        ("(", ")"), ("[", "]"), ("{", "}"),
    ):
        return None
    inner = e[1:-1].strip()
    if not inner or not _PLAIN_WORD.fullmatch(inner):
        return None
    return e[0], inner, e[-1]


def is_plain_word(entry: str) -> bool:
    """True if a user entry is a plain word/phrase (gets safe end/bracket
    semantics); False means it will run as a raw regex."""
    return bool(_PLAIN_WORD.fullmatch((entry or "").strip()))


def is_literal_drop(entry: str) -> bool:
    """True for a safe plain phrase or an exact plain bracketed tag."""
    return is_plain_word(entry) or _literal_bracket_entry(entry) is not None


def valid_pattern(entry: str) -> bool:
    """Can this user entry actually be used? Literal drops always can; a regex
    entry must compile (invalid ones are rejected AT ENTRY so the user knows,
    instead of being silently skipped later)."""
    e = (entry or "").strip()
    if not e:
        return False
    if is_literal_drop(e):
        return True
    try:
        re.compile(e, re.IGNORECASE)
        return True
    except re.error:
        return False




@lru_cache(maxsize=8)
def _compiled_junk(junk: tuple):
    out = []
    for entry in junk:
        e = (entry or "").strip()
        if not e:
            continue
        bracketed = _literal_bracket_entry(e)
        if bracketed:                    # exact literal tag, incl. its brackets
            opening, phrase, closing = bracketed
            w = re.escape(phrase).replace(r"\ ", r"\s+")
            pat = (rf"{re.escape(opening)}\s*{w}\s*"
                   rf"{re.escape(closing)}")
        elif _PLAIN_WORD.fullmatch(e):   # plain word/phrase → safe semantics
            w = re.escape(e).replace(r"\ ", r"\s+")
            pat = (rf"(?:\s+{w})+\s*$|"
                   rf"(?:\(\s*{w}\s*\)|\[\s*{w}\s*\]|"
                   rf"\{{\s*{w}\s*\}})")
        else:                            # raw regex (power users)
            pat = e
        try:
            out.append(re.compile(pat, re.IGNORECASE))
        except re.error:
            continue                     # a bad pattern must never break the app
    return tuple(out)


def tidy_title(name: str, junk=None) -> str:
    """Strip the junk patterns from a DISPLAY/EXPORT title. `junk` = the user's
    pattern list (Settings), defaulting to DEFAULT_TITLE_JUNK. Applied repeatedly
    until stable (patterns can expose each other, e.g. "X FINAL V1 140"). Falls
    back to the original if stripping would empty it."""
    res = _compiled_junk(tuple(DEFAULT_TITLE_JUNK if junk is None else junk))
    s = name or ""
    for _ in range(4):
        before = s
        for rx in res:
            s = rx.sub(" ", s)
        s = re.sub(r"\s{2,}", " ", s).strip()
        s = re.sub(r"\s+([)\]}])", r"\1", s)
        if s == before:
            break
    out = s.strip(" -–—_")
    return out if len(out) >= 2 else (name or "").strip()


def effective_title(track_id: str, name: str, artist: str,
                    titles: "dict[str, str] | None" = None, junk=None, *, cleanup=True,
                    source_artist: str | None = None, registry=None) -> str:
    """The title to display/export: a manual override wins verbatim, else the
    raw name with any redundant edge-artist stripped and the dead-simple
    garbage tidied away."""
    if titles and track_id in titles and titles[track_id].strip():
        return titles[track_id].strip()
    if not cleanup:
        return name
    name, _primary = cleanup_credit_pair(name, artist if source_artist is None else source_artist,
                                       registry=registry)
    name = canonical_title_credits(name, registry)
    return tidy_title(neat_title(name, artist), junk)


def effective_artist(track_id: str, name: str, artist_tag: str,
                     overrides: dict | None = None, derive: bool = True,
                     registry: ArtistRegistry | FrozenArtistRegistry | None = None,
                     stem: str = "", *, cleanup=True) -> str:
    """The artist to actually use: a manual override wins, else auto-derivation
    (or the raw tag if derive is off). With a registry: every result — INCLUDING
    overrides — is canonicalized to the majority spelling, so one artist can
    never appear under two casings (the user's rule: most tracks wins; collab
    strings stay distinct). Unknowns are rescued from a known artist in the
    title, and a derived name that is NOT trusted flips to the right side of
    'TITLE - ARTIST' when THAT side IS trusted (tag/override-confirmed)."""
    cleanup = cleanup and (registry is None or registry.cleanup_enabled)
    if overrides and track_id in overrides and overrides[track_id].strip():
        ov = overrides[track_id].strip()
        return (registry.canonical(ov) if cleanup else registry.explicit_alias(ov)) if registry else ov
    if not cleanup:
        return registry.explicit_alias(artist_tag) if registry else artist_tag
    if derive or _FEATURE_MARKER.search(artist_tag):
        name, artist_tag = cleanup_credit_pair(name, artist_tag, registry=registry)
    base, origin = derive_artist(name, artist_tag) if derive else (artist_tag, TAG)
    if derive:
        _title, base = cleanup_credit_pair(name, base)
    if registry is None:
        return base
    if base and base != UNKNOWN_ARTIST:
        # Reversed-title flip, trusted-gated: only for names PARSED from the title
        # (never a rekordbox tag), only when the left side isn't itself trusted,
        # and only toward a human-confirmed artist. "I Wanna Rock - ZONDO final"
        # → ZONDO once ZONDO is tagged or overridden anywhere.
        if origin == NAME and not registry.is_trusted(base) and not editor_credits(name):
            flip = _flip_candidate(name)
            if flip and _fold(flip) != _fold(base) and registry.is_trusted(flip):
                return registry.canonical(flip)
        return registry.canonical(base)
    r = registry.rescue_from_title(name)
    if r:
        return r
    # Last resort: the source FILENAME often kept "Artist - Title" that the
    # rekordbox Title lost ("WOMANIZER" ← "WODD - WOMANIZER.wav"). Same rules,
    # one level deep. Measured 2026-07-02: +14 correct rescues on the real lib.
    if stem and _fold(stem) != _fold(name):
        s = effective_artist(track_id, stem, "", None, True, registry)
        if s != UNKNOWN_ARTIST:
            return s
    return base or UNKNOWN_ARTIST


def derive_artist(name: str, artist_tag: str) -> tuple[str, str]:
    """Return (artist, origin). See module docstring for strategy."""
    n = (name or "").strip()

    # 1) An explicit editor/remixer credit outranks the original-artist tag.
    # Take the LAST valid one (the most recent edit). This makes a tagged
    # "DON'T CHA (WHALES REMIX)" behave like an otherwise-identical untagged
    # edit instead of silently reverting to THE PUSSYCAT DOLLS.
    credits = editor_credits(n)
    if credits:
        return credits[-1][0], NAME

    # 2) No named transformation credit: keep the source artist tag.
    if artist_tag and artist_tag.strip():
        return artist_tag.strip(), TAG

    # 3) "Artist - Title" → before the first ' - '
    if " - " in n:
        left = n.split(" - ", 1)[0].strip()
        if left and len(left) <= _MAX_ARTIST_LEN and left[0] not in "([{":
            return left, NAME

    # 3.5) DJ hyphen styles: "CALCIUM- GUTTER" / "Arbityr -Badboy" /
    # "ABYSS_-_FEINT" / "Adele-Fire To The Rain" (see _split_hyphen_artist).
    if "-" in n:
        hy = _split_hyphen_artist(n)
        if hy:
            return hy, NAME

    return "Unknown Artist", UNKNOWN
