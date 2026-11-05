"""Offline OAuth, likes, credential privacy and connection-worker regressions."""
from __future__ import annotations

import base64
import hashlib
import io
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
        with patch.object(sc, "liked_entries", return_value=(liked,)), \
                patch.object(discovery, "_flat_search", return_value=[{
                    **track("general", "Song"), "webpage_url": "https://soundcloud.com/artist/general",
                    "uploader": "Artist", "thumbnail": "https://i1.sndcdn.com/general.jpg"}]) as search:
            candidates = discovery.search_candidates("Artist", "Artist - Song")
            search.assert_called()
            self.assertEqual(len(candidates), 2)
            self.assertTrue(candidates[0]["liked"])
            self.assertEqual(candidates[1]["url"], "https://soundcloud.com/artist/general")
        with patch.object(sc, "liked_entries", return_value=(liked,)), \
                patch.object(discovery, "_flat_search", side_effect=RuntimeError("search offline")):
            candidates = discovery.search_candidates("Artist", "Artist - Song")
            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0]["url"], liked["webpage_url"])
        vip = {**liked, "title": "Song VIP"}
        with patch.object(sc, "liked_entries", return_value=(vip,)):
            self.assertEqual(discovery._liked_candidates("Artist", "Artist - Song"), [])

    def test_public_likes_use_client_credentials_without_callback_or_me_and_reuse_tokens(self):
        calls = []
        def api(url, **kwargs):
            calls.append((url, kwargs))
            if url.endswith("/oauth/token"):
                self.assertEqual(kwargs["basic"], ("public-id", "public-secret"))
                self.assertEqual(kwargs["form"], {"grant_type": "client_credentials"})
                self.assertNotIn("redirect_uri", kwargs["form"])
                return {"access_token": "app-access", "refresh_token": "app-refresh", "expires_in": 3600}
            self.assertEqual(kwargs["token"], "app-access")
            if "/resolve?" in url:
                self.assertEqual(urllib.parse.parse_qs(urllib.parse.urlsplit(url).query),
                                 {"url": ["https://soundcloud.com/test-user"]})
                return {"kind": "user", "urn": "soundcloud:users:42", "username": "Test user"}
            self.assertIn("/users/soundcloud%3Ausers%3A42/likes/tracks?", url)
            return {"collection": [track()]}
        with patch.object(sc, "_json_request", side_effect=api), \
                patch.object(self.connection, "_authorize", side_effect=AssertionError("browser authorization")):
            for _ in range(2):
                self.connection.connect_public("public-id", "public-secret", "https://soundcloud.com/test-user",
                                               self.stop, self.progress.append)
            self.connection.refresh(self.stop, self.progress.append)
        self.assertEqual(sum(url.endswith("/oauth/token") for url, _ in calls), 1)
        self.assertEqual(self.connection.credentials(), ("public-id", "public-secret", "https://soundcloud.com/test-user"))
        self.assertEqual(self.connection.status()["mode"], "public")
        self.assertEqual(len(self.connection.entries()), 1)
        for path in self.connection.root.rglob("*.json"):
            for secret in ("public-id", "public-secret", "app-access", "app-refresh"):
                self.assertNotIn(secret, path.read_text())
        with patch.object(sc, "_json_request", side_effect=AssertionError("must not revoke website tokens")):
            self.connection.disconnect(self.stop, self.progress.append)
        self.assertEqual(self.store.value, {})

    def test_public_likes_refresh_rotation_persists_before_scan(self):
        self.store.value.update(mode="public", public_profile="https://soundcloud.com/test-user", expires_at=0)
        def api(url, **kwargs):
            if url.endswith("/oauth/token"):
                self.assertEqual(kwargs["form"]["grant_type"], "refresh_token")
                return {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600}
            self.assertEqual(self.store.value["refresh_token"], "new-refresh")
            if "/resolve?" in url:
                return {"kind": "user", "id": 42, "username": "Test user"}
            return {"collection": [track()]}
        with patch.object(sc, "_json_request", side_effect=api):
            self.connection.refresh(self.stop, self.progress.append)

    def test_public_application_token_without_refresh_token_reuses_until_expiry(self):
        self.store.value.update(mode="public", public_profile="https://soundcloud.com/test-user",
                                access_token="valid-app-token", expires_at=time.time()+3600)
        self.store.value.pop("refresh_token", None)
        with patch.object(sc, "_json_request") as request:
            self.assertEqual(self.connection._ready_tokens(self.store.value, self.stop)["access_token"],
                             "valid-app-token")
            request.assert_not_called()
            self.store.value["expires_at"] = 0
            request.return_value = {"access_token": "renewed-app-token", "refresh_token": "renewed-refresh", "expires_in": 3600}
            renewed = self.connection._ready_tokens(self.store.value, self.stop)
            self.assertEqual(renewed["access_token"], "renewed-app-token")
            self.assertEqual(request.call_args.kwargs["form"], {"grant_type": "client_credentials"})

    def test_public_profile_validation_fails_before_credentials_or_network_change(self):
        before = dict(self.store.value)
        for profile in ("", "http://soundcloud.com/user", "https://example.invalid/user",
                        "https://soundcloud.com/user/likes", "https://soundcloud.com/user?token=secret",
                        "https://soundcloud.com@evil.invalid/user", "https://soundcloud.com/user#fragment"):
            with self.subTest(profile=profile), patch.object(sc, "_json_request") as request:
                with self.assertRaises(sc.ConnectionError):
                    self.connection.connect_public("id", "secret", profile, self.stop, self.progress.append)
                request.assert_not_called()
                self.assertEqual(self.store.value, before)

    def test_public_profile_change_failure_discards_old_likes_but_keeps_reusable_app_token(self):
        self.store.value.update(mode="public", public_profile="https://soundcloud.com/old-user")
        with patch.object(sc, "_json_request", side_effect=lambda url, **kw:
                {"kind": "user", "id": 1, "username": "Old user"} if "/resolve?" in url else {"collection": [track()]}):
            self.connection.refresh(self.stop, self.progress.append)
        with patch.object(sc, "_json_request", side_effect=sc.ConnectionError("offline")), self.assertRaises(sc.ConnectionError):
            self.connection.connect_public("private-id", "private-secret", "https://soundcloud.com/new-user",
                                           self.stop, self.progress.append)
        self.assertEqual(self.connection.entries(), ())
        self.assertFalse(self.connection.status()["connected"])
        self.assertFalse((self.connection.root / "likes.json").exists())
        self.assertEqual(self.store.value["refresh_token"], "private-refresh")

    def test_public_cancel_and_invalid_resolved_resource_fail_closed(self):
        before = dict(self.store.value)
        self.stop.set()
        with patch.object(sc, "_json_request") as request, self.assertRaises(sc.Cancelled):
            self.connection.connect_public("id", "secret", "https://soundcloud.com/test-user", self.stop, self.progress.append)
        request.assert_not_called()
        self.assertEqual(self.store.value, before)
        self.stop.clear()
        self.store.value.update(mode="public", public_profile="https://soundcloud.com/test-user")
        with patch.object(sc, "_json_request", return_value={"kind": "track", "id": 42}) as request, \
                self.assertRaises(sc.ConnectionError):
            self.connection.refresh(self.stop, self.progress.append)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(self.connection.entries(), ())

    def test_basic_auth_and_resolve_redirect_only_forward_to_exact_api_user_endpoint(self):
        from email.message import Message
        from unittest.mock import MagicMock
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"kind":"user","id":42}'
        for location, allowed in (("https://api.soundcloud.com/users/42", True),
                                  ("https://evil.invalid/users/42", False),
                                  ("http://api.soundcloud.com/users/42", False),
                                  ("https://api.soundcloud.com/tracks/42", False),
                                  ("https://api.soundcloud.com/users/42?token=x", False)):
            headers = Message()
            headers["Location"] = location
            redirect = urllib.error.HTTPError("https://api.soundcloud.com/resolve", 302, "Found", headers, io.BytesIO())
            opener = MagicMock()
            opener.open.side_effect = [redirect, response]
            with self.subTest(location=location), patch.object(sc.urllib.request, "build_opener", return_value=opener):
                if allowed:
                    self.assertEqual(sc._json_request("https://api.soundcloud.com/resolve?url=profile", token="secret-token",
                                                      resolve_user=True)["id"], 42)
                    self.assertEqual(opener.open.call_count, 2)
                    request = opener.open.call_args.args[0]
                    self.assertEqual(request.full_url, location)
                    self.assertEqual(request.get_header("Authorization"), "OAuth secret-token")
                else:
                    with self.assertRaises(sc.ConnectionError):
                        sc._json_request("https://api.soundcloud.com/resolve?url=profile", token="secret-token", resolve_user=True)
                    self.assertEqual(opener.open.call_count, 1)
        opener = MagicMock()
        opener.open.return_value = response
        with patch.object(sc.urllib.request, "build_opener", return_value=opener):
            sc._json_request("https://secure.soundcloud.com/oauth/token", form={"grant_type": "client_credentials"},
                             basic=("id", "secret"))
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Basic " + base64.b64encode(b"id:secret").decode())
        self.assertEqual(request.data, b"grant_type=client_credentials")

    def test_unmatched_or_ambiguous_likes_fall_back_to_general_art_search(self):
        for candidates in ([], [{"score": 0.99}, {"score": 0.98}]):
            with self.subTest(candidates=candidates), \
                    patch.object(discovery, "_liked_candidates", return_value=candidates), \
                    patch.object(discovery, "fetch_by_search", return_value="general art") as search:
                self.assertEqual(discovery.fetch_best("Artist", "Song"), "general art")
                search.assert_called_once_with("Artist", "Song")


