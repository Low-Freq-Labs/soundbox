"""Personal SoundCloud OAuth and a disposable liked-track metadata index.

Credentials/tokens live only in the login Keychain. No audio or account mutations
are requested; disconnect revokes only this application's OAuth authorization.
"""
from __future__ import annotations

import base64
import ctypes
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
import threading
import time
from typing import Callable
import urllib.error
import urllib.parse
import urllib.request

from soundbox.providers import ConnectionFailure

REDIRECT_URI = "http://127.0.0.1:53927/soundcloud/callback"
_API = "https://api.soundcloud.com"
_AUTH = "https://secure.soundcloud.com"
_service: SoundCloudConnection | None = None


class ConnectionError(ConnectionFailure):
    """Safe, user-facing errors; never include request bodies or token URLs."""
    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status


class Cancelled(ConnectionError):
    pass


def _check(stop: threading.Event) -> None:
    if stop.is_set():
        raise Cancelled("Cancelled")


class Keychain:
    """Generic-password API avoids sending credentials through subprocess argv."""
    def __init__(self, account: str):
        self.account = account.encode()
        self.service = b"com.lowfreqlabs.soundbox.soundcloud"

    def _libraries(self):
        security = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
        core = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        p, n = ctypes.c_void_p, ctypes.c_uint32
        security.SecKeychainFindGenericPassword.argtypes = [p, n, p, n, p,
                                                          ctypes.POINTER(n), ctypes.POINTER(p), ctypes.POINTER(p)]
        security.SecKeychainAddGenericPassword.argtypes = [p, n, p, n, p, n, p, p]
        security.SecKeychainItemModifyAttributesAndData.argtypes = [p, p, n, p]
        security.SecKeychainItemFreeContent.argtypes = [p, p]
        security.SecKeychainItemDelete.argtypes = [p]
        core.CFRelease.argtypes = [p]
        core.CFRelease.restype = None
        return security, core

    def _find(self, security):
        length, data, item = ctypes.c_uint32(), ctypes.c_void_p(), ctypes.c_void_p()
        code = security.SecKeychainFindGenericPassword(
            None, len(self.service), self.service, len(self.account), self.account,
            ctypes.byref(length), ctypes.byref(data), ctypes.byref(item))
        if code not in (0, -25300):
            raise ConnectionError("SoundCloud credentials could not be accessed in macOS Keychain.")
        return code, length, data, item

    def read(self) -> dict:
        security, core = self._libraries()
        code, length, data, item = self._find(security)
        if code == -25300:
            return {}
        try:
            result = json.loads(ctypes.string_at(data, length.value))
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except (ValueError, UnicodeError):
            raise ConnectionError("Saved SoundCloud credentials are invalid. Edit them to reconnect.") from None
        finally:
            security.SecKeychainItemFreeContent(None, data)
            core.CFRelease(item)

    def write(self, value: dict) -> None:
        security, core = self._libraries()
        payload = json.dumps(value).encode()
        code, _length, data, item = self._find(security)
        if code == -25300:
            code = security.SecKeychainAddGenericPassword(
                None, len(self.service), self.service, len(self.account), self.account,
                len(payload), payload, None)
        else:
            try:
                code = security.SecKeychainItemModifyAttributesAndData(item, None, len(payload), payload)
            finally:
                security.SecKeychainItemFreeContent(None, data)
                core.CFRelease(item)
        if code:
            raise ConnectionError("SoundCloud credentials could not be saved in macOS Keychain.")

    def delete(self) -> None:
        security, core = self._libraries()
        code, _length, data, item = self._find(security)
        if code == -25300:
            return
        try:
            code = security.SecKeychainItemDelete(item)
        finally:
            security.SecKeychainItemFreeContent(None, data)
            core.CFRelease(item)
        if code:
            raise ConnectionError("SoundCloud credentials could not be removed from macOS Keychain.")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ConnectionError("SoundCloud returned an unexpected redirect. Try reconnecting.")


def _json_request(url: str, *, token: str = "", form: dict | None = None,
                  post: bool = False) -> dict:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc not in ("api.soundcloud.com", "secure.soundcloud.com"):
        raise ConnectionError("SoundCloud returned an invalid API address.")
    headers = {"Accept": "application/json", "User-Agent": "SoundBox/1.0"}
    data = None
    if token:
        headers["Authorization"] = "OAuth " + token
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif post:
        data = b""
    try:
        with urllib.request.build_opener(_NoRedirect).open(
                urllib.request.Request(url, data=data, headers=headers), timeout=15) as response:
            raw = response.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise ConnectionError("SoundCloud's response was too large.")
        result = json.loads(raw) if raw else {}
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except urllib.error.HTTPError as exc:
        messages = {401: "SoundCloud authorization expired. Connect again.",
                    429: "SoundCloud is rate limiting requests. Try refreshing likes later."}
        message = messages.get(exc.code, f"SoundCloud request failed (HTTP {exc.code}).")
        exc.close()
        raise ConnectionError(message, status=exc.code) from None
    except (OSError, ValueError):
        raise ConnectionError("Could not reach SoundCloud. Check your connection and try again.") from None


