"""Generated source identities only; no SoundCloud or live artwork access."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from soundbox.art_sources import excluded_source, same_art_source
from soundbox.artwork import ArtCache, ArtResult
from soundbox_personal import discovery as discovery


class SourceExclusions(unittest.TestCase):
    def test_artist_catalog_batches_status_and_refreshes_policy_for_sample_images(self):
        import json
        from types import SimpleNamespace
        from desktop.app import MainWindow
        from soundbox.library import Library, Track
        with tempfile.TemporaryDirectory() as area:
            cache = ArtCache(Path(area) / 'art')
            cache.index = {
                'first': {'status': 'ok', 'src': 'https://soundcloud.com/artist'},
                'second': {'status': 'ok', 'src': 'https://soundcloud.com/artist'},
                'excluded': {'status': 'ok', 'src': 'https://soundcloud.com/blocked'},
                'cover': {'status': 'ok', 'src': 'https://soundcloud.com/artist/song'},
            }
            (cache.root / 'source-policy.json').write_text(
                json.dumps({'schema': 1, 'accounts': ['blocked']}))
            tracks = {tid: Track(tid, 'Song', 'Artist', f'file:///missing/{tid}.wav')
                      for tid in cache.index}
            host = SimpleNamespace(lib=Library(tracks, [], 'synthetic'),
                                   art_cache=cache, titles={}, overrides={})
            with patch.object(cache, 'source_exclusions', wraps=cache.source_exclusions) as policy:
                groups = MainWindow._artist_avatar_groups(host)
            self.assertEqual(groups, [('Artist', ['first', 'second'], None)])
            # One status snapshot and a separate fresh check for the sample
            # image; grouping never grants image-read or write authority.
            self.assertEqual(policy.call_count, 2)
            (cache.root / 'source-policy.json').write_text(
                json.dumps({'schema': 1, 'accounts': ['artist', 'blocked']}))
            self.assertEqual(MainWindow._artist_avatar_groups(host), [])

    def test_batch_status_uses_one_policy_snapshot_and_notices_replacement(self):
        import json
        with tempfile.TemporaryDirectory() as area:
            cache = ArtCache(Path(area) / 'art')
            cache.index = {str(i): {'status': 'ok', 'src': 'https://soundcloud.com/artist/song'}
                           for i in range(100)}
            with patch.object(cache, 'source_exclusions', wraps=cache.source_exclusions) as policy:
                self.assertEqual(set(cache.status_kinds(cache.index).values()), {'cover'})
                policy.assert_called_once()
            path = cache.root / 'source-policy.json'
            path.write_text(json.dumps({'schema': 1, 'accounts': ['artist']}))
            self.assertEqual(set(cache.status_kinds(cache.index).values()), {'blocked'})
            path.unlink()
            self.assertEqual(set(cache.status_kinds(cache.index).values()), {'cover'})
            path.symlink_to(Path(area) / 'missing')
            with self.assertRaises(ValueError):
                cache.status_kinds(cache.index)

    def test_review_source_identity_ignores_soundcloud_tracking_but_not_different_uploads(self):
        source = "https://soundcloud.com/rissrossmusic/who-is-she"
        for alias in (source + "?utm_source=api&utm_campaign=social_sharing",
                      "http://m.soundcloud.com/rissrossmusic/who-is-she/#comments",
                      "https://www.soundcloud.com/RISSROSSMUSIC/who-is-she/"):
            self.assertTrue(same_art_source(source, alias))
        for other in ("https://soundcloud.com/rissrossmusic/another-song",
                      "https://soundcloud.com/other/who-is-she",
                      "https://soundcloud.com.evil.invalid/rissrossmusic/who-is-she", ""):
            self.assertFalse(same_art_source(source, other))
        self.assertFalse(same_art_source("", ""))
        self.assertFalse(same_art_source("https://images.invalid/art?id=1", "https://images.invalid/art?id=2"))

    def test_real_unicode_edition_is_valid_and_original_other_edits_are_not(self):
        artist = "ADÉLA"
        title = "Adéla - Nicole Kidman (So Sus Edition)"
        wanted = dict(title="Ade\u0301la - Nicole Kidman (So Sus Edition)", uploader="So Sus",
                      webpage_url="https://soundcloud.com/sosusmusic/ade-la-nicole-kidman-so-sus",
                      thumbnail="https://images.invalid/so-sus.jpg")
        original = dict(title="Nicole Kidman", uploader="ADÉLA", webpage_url="https://soundcloud.com/adelajergova/nicole-kidman",
                        thumbnail="https://images.invalid/original.jpg")
        other = dict(title="ADE\u0301LA - Nicole Kidman (Chase Me Edit)", uploader="Chase Me",
                     webpage_url="https://soundcloud.com/chasemevip/nicole-kidman", thumbnail="https://images.invalid/other.jpg")
        with patch.object(discovery, "_flat_search", return_value=[original, other, wanted]), \
                patch.object(discovery, "_liked_candidates", return_value=[]):
            results = discovery.search_candidates(artist, title)
        self.assertEqual([row["uploader"] for row in results], ["So Sus"])
        self.assertEqual(results[0]["score"], 1)
        self.assertTrue(results[0]["version_upload"])

    def test_version_creator_query_is_used_when_full_title_finds_nothing(self):
        wanted = dict(title="Ade\u0301la - Nicole Kidman (So Sus Edition)", uploader="So Sus",
                      webpage_url="https://soundcloud.com/sosusmusic/edition", thumbnail="https://images.invalid/edition.jpg")
        with patch.object(discovery, "_flat_search", side_effect=lambda q,n:
                          [wanted] if q == "So Sus Nicole Kidman" else []):
            results = discovery.search_candidates("ADÉLA", "Nicole Kidman (So Sus Edition)")
        self.assertEqual(results[0]["uploader"], "So Sus")

    def test_short_song_name_and_one_collaborator_are_a_strong_match(self):
        clean = "SCHMUX x TWOPERCENT - H-O-T"
        for uploader in ("SCHMUX", "TWOPERCENT"):
            self.assertGreaterEqual(discovery._match_score(clean, "H-O-T", "SCHMUX x TWOPERCENT", "H-O-T", uploader), .8)
            self.assertLess(discovery._match_score(clean, "H-O-T", "SCHMUX x TWOPERCENT", "NOT H-O-T", uploader), .8)

    def test_alternate_account_and_title_variant_are_review_only_without_hardcoded_aliases(self):
        candidate = dict(title="Windin n' Grindin (Akon - I Wanna Love You)", uploader="Mport's Xports",
                         webpage_url="https://soundcloud.com/mports-xports/windin-n-grindin-mport-edit",
                         thumbnail="https://images.invalid/mport.jpg")
        def query(q,n):
            return [candidate] if q == "Mport Windin n Grindin" else []
        with patch.object(discovery, "_flat_search", side_effect=query), \
                patch.object(discovery, "_liked_candidates", return_value=[]):
            result = discovery.search_candidates("Mport", "Mport - Wind n' Grind (Mport Edit)")
            self.assertEqual(result[0]["uploader"], "Mport's Xports")
            self.assertTrue(result[0]["possible"])
            self.assertGreaterEqual(result[0]["score"], .65)
            self.assertIsNone(discovery.fetch_by_search("Mport", "Mport - Wind n' Grind (Mport Edit)"))
        clean = discovery._clean_search_title("Mport - Wind n' Grind (Mport Edit)")
        for uploader in ("Unrelated Artist", "Randomport Archive"):
            score, reason = discovery._review_score(clean, "Wind n' Grind", "Mport", {**candidate, "uploader":uploader})
            self.assertFalse(reason)
            self.assertLess(score, .65)
    def setUp(self):
        discovery.configure_source_exclusions(())
        self.addCleanup(discovery.configure_source_exclusions, ())

    def test_seller_names_handles_and_separator_variants(self):
        for name in ("Dubplate Dealer", "DUBPLATE SELLERS", "Dubplate_Reseller_2",
                     "Dubplate Shop", "Dubs Trading", "Dubz Store", "D U B P L A T E DEALER",
                     "Plate Selling", "PLATE SELLER", "Plate Trading", "Selling Plates"):
            with self.subTest(name=name):
                self.assertTrue(excluded_source(name))
        for handle in ("dubplatedealerfuk", "DUBPLATE-SELLER", "dubplate%20dealer", "dubplate-marketplace",
                       "plate-selling", "plate%20dealer", "plates-reseller"):
            with self.subTest(handle=handle):
                self.assertTrue(excluded_source(url=f"https://soundcloud.com/{handle}/song"))
        self.assertFalse(excluded_source("Dubplate Records", "https://soundcloud.com/real-artist/dubplate-dealer"))
        self.assertFalse(excluded_source("Dealer", "https://soundcloud.com/dealer/song"))
        self.assertFalse(excluded_source("Plate Records", "https://soundcloud.com/platerecords/dubplate-selling"))

    def _entry(self, uploader, handle, title="Artist - Song"):
        return dict(title=title, uploader=uploader,
                    webpage_url=f"https://soundcloud.com/{handle}/song",
                    uploader_url=f"https://soundcloud.com/{handle}",
                    thumbnail=f"https://images.invalid/{handle}.jpg", artists=["Artist"])

    def test_reseller_perfect_title_and_credits_never_beat_the_artist(self):
        entries = [self._entry("Plate Selling", "plate-selling"),
                   self._entry("Dubplate Dealer", "dubplatedealerfuk"),
                   self._entry("Someone else", "dubplate-seller"),
                   self._entry("Artist", "artist", title="Song")]
        with patch.object(discovery, "_flat_search", return_value=entries), \
                patch.object(discovery, "_liked_candidates", return_value=[]), \
                patch.object(discovery, "_download", return_value=(b"cover", "image/jpeg")) as download:
            candidates = discovery.search_candidates("Artist", "Artist - Song")
            self.assertEqual([c["uploader"] for c in candidates], ["Artist"])
            chosen = discovery.fetch_by_search("Artist", "Artist - Song")
            self.assertEqual(chosen.source_url, "https://soundcloud.com/artist/song")
            download.assert_called_once_with("https://images.invalid/artist.jpg")

    def test_artist_name_match_ranks_before_liked_repost(self):
        from soundbox_personal import connection
        liked = self._entry("Repost Account", "repost")
        own = self._entry("Artist", "artist", title="Song")
        with patch.object(connection, "liked_entries", return_value=(liked,)), \
                patch.object(discovery, "_flat_search", return_value=[own]):
            candidates = discovery.search_candidates("Artist", "Artist - Song")
        self.assertTrue(candidates[0]["artist_upload"])
        self.assertEqual(candidates[0]["uploader"], "Artist")
        self.assertTrue(candidates[1]["liked"])

    def test_accountable_liked_label_survives_general_search_failure(self):
        from soundbox_personal import connection
        liked = self._entry("Music Label", "label", title="Song")
        with patch.object(connection, "liked_entries", return_value=(liked,)), \
                patch.object(discovery, "_flat_search", side_effect=OSError("offline")), \
                patch.object(discovery, "_download", return_value=(b"cover", "image/jpeg")):
            result = discovery.fetch_best("Artist", "Artist - Song")
            self.assertEqual(result.source_url, liked["webpage_url"])

    def test_likes_and_artist_suggestions_also_exclude_sellers(self):
        from soundbox_personal import connection
        entry = self._entry("Dubplate Dealer", "dubplatedealerfuk")
        with patch.object(connection, "liked_entries", return_value=(entry,)), \
                patch.object(discovery, "_flat_search", return_value=[entry]), \
                patch.object(discovery, "_download") as download:
            self.assertEqual(discovery._liked_candidates("Artist", "Artist - Song"), [])
            self.assertIsNone(discovery.fetch_best("Artist", "Artist - Song"))
            self.assertIsNone(discovery.suggest_artist("Artist - Song"))
            self.assertIsNone(discovery.fetch_artist_avatar("Dubplate Dealer"))
            download.assert_not_called()

    def test_direct_urls_previews_and_full_extraction_cannot_bypass_exclusion(self):
        url = "https://soundcloud.com/dubplatedealerfuk/song"
        with patch.object(discovery, "_download") as download, \
                patch.object(discovery, "_full") as full, \
                patch.object(discovery.urllib.request, "urlopen") as request:
            self.assertIsNone(discovery.fetch_thumb("https://images.invalid/a.jpg", url))
            self.assertIsNone(discovery.fetch_by_url(url))
            self.assertIsNone(discovery._page_og_image(url))
            self.assertIsNone(discovery._fetch_from_entry(url, 1))
            self.assertIsNone(discovery.preview_image(dict(url=url, thumb="https://images.invalid/a.jpg")))
            download.assert_not_called()
            full.assert_not_called()
            request.assert_not_called()

    def test_custom_exclusions_persist_and_guard_publication_after_network(self):
        with tempfile.TemporaryDirectory() as td:
            cache = ArtCache(Path(td) / "art")
            url = "https://soundcloud.com/other-reposter/song"
            cache.exclude_source(url)
            reopened = ArtCache(cache.root)
            self.assertEqual(reopened.source_exclusions(), ("other-reposter",))
            discovery.configure_source_exclusions(reopened.source_exclusions())
            entry = self._entry("Different display name", "other-reposter")
            with patch.object(discovery, "_flat_search", return_value=[entry]):
                self.assertEqual(discovery.search_candidates("Artist", "Artist - Song"), [])
            fetched = ArtResult(b"image", "image/jpeg", 1, url, "Song")
            self.assertFalse(cache.store_auto_if_unfetched("1", fetched))
            with self.assertRaisesRegex(ValueError, "excluded"):
                cache.store_ok("1", fetched)
            self.assertEqual(cache.index, {})
            cache.allow_source("other-reposter")
            self.assertEqual(cache.source_exclusions(), ())

    def test_excerpt_is_not_a_full_track_even_from_the_artist(self):
        for marker in ("CLIP", "Snippet", "Preview", "Teaser"):
            with self.subTest(marker=marker):
                self.assertEqual(discovery._match_score("Artist - Song", "Song", "Artist",
                    f"Artist - Song ({marker})", "Artist"), 0)

    def test_reported_artist_clip_remains_a_manual_art_option_above_seller(self):
        entries = [self._entry("Dubplate Dealer", "dubplatedealerfuk", "AD & AKIRA - U MELT! PT.2"),
                   self._entry("AD_MONSTERS", "ad-monsters", "AD & AKIRA - YOU MELT! PT.2 (CLIP)")]
        with patch.object(discovery, "_flat_search", return_value=entries), \
                patch.object(discovery, "_liked_candidates", return_value=[]):
            candidates = discovery.search_candidates("AD & AKIRA", "AD & AKIRA - U MELT! PT.2 (v2)")
            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0]["uploader"], "AD_MONSTERS")
            self.assertTrue(candidates[0]["excerpt"])
            self.assertGreaterEqual(candidates[0]["score"], .8)
            self.assertIsNone(discovery.fetch_by_search("AD & AKIRA", "AD & AKIRA - U MELT! PT.2 (v2)"))