class CachedLikesDemand(unittest.TestCase):
    def setUp(self):
        self.area = tempfile.TemporaryDirectory()
        self.addCleanup(self.area.cleanup)
        self.root = Path(self.area.name)
        self.addCleanup(patch.stopall)
        patch.object(sc, '_configured_root', None).start()
        patch.object(sc, '_service', None).start()
        patch.object(sc.Keychain, '_libraries', side_effect=AssertionError('Keychain')).start()
        patch.object(sc, '_json_request', side_effect=AssertionError('network')).start()

    def _cached(self, *, state_user='user:1', cache_user='user:1', connected=True, entries=None):
        root = self.root / 'connections' / 'soundcloud'
        root.mkdir(parents=True)
        (root / 'connection.json').write_text(json.dumps({
            'schema': 1, 'connected': connected, 'user_id': state_user}))
        (root / 'likes.json').write_text(json.dumps({
            'schema': 1, 'user_id': cache_user,
            'entries': [sc._track_entry(track(title='Song'))] if entries is None else entries}))

    def test_bind_has_no_io_then_first_art_demand_uses_cached_likes_and_shared_service(self):
        self._cached()
        with patch.object(sc, '_read', side_effect=AssertionError('startup read')):
            sc.bind_root(self.root)
            self.assertIsNone(sc._service)
        with patch.object(discovery, '_flat_search', side_effect=AssertionError('general search')), \
                patch.object(discovery, '_download', return_value=(b'cover', 'image/jpeg')):
            result = discovery.fetch_best('Artist', 'Artist - Song')
        service = sc._service
        self.assertEqual(result.source_url, 'https://soundcloud.com/artist/one')
        self.assertIs(sc.create(self.root), service)
        self.assertEqual(len(service.entries()), 1)
        sc.bind_root(self.root / 'other')
        self.assertIsNone(sc._service)
        self.assertEqual(sc.liked_entries(), ())

    def test_other_account_disconnected_or_invalid_rows_do_not_enter_matching(self):
        cases = ({'cache_user': 'user:other'}, {'connected': False},
                 {'entries': [{'id': 'broken'}, None, {'id': [], 'title': 'bad'}]})
        for i, kwargs in enumerate(cases):
            with self.subTest(case=kwargs):
                self.root = Path(self.area.name) / str(i)
                self._cached(**kwargs)
                sc.bind_root(self.root)
                self.assertEqual(sc.liked_entries(), ())

    def test_parallel_first_demand_constructs_only_one_service(self):
        self._cached()
        sc.bind_root(self.root)
        constructor = sc.SoundCloudConnection
        with patch.object(sc, 'SoundCloudConnection', wraps=constructor) as create:
            results = []
            threads = [Thread(target=lambda: results.append(sc.liked_entries())) for _ in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(2)
                self.assertFalse(thread.is_alive())
            self.assertEqual(create.call_count, 1)
            self.assertEqual(len(results), 8)
            self.assertTrue(all(len(result) == 1 for result in results))

    def test_initial_refresh_is_shared_serialized_and_explicit_refresh_remains_repeatable(self):
        self._cached()
        service = sc.create(self.root)
        service.store = MemoryKeychain(saved_tokens())
        calls = []
        def api(url, **_kwargs):
            calls.append(url)
            if url.endswith('/me'):
                return {'id': 'user:1', 'username': 'Fixture user'}
            return {'collection': [track()]}
        with patch.object(sc, '_json_request', side_effect=api):
            workers = [Thread(target=lambda: service.refresh_initial(Event(), lambda _: None)) for _ in range(2)]
            for worker in workers:worker.start()
            for worker in workers:
                worker.join(2)
                self.assertFalse(worker.is_alive())
            self.assertEqual(sum(url.endswith('/me') for url in calls), 1)
            service.refresh(Event(), lambda _: None)
            self.assertEqual(sum(url.endswith('/me') for url in calls), 2)

    def test_cancelled_initial_admission_and_failed_refresh_keep_explicit_retry(self):
        self._cached()
        service = sc.create(self.root)
        service.store = MemoryKeychain(saved_tokens())
        stop = Event();stop.set()
        with self.assertRaises(sc.Cancelled):service.refresh_initial(stop, lambda _: None)
        stop.clear()
        with patch.object(sc, '_json_request', side_effect=sc.ConnectionError('offline')) as api:
            with self.assertRaises(sc.ConnectionError):service.refresh_initial(stop, lambda _: None)
            service.refresh_initial(stop, lambda _: None)
            self.assertEqual(api.call_count, 1)
            with self.assertRaises(sc.ConnectionError):service.refresh(stop, lambda _: None)
            self.assertEqual(api.call_count, 2)
        self.assertEqual(len(service.entries()), 1)

    def test_disconnected_initial_refresh_never_reads_credentials(self):
        sc.bind_root(self.root)
        service = sc.create(self.root)
        service.refresh_initial(Event(), lambda _: None)


class MetadataRequestCancellation(unittest.TestCase):
    class Response:
        def __init__(self, data, *, on_read=None, whole=False):
            self.stream = io.BytesIO(data)
            self.on_read = on_read
            self.whole = whole
            self.reads = []
            self.closed = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.closed = True

        def read(self, size):
            if not self.whole:
                raise AssertionError('uncancellable whole metadata read')
            data = self.stream.read(size)
            if self.on_read:
                self.on_read()
            return data

        def read1(self, size):
            self.reads.append(size)
            data = self.stream.read(size)
            if self.on_read:
                self.on_read()
            return data

    def test_trickle_get_cancels_after_one_receive_and_closes_response(self):
        from unittest.mock import Mock
        stop = Event()
        response = self.Response(b'{"collection":', on_read=stop.set)
        opener = Mock()
        opener.open.return_value = response
        with patch.object(sc.urllib.request, 'build_opener', return_value=opener):
            with self.assertRaises(sc.Cancelled):
                sc._json_request('https://api.soundcloud.com/me/likes/tracks', stop=stop)
        self.assertEqual(response.reads, [64 * 1024])
        self.assertTrue(response.closed)

    def test_get_size_json_and_http_error_guards_remain_sanitized(self):
        from unittest.mock import Mock
        for payload, message in ((b' ' * (4 * 1024 * 1024 + 1), 'too large'),
                                 (b'{private-response', 'Could not reach'),
                                 (b'[]', 'Could not reach')):
            response = self.Response(payload)
            opener = Mock()
            opener.open.return_value = response
            with self.subTest(message=message), patch.object(sc.urllib.request, 'build_opener', return_value=opener):
                with self.assertRaisesRegex(sc.ConnectionError, message) as error:
                    sc._json_request('https://api.soundcloud.com/me', stop=Event())
                self.assertNotIn('private-response', str(error.exception))
            self.assertTrue(response.closed)
            self.assertLessEqual(sum(response.reads), 4 * 1024 * 1024 + 64 * 1024)
        opener = Mock()
        body = io.BytesIO(b'private-server-body')
        opener.open.side_effect = urllib.error.HTTPError(
            'https://api.soundcloud.com/me', 503, 'private-token', {}, body)
        with patch.object(sc.urllib.request, 'build_opener', return_value=opener):
            with self.assertRaises(sc.ConnectionError) as error:
                sc._json_request('https://api.soundcloud.com/me', stop=Event())
        self.assertEqual(error.exception.status, 503)
        self.assertNotIn('private-token', str(error.exception))
        self.assertTrue(body.closed)

    def test_resolve_redirect_carries_get_cancellation(self):
        from unittest.mock import Mock
        stop = Event()
        response = self.Response(b'{"kind":"user"}', on_read=stop.set)
        opener = Mock()
        opener.open.side_effect = [urllib.error.HTTPError(
            'https://api.soundcloud.com/resolve', 302, 'Found',
            {'Location': 'https://api.soundcloud.com/users/42'}, io.BytesIO()), response]
        with patch.object(sc.urllib.request, 'build_opener', return_value=opener):
            with self.assertRaises(sc.Cancelled):
                sc._json_request('https://api.soundcloud.com/resolve?url=profile',
                                 resolve_user=True, stop=stop)
        self.assertEqual(opener.open.call_count, 2)
        self.assertTrue(response.closed)

    def test_auth_response_finishes_and_rotating_tokens_persist_before_cancel(self):
        from unittest.mock import Mock
        stop = Event()
        response = self.Response(json.dumps(dict(access_token='new-private-access',
                                                  refresh_token='new-private-refresh',
                                                  expires_in=3600)).encode(),
                                 on_read=stop.set, whole=True)
        opener = Mock()
        opener.open.return_value = response
        store = MemoryKeychain(saved_tokens(expires_at=0))
        with tempfile.TemporaryDirectory() as area, \
                patch.object(sc.urllib.request, 'build_opener', return_value=opener):
            connection = sc.SoundCloudConnection(Path(area), store)
            with self.assertRaises(sc.Cancelled):
                connection.refresh(stop, lambda *_args: None)
            self.assertFalse((Path(area)/'likes.json').exists())
        self.assertEqual(store.value['refresh_token'], 'new-private-refresh')
        self.assertEqual(store.value['access_token'], 'new-private-access')
        self.assertEqual(response.reads, [])
        self.assertTrue(response.closed)
        self.assertEqual(opener.open.call_count, 1)