def _publish(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or path.parent.is_symlink():
        raise ConnectionError("SoundCloud connection state cannot use symlinks.")
    temporary = path.with_name(path.name + "." + secrets.token_hex(8) + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            os.chmod(temporary, 0o600)
            json.dump(value, stream)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read(path: Path) -> dict:
    try:
        if path.stat().st_size > 32 * 1024 * 1024:
            return {}
        result = json.loads(path.read_text())
        return result if isinstance(result, dict) else {}
    except (OSError, ValueError):
        return {}


class SoundCloudConnection:
    def __init__(self, root: Path, store=None):
        self.root = root
        self.store = store if store is not None else Keychain(hashlib.sha256(str(root.resolve()).encode()).hexdigest())
        self._state = _read(root / "connection.json")
        self._entries: tuple[dict, ...] = ()
        self._lock = threading.RLock()
        self._operations = threading.Lock()

    def status(self) -> dict:
        with self._lock:
            return dict(self._state)

    def entries(self) -> tuple[dict, ...]:
        with self._lock:
            return self._entries

    def credentials(self) -> tuple[str, str]:
        saved = self.store.read()
        return str(saved.get("client_id", "")), str(saved.get("client_secret", ""))

    def _set_state(self, **state) -> None:
        _publish(self.root / "connection.json", {"schema": 1, **state})
        with self._lock:
            self._state = {"schema": 1, **state}

    def _tokens(self, saved: dict, response: dict) -> dict:
        access, refresh = response.get("access_token"), response.get("refresh_token")
        if not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh:
            raise ConnectionError("SoundCloud did not return usable authorization. Connect again.")
        result = {**saved, "access_token": access, "refresh_token": refresh,
                  "expires_at": time.time() + float(response.get("expires_in", 3600))}
        self.store.write(result)  # rotating refresh tokens must persist immediately
        return result

    def connect(self, client_id: str, client_secret: str, stop: threading.Event,
                browser: Callable[[str], None], progress: Callable[[str], None]) -> None:
        with self._operations:
            _check(stop)
            if not client_id.strip() or not client_secret.strip():
                raise ConnectionError("Enter both the Client ID and Client Secret.")
            saved = {"client_id": client_id.strip(), "client_secret": client_secret.strip()}
            self.store.write(saved)
            self._set_state(configured=True, connected=False)
            with self._lock:
                self._entries = ()
            (self.root / "likes.json").unlink(missing_ok=True)
            progress("Saved. Finish signing in with SoundCloud in your browser…")
            verifier = secrets.token_urlsafe(48)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            state = secrets.token_urlsafe(32)
            code = self._authorize(client_id.strip(), verifier, challenge, state, stop, browser)
            _check(stop)
            response = _json_request(_AUTH + "/oauth/token", form={
                **saved, "grant_type": "authorization_code", "code": code,
                "redirect_uri": REDIRECT_URI, "code_verifier": verifier})
            saved = self._tokens(saved, response)
            _check(stop)
            self._refresh(saved, stop, progress)

    def _authorize(self, client_id, verifier, challenge, state, stop, browser) -> str:
        result: dict[str, str] = {}

        class Callback(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass  # request path contains authorization codes

            def setup(self):
                super().setup()
                self.connection.settimeout(1)

            def do_GET(self):
                url = urllib.parse.urlsplit(self.path)
                values = urllib.parse.parse_qs(url.query)
                valid = (url.path == "/soundcloud/callback"
                         and self.headers.get("Host") == "127.0.0.1:53927"
                         and values.get("state") == [state])
                if not valid:
                    self.send_error(400, "Invalid SoundCloud callback")
                    return
                if values.get("error"):
                    result["error"] = "SoundCloud authorization was declined."
                elif len(values.get("code", [])) == 1 and values["code"][0]:
                    result["code"] = values["code"][0]
                else:
                    self.send_error(400, "Missing authorization code")
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.end_headers()
                self.wfile.write(b"Return to SoundBox to finish connecting. You can close this tab.")

        try:
            server = HTTPServer(("127.0.0.1", 53927), Callback)
        except OSError:
            raise ConnectionError("SoundCloud sign-in callback is busy. Close another sign-in and try again.") from None
        with server:
            server.timeout = 0.25
            browser(_AUTH + "/authorize?" + urllib.parse.urlencode({
                "client_id": client_id, "redirect_uri": REDIRECT_URI,
                "response_type": "code", "code_challenge": challenge,
                "code_challenge_method": "S256", "state": state}))
            deadline = time.monotonic() + 180
            while not result:
                _check(stop)
                if time.monotonic() >= deadline:
                    raise ConnectionError("SoundCloud sign-in timed out. Your credentials are saved; connect again.")
                server.handle_request()
        if "error" in result:
            raise ConnectionError(result["error"])
        return result["code"]

    def refresh(self, stop: threading.Event, progress: Callable[[str], None]) -> None:
        with self._operations:
            _check(stop)
            cached = _read(self.root / "likes.json")
            if (cached.get("schema") == 1 and cached.get("user_id") == self.status().get("user_id")
                    and self.status().get("connected") and isinstance(cached.get("entries"), list)):
                with self._lock:
                    self._entries = tuple(e for e in cached["entries"] if isinstance(e, dict))
            saved = self.store.read()
            if not saved.get("refresh_token"):
                raise ConnectionError("Your credentials are saved. Connect to authorize access to your likes.")
            if float(saved.get("expires_at", 0)) <= time.time() + 60:
                saved = self._tokens(saved, _json_request(_AUTH + "/oauth/token", form={
                    "grant_type": "refresh_token", "client_id": saved["client_id"],
                    "client_secret": saved["client_secret"], "refresh_token": saved["refresh_token"]}))
            _check(stop)
            self._refresh(saved, stop, progress)

    def _refresh(self, saved: dict, stop: threading.Event, progress: Callable[[str], None]) -> None:
        token = saved["access_token"]
        user = _json_request(_API + "/me", token=token)
        _check(stop)
        user_id = str(user.get("urn") or user.get("id") or "")
        if not user_id:
            raise ConnectionError("SoundCloud did not identify the connected account.")
        # Never carry likes across account changes, even if the new scan fails.
        if self.status().get("user_id") != user_id:
            with self._lock:
                self._entries = ()
            (self.root / "likes.json").unlink(missing_ok=True)
        self._set_state(configured=True, connected=True, user_id=user_id,
                        username=str(user.get("username") or "SoundCloud"),
                        count=len(self.entries()), updated=self.status().get("updated", 0))
        url = _API + "/me/likes/tracks?limit=200&linked_partitioning=true"
        entries, visited = {}, set()
        while url:
            _check(stop)
            parsed = urllib.parse.urlsplit(url)
            if parsed.scheme != "https" or parsed.netloc != "api.soundcloud.com" or url in visited:
                raise ConnectionError("SoundCloud returned an invalid likes page.")
            visited.add(url)
            page = _json_request(url, token=token)
            collection = page.get("collection")
            if not isinstance(collection, list):
                raise ConnectionError("SoundCloud returned an invalid likes list.")
            for item in collection:
                track = item.get("track", item) if isinstance(item, dict) else {}
                entry = _track_entry(track)
                if entry:
                    entries[entry["id"]] = entry
            progress(f"Reading SoundCloud likes… {len(entries):,} tracks")
            url = page.get("next_href") or ""
            if not isinstance(url, str) or (len(visited) >= 500 and url):
                raise ConnectionError("SoundCloud likes scan exceeded its page limit. Previous index retained.")
        _check(stop)
        snapshot = {"schema": 1, "user_id": user_id, "entries": list(entries.values())}
        _publish(self.root / "likes.json", snapshot)
        with self._lock:
            self._entries = tuple(entries.values())
        self._set_state(configured=True, connected=True, user_id=user_id,
                        username=str(user.get("username") or "SoundCloud"),
                        count=len(entries), updated=time.time())

    def disconnect(self, stop: threading.Event, progress: Callable[[str], None]) -> None:
        with self._operations:
            _check(stop)
            saved = self.store.read()
            if saved.get("access_token"):
                progress("Disconnecting SoundCloud…")
                try:
                    _json_request(_API + "/disconnect", token=saved["access_token"], post=True)
                except ConnectionError as exc:
                    if exc.status != 401:  # an already-invalid token needs only local cleanup
                        raise ConnectionError("Could not revoke SoundCloud access. Try disconnecting again when online.") from None
            self.store.delete()
            self._set_state(configured=False, connected=False)
            with self._lock:
                self._entries = ()
            (self.root / "likes.json").unlink(missing_ok=True)


def _track_entry(track: dict) -> dict | None:
    if not isinstance(track, dict):
        return None
    user = track.get("user") or {}
    if not isinstance(user, dict):
        return None
    title, url = track.get("title"), track.get("permalink_url")
    thumb = track.get("artwork_url")
    key = str(track.get("urn") or track.get("id") or "")
    if not (isinstance(title, str) and isinstance(url, str) and isinstance(thumb, str) and key):
        return None
    return {"id": key, "title": title, "webpage_url": url,
            "thumbnail": thumb.replace("-large.", "-t500x500."),
            "uploader": str(user.get("username") or ""),
            "artists": [str(track.get("metadata_artist") or "")]}


def create(root: Path) -> SoundCloudConnection:
    global _service
    _service = SoundCloudConnection(root / "connections" / "soundcloud")
    return _service


def liked_entries() -> tuple[dict, ...]:
    return _service.entries() if _service else ()
