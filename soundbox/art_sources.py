"""Local artwork source preferences; pure rules, no provider/network dependency."""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from urllib.parse import unquote, urlsplit


def same_art_source(first: str, second: str) -> bool:
    """Tracking links and mobile/web aliases still identify the same SC upload.

    Other providers retain their exact query/path semantics. This only suppresses
    redundant review suggestions; it never establishes publisher trust.
    """
    def key(url):
        try:
            parsed = urlsplit(url)
            if parsed.hostname in ("soundcloud.com", "www.soundcloud.com", "m.soundcloud.com"):
                return ("soundcloud", unquote(parsed.path).strip("/").casefold())
        except ValueError:
            pass
        return url.rstrip("/")
    return bool(first and second and key(first) == key(second))


def account_name(url: str) -> str:
    """The uploader handle, never a song title that happens to mention sellers."""
    if not isinstance(url, str):
        return ""
    try:
        parsed = urlsplit(url)
        if parsed.hostname not in ("soundcloud.com", "www.soundcloud.com", "m.soundcloud.com"):
            return ""
        return unquote(parsed.path).strip("/").split("/", 1)[0].casefold()
    except ValueError:
        return ""


def profile_source(url: str) -> bool:
    """An actual SoundCloud account URL, rather than a track or service page."""
    return bool(account_name(url) and len(unquote(urlsplit(url).path).strip("/").split("/")) == 1 and
                account_name(url) not in {"search", "discover", "stream", "you", "upload", "charts", "tags"})


def _compact(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text).casefold()
                   if c.isalnum())


def parse_exclusions(raw: object) -> tuple[str, ...]:
    if (not isinstance(raw, dict) or raw.get("schema") != 1 or not isinstance(raw.get("accounts"), list) or
            any(not isinstance(name, str) or not name for name in raw["accounts"])):
        raise ValueError("Invalid artwork source exclusions")
    return tuple(raw["accounts"])


_PRODUCT = r"(?:dubplates?|plates?|dubs?|dubz)"
_ROLE = r"(?:resellers?|dealers?|sellers?|selling|resales?|shops?|stores?|market(?:place)?s?|traders?|trading|sales)"
_SELLER = re.compile(_PRODUCT + _ROLE + "|" + _ROLE + _PRODUCT)


def excluded_source(uploader: str = "", url: str = "", *,
                    uploader_url: str = "", excluded_accounts: Iterable[str] = ()) -> bool:
    """Exclude named seller/dealer sources and accounts the user has excluded.

    This is a source preference, not an allegation or a classifier for all
    resellers. 'Dubplate' alone is not enough to exclude an artist or label.
    """
    handles = {account_name(url), account_name(uploader_url)} - {""}
    excluded = {name.casefold() for name in excluded_accounts}
    return bool(handles & excluded or any(
        _SELLER.search(_compact(name)) for name in (uploader, *handles) if name))
