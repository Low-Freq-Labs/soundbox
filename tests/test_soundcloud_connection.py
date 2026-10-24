"""Offline OAuth, likes, credential privacy and connection-worker regressions."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import tempfile
from threading import Event, Thread
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.parse
import urllib.request

from soundbox_personal import connection as sc, discovery


class MemoryKeychain:
    def __init__(self, value=None):
        self.value = dict(value or {})
        self.writes = []

    def read(self):
        return dict(self.value)

    def write(self, value):
        self.value = dict(value)
        self.writes.append(dict(value))

    def delete(self):
        self.value = {}


def saved_tokens(**extra):
    return {"client_id": "private-id", "client_secret": "private-secret",
            "access_token": "private-access", "refresh_token": "private-refresh",
            "expires_at": time.time() + 3600, **extra}


def track(tid="one", title="Artist - Song", **extra):
    return {"id": tid, "title": title, "permalink_url": f"https://soundcloud.com/artist/{tid}",
            "user": {"username": "Artist"}, "artwork_url": "https://i1.sndcdn.com/cover-large.jpg",
            "metadata_artist": "Artist", **extra}


class SoundCloudConnection(unittest.TestCase):
    def setUp(self):
        self.area = tempfile.TemporaryDirectory()
        self.addCleanup(self.area.cleanup)
        self.store = MemoryKeychain(saved_tokens())
        self.connection = sc.SoundCloudConnection(Path(self.area.name) / "soundcloud", self.store)
        self.stop = Event()
        self.progress = []

    def _api(self, url, **kwargs):
        if url.endswith("/me"):
            return {"urn": "soundcloud:users:1", "username": "Sonny"}
        return {"collection": [track()]}

    def test_save_authorize_pkce_and_likes_without_secrets_in_files(self):
        calls = []

        def authorize(client_id, verifier, challenge, state, stop, browser):
            self.assertEqual(client_id, "private-id")
            expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            self.assertEqual(challenge, expected)
            self.assertGreaterEqual(len(state), 32)
            return "private-code"

        def api(url, **kwargs):
            calls.append((url, kwargs))
            if url.endswith("/oauth/token"):
                self.assertEqual(kwargs["form"]["redirect_uri"], sc.REDIRECT_URI)
                self.assertEqual(kwargs["form"]["code"], "private-code")
                return {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600}
            return self._api(url, **kwargs)

        with patch.object(self.connection, "_authorize", side_effect=authorize), \
                patch.object(sc, "_json_request", side_effect=api):
            self.connection.connect(" private-id ", "private-secret", self.stop,
                                    lambda url: None, self.progress.append)
        self.assertEqual(self.store.value["refresh_token"], "new-refresh")
        self.assertEqual(self.connection.status()["count"], 1)
        self.assertEqual(self.connection.status()["username"], "Sonny")
        for path in Path(self.area.name).rglob("*.json"):
            contents = path.read_text()
            for secret in ("private-id", "private-secret", "private-code", "new-access", "new-refresh"):
                self.assertNotIn(secret, contents)
        self.assertTrue(all("/likes/" in url or url.endswith(("/me", "/oauth/token")) for url, _ in calls))

    def test_pagination_and_wrapped_tracks_publish_complete_deduped_index(self):
        def api(url, **kwargs):
            if url.endswith("/me"):
                return {"id": 1, "username": "Sonny"}
            if "page=2" in url:
                return {"collection": [{"track": track("two")}, track()]}
            return {"collection": [track()], "next_href": "https://api.soundcloud.com/me/likes/tracks?page=2"}
        with patch.object(sc, "_json_request", side_effect=api):
            self.connection.refresh(self.stop, self.progress.append)
        self.assertEqual(self.connection.status()["count"], 2)
        self.assertEqual(len(self.connection.entries()), 2)
        self.assertEqual(len(json.loads((self.connection.root / "likes.json").read_text())["entries"]), 2)

    def test_failed_or_cancelled_partial_refresh_preserves_previous_same_account_index(self):
        with patch.object(sc, "_json_request", side_effect=self._api):
            self.connection.refresh(self.stop, self.progress.append)
        before = (self.connection.root / "likes.json").read_bytes()
        for cancel in (False, True):
            self.stop.clear()
            def api(url, **kwargs):
                if url.endswith("/me"):
                    return {"urn": "soundcloud:users:1", "username": "Sonny"}
                if "page=2" in url:
                    raise sc.ConnectionError("offline")
                if cancel:
                    self.stop.set()
                return {"collection": [track("new")], "next_href": "https://api.soundcloud.com/me/likes/tracks?page=2"}
            with self.subTest(cancel=cancel), patch.object(sc, "_json_request", side_effect=api):
                with self.assertRaises(sc.ConnectionError):
                    self.connection.refresh(self.stop, self.progress.append)
                self.assertEqual((self.connection.root / "likes.json").read_bytes(), before)
                self.assertEqual(self.connection.entries()[0]["id"], "one")

    def test_cross_account_failure_drops_previous_account_likes(self):
        with patch.object(sc, "_json_request", side_effect=self._api):
            self.connection.refresh(self.stop, self.progress.append)
        def api(url, **kwargs):
            if url.endswith("/me"):
                return {"id": 2, "username": "Other account"}
            raise sc.ConnectionError("offline")
        with patch.object(sc, "_json_request", side_effect=api), self.assertRaises(sc.ConnectionError):
            self.connection.refresh(self.stop, self.progress.append)
        self.assertEqual(self.connection.entries(), ())
        self.assertFalse((self.connection.root / "likes.json").exists())

    def test_pagination_never_sends_token_to_another_host_or_follows_a_loop(self):
        for next_url in ("https://example.invalid/steal", "https://api.soundcloud.com/me/likes/tracks?limit=200&linked_partitioning=true"):
            calls = []
            def api(url, **kwargs):
                calls.append(url)
                if url.endswith("/me"):
                    return {"id": 1, "username": "Sonny"}
                return {"collection": [], "next_href": next_url}
            with self.subTest(url=next_url), patch.object(sc, "_json_request", side_effect=api):
                with self.assertRaises(sc.ConnectionError):
                    self.connection.refresh(self.stop, self.progress.append)
                self.assertEqual(len(calls), 2)

    def test_refresh_rotation_is_saved_before_reading_likes_and_reused(self):
        self.store.value["expires_at"] = 0
        rotations = []
        def api(url, **kwargs):
            if url.endswith("/oauth/token"):
                rotations.append(kwargs["form"]["refresh_token"])
                return {"access_token": "rotated-access", "refresh_token": "rotated-refresh", "expires_in": 3600}
            self.assertEqual(self.store.value["refresh_token"], "rotated-refresh")
            return self._api(url, **kwargs)
        with patch.object(sc, "_json_request", side_effect=api):
            self.connection.refresh(self.stop, self.progress.append)
            self.connection.refresh(self.stop, self.progress.append)
        self.assertEqual(rotations, ["private-refresh"])

    def test_callback_rejects_wrong_state_and_accepts_exact_loopback_callback(self):
        results, errors, clients = [], [], []
        def browser(url):
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            def visit():
                try:
                    for state in ("wrong", query["state"][0]):
                        try:
                            with urllib.request.urlopen(sc.REDIRECT_URI + "?" + urllib.parse.urlencode(
                                    {"code": "private-code", "state": state}), timeout=3) as response:
                                results.append(response.read())
                        except urllib.error.HTTPError as exc:
                            errors.append(exc.code)
                            exc.close()
                except Exception as exc:
                    errors.append(type(exc).__name__)
            client = Thread(target=visit)
            clients.append(client)
            client.start()
        code = self.connection._authorize("id", "verifier", "challenge", "correct-state", self.stop, browser)
        for client in clients:
            client.join(4)
            self.assertFalse(client.is_alive())
        self.assertEqual(code, "private-code")
        self.assertEqual(errors, [400])
        self.assertEqual(len(results), 1)

    def test_cancelled_sign_in_releases_callback_socket(self):
        def browser(url):
            self.stop.set()
        with self.assertRaises(sc.Cancelled):
            self.connection._authorize("id", "verifier", "challenge", "state", self.stop, browser)
        self.stop.clear()
        # Binding the callback again proves cancellation closed the listener.
        with sc.HTTPServer(("127.0.0.1", 53927), sc.BaseHTTPRequestHandler):
            pass

    def test_disconnect_revokes_application_only_and_removes_local_credentials(self):
        with patch.object(sc, "_json_request", side_effect=self._api):
            self.connection.refresh(self.stop, self.progress.append)
        with patch.object(sc, "_json_request", return_value={}) as request:
            self.connection.disconnect(self.stop, self.progress.append)
        request.assert_called_once_with("https://api.soundcloud.com/disconnect", token="private-access", post=True)
        self.assertEqual(self.store.value, {})
        self.assertEqual(self.connection.entries(), ())
        self.assertFalse(self.connection.status()["configured"])

    def test_likes_precede_general_search_without_overriding_version_checks(self):
        liked = sc._track_entry(track(title="Song", metadata_artist="Artist"))
        with patch.object(sc, "liked_entries", return_value=(liked,)), \
                patch.object(discovery, "_flat_search", side_effect=AssertionError("general search")), \
                patch.object(discovery, "_download", return_value=(b"cover", "image/jpeg")):
            result = discovery.fetch_best("Artist", "Artist - Song")
            self.assertEqual(result.source_url, liked["webpage_url"])
            self.assertTrue(discovery.search_candidates("Artist", "Artist - Song")[0]["liked"])
        vip = {**liked, "title": "Song VIP"}
        with patch.object(sc, "liked_entries", return_value=(vip,)):
            self.assertEqual(discovery._liked_candidates("Artist", "Artist - Song"), [])
