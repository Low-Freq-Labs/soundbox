import unittest

from soundbox_personal.discovery import (
    _clean_search_title as clean, _core_title, _core_title_for_artist,
    _split_artists, _match_score,
    MIN_CONFIDENCE,
)


class TestCoreAndSplit(unittest.TestCase):
    def test_core_title_strips_prefix_and_brackets(self):
        self.assertEqual(_core_title("SAKURO - CORROSION"), "CORROSION")
        self.assertEqual(_core_title("GNARLY (DENNETT REMIX)"), "GNARLY")
        self.assertEqual(_core_title("Just A Title"), "Just A Title")

    def test_core_title_uses_artist_to_orient_working_names(self):
        self.assertEqual(
            _core_title_for_artist("WODD - CRASH TEST", "WODD"), "CRASH TEST")
        self.assertEqual(
            _core_title_for_artist("CRASH TEST - WODD", "WODD"), "CRASH TEST")

    def test_remixer_credit_does_not_reverse_original_artist_prefix(self):
        for title, artist, expected in (
            ("PHAROAHE MONCH - SIMON SAYS (MYTHM FLIP)", "MYTHM", "SIMON SAYS"),
            ("Huey- Pop, Lock & Drop It - (SAINT MILLER REMIX)",
             "SAINT MILLER", "Pop, Lock & Drop It"),
            ("Usher, Lil Jon, Ludacris - Yeah! (Hairitage Edit)", "Hairitage", "Yeah!"),
        ):
            with self.subTest(title=title):
                self.assertEqual(_core_title_for_artist(title, artist), expected)
        self.assertEqual(_core_title_for_artist("Self- Control", "An Artist"), "Self- Control")

    def test_split_artists(self):
        self.assertEqual(_split_artists("DENNETT x MACKAI"), ["DENNETT", "MACKAI"])
        self.assertEqual(_split_artists("FLY VS HORSE"), ["FLY", "HORSE"])
        self.assertEqual(_split_artists("VICIOUS & VOLATIVE"), ["VICIOUS", "VOLATIVE"])
        self.assertEqual(_split_artists("Zana, Lyente, Sonny Wern"),
                         ["Zana", "Lyente", "Sonny Wern"])
        self.assertEqual(_split_artists("SAKURO"), ["SAKURO"])


class TestMatchScoreAccuracy(unittest.TestCase):
    def _score(self, title, artist, candidate, uploader):
        cleaned = clean(title)
        return _match_score(cleaned, _core_title_for_artist(cleaned, artist),
                            artist, candidate, uploader)

    def test_own_account_omitted_original_artist_and_edit_credit(self):
        cases = (
            ("PHAROAHE MONCH - SIMON SAYS (MYTHM FLIP)", "MYTHM",
             "SIMON SAYS (MYTHM FLIP) (FREE DL)", "MYTHM"),
            ("Huey- Pop, Lock & Drop It - (SAINT MILLER REMIX)", "SAINT MILLER",
             "Pop, Lock & Drop It", "SAINT MILLER"),
        )
        for args in cases:
            with self.subTest(title=args[0]):
                self.assertGreaterEqual(self._score(*args), MIN_CONFIDENCE)

    def test_own_account_reordered_working_title_and_small_spelling_difference(self):
        cases = (
            ("OSSIEN - BLAST LIKE Sippy, Bear Gryllz (OSSIEN Remix) - FINAL", "OSSIEN",
             "Sippy, Bear Grillz - Blast Like (OSSIEN Remix)", "OSSIEN"),
            ("POP DAT THANGG (EYEWITNESS X DEBBIE CHECK REMIX)", "DEBBIE CHECK",
             "GLORILLA - POP DAT THANG (DEBBIE CHECK X EYEWITNESS REMIX)", "Debbie Check"),
        )
        for args in cases:
            with self.subTest(title=args[0]):
                self.assertGreaterEqual(self._score(*args), MIN_CONFIDENCE)

    def test_edit_rescue_rejects_wrong_artist_version_and_song(self):
        title = "POP DAT THANGG (EYEWITNESS X DEBBIE CHECK REMIX)"
        for candidate, uploader in (
            ("GLORILLA - POP DAT THANG (DEBBIE CHECK X EYEWITNESS REMIX)", "Random Reposts"),
            ("POP DAT THANGG (SOMEONE ELSE REMIX)", "Debbie Check"),
            ("POP DAT THANGG (EYEWITNESS X DEBBIE CHECK FLIP)", "Debbie Check"),
            ("STOP THAT THING (DEBBIE CHECK X EYEWITNESS REMIX)", "Debbie Check"),
            ("POP DAT THANGG DOWN (DEBBIE CHECK X EYEWITNESS REMIX)", "Debbie Check"),
        ):
            with self.subTest(candidate=candidate, uploader=uploader):
                self.assertLess(self._score(title, "DEBBIE CHECK", candidate, uploader),
                                MIN_CONFIDENCE)
        self.assertLess(self._score(
            "PHAROAHE MONCH - SIMON SAYS (MYTHM FLIP)", "MYTHM",
            "SIMON SAYS VIP", "MYTHM"), MIN_CONFIDENCE)

    def test_fixed_song_core_drives_search_and_selection(self):
        from unittest.mock import patch
        from soundbox_personal import discovery as af
        entry = {"title": "Pop, Lock & Drop It", "uploader": "SAINT MILLER",
                 "webpage_url": "https://soundcloud.com/saint-baek/pop-lock-drop-it",
                 "thumbnail": "https://example.invalid/cover.jpg"}

        def search(query, n):
            return [entry] if query == "SAINT MILLER Pop, Lock & Drop It" else []

        with patch.object(af, "_flat_search", side_effect=search) as queries, \
                patch.object(af, "_download", return_value=(b"cover", "image/jpeg")):
            result = af.fetch_best(
                "SAINT MILLER", "Huey- Pop, Lock & Drop It - (SAINT MILLER REMIX)")
        self.assertIsNotNone(result)
        self.assertEqual(result.source_url, entry["webpage_url"])
        self.assertEqual(queries.call_args_list[-1].args[0],
                         "SAINT MILLER Pop, Lock & Drop It")

    def test_label_artist_credits_rank_exact_song_above_repost_mashup(self):
        from unittest.mock import patch
        from soundbox_personal import discovery as af
        label = {"title": "echo", "uploader": "Sweet Sounds Collective",
                 "artists": [" huck.jorris x KiLO "],
                 "webpage_url": "https://soundcloud.com/sweetsoundscollective/echo",
                 "thumbnail": "https://example.invalid/original.jpg"}
        mashup = {"title": "huck.jorris x KiLO- echo x Coast Modern- The Way It Was",
                  "uploader": "Jasmine Nichole", "thumbnail": "https://example.invalid/mashup.jpg"}
        with patch.object(af, "_flat_search", return_value=[mashup, label]), \
                patch.object(af, "_download", return_value=(b"correct cover", "image/jpeg")):
            picked = af.search_candidates("huck.jorris x KiLO", "huck.jorris x KiLO - echo")
            result = af.fetch_best("huck.jorris x KiLO", "huck.jorris x KiLO - echo")
        self.assertEqual(picked[0]["url"], label["webpage_url"])
        self.assertEqual(result.source_url, label["webpage_url"])

    def test_label_same_title_requires_structured_artist_credit_and_complete_core(self):
        for credits, candidate in ((None, "echo"), (["Someone Else"], "echo"),
                                   (["huck.jorris x KiLO"], "echo x Another Song")):
            with self.subTest(credits=credits, candidate=candidate):
                self.assertLess(_match_score(
                    "huck.jorris x KiLO - echo", "echo", "huck.jorris x KiLO",
                    candidate, "Sweet Sounds Collective", credits), MIN_CONFIDENCE)

    def test_uploader_same_account_rescues_prefixed_title(self):
        # Their OWN upload omits the "SAKURO -" prefix → low full ratio, but uploader
        # is Sakuro and the core song is present → confident.
        s = _match_score("SAKURO - CORROSION", "CORROSION", "SAKURO",
                         "CORROSION [6K FREEBIE]", "Sakuro")
        self.assertGreaterEqual(s, 0.9)

    def test_wrong_artist_same_title_rejected(self):
        # A DIFFERENT artist's "Gas Leak" must score 0 (not accountable) — never grabbed.
        s = _match_score("Nadii - Gas Leak", "Gas Leak", "Nadii",
                         "GAS LEAK", "Perry Maysun")
        self.assertEqual(s, 0.0)

    def test_megamix_tracklist_below_threshold(self):
        # A long megamix that merely lists the artist has a LOW full ratio → not accepted
        # even though the artist name appears in it.
        s = _match_score("HEMZ - Dream", "Dream", "HEMZ",
                         "DnB Mix - BOU FK JUMP UP - DON DARKOE X HEMZ - Dream x Others",
                         "ED DNB UK")
        self.assertLess(s, MIN_CONFIDENCE)

    def test_legit_strong_accountable_match(self):
        s = _match_score("Decimate - Sassy Fake", "Sassy Fake", "Decimate",
                         "DECIMATE - SASSY FAKE", "Decimate")
        self.assertGreaterEqual(s, MIN_CONFIDENCE)

    def test_unattended_floor_rejects_trigger_finger_style_weak_match(self):
        self.assertGreater(MIN_CONFIDENCE, 0.78)

    def test_unknown_artist_never_searches_unattended(self):
        import soundbox_personal.discovery as af
        real = af.fetch_by_search
        af.fetch_by_search = lambda *a, **k: self.fail("unknown artist searched")
        try:
            self.assertIsNone(af.fetch_best("Unknown Artist", "Nite 'N' Day"))
        finally:
            af.fetch_by_search = real


class TestSearchTitleCleaner(unittest.TestCase):
    def test_strips_version_marker(self):
        self.assertEqual(
            clean("KEYS N KRATES - DUM DEE DUM (PIERCE REMIX) V7"),
            "KEYS N KRATES - DUM DEE DUM (PIERCE REMIX)")

    def test_strips_final_and_version(self):
        self.assertEqual(clean("Deucez Passout Bootleg FINAL V1"), "Deucez Passout Bootleg")

    def test_strips_bpm_key_block(self):
        # BPM/key annotation block is dropped (a bare trailing number may remain;
        # the fuzzy match tolerates that).
        self.assertEqual(clean("MANADA - RIGHT NOW (FINAL) [140 BPM _ D# MINOR] 2"),
                         "MANADA - RIGHT NOW 2")

    def test_strips_format_tags(self):
        self.assertEqual(clean("Whatcha Heard (Master) wav"), "Whatcha Heard")
        self.assertEqual(clean("Out Of Manner (16bit)"), "Out Of Manner")

    def test_strips_trailing_paren_dupe(self):
        self.assertEqual(clean("Decimate & Rico Act - Wook Flu (1)"),
                         "Decimate & Rico Act - Wook Flu")

    def test_keeps_real_trailing_number(self):
        # Must NOT strip a meaningful bare trailing number.
        self.assertEqual(clean("VOYAGER x Drophop - AREA 51"),
                         "VOYAGER x Drophop - AREA 51")

    def test_strips_original_and_extended_mix(self):
        self.assertEqual(clean("Subtronics - bRoKeN cOdE (Original Mix)"),
                         "Subtronics - bRoKeN cOdE")
        self.assertEqual(clean("Dance For Me (1, 2, 3) (Extended Mix)"),
                         "Dance For Me (1, 2, 3)")

    def test_strips_download_and_promo_gates(self):
        self.assertEqual(clean("Shifu & Python - Trauma (Free Download)"),
                         "Shifu & Python - Trauma")
        self.assertEqual(clean("RANE - CONTAGION (4K FREE DL)"), "RANE - CONTAGION")
        self.assertEqual(clean("FLICK - BOOBY BOOB -FREE DOWNLOAD-"), "FLICK - BOOBY BOOB")
        self.assertEqual(clean("HE$H - HIT A LICK (OUT NOW)"), "HE$H - HIT A LICK")

    def test_strips_full_and_playable_brackets(self):
        self.assertEqual(clean("Tune (FULL)"), "Tune")
        self.assertEqual(clean("Some Track (PLAYABLE)"), "Some Track")

    def test_keeps_vip_remix_flip_and_artist_tags(self):
        # These are part of the real SoundCloud title — never strip them.
        for s in ("POKEMON (SAKURO VIP)", "GNARLY (DENNETT REMIX)", "x (PHRVA FLIP)",
                  "RIDDIM (UNOWN BOOTLEG)", "Chibs - Crust and Jelly (XotiX Remix)"):
            self.assertEqual(clean(s), s)

    def test_keeps_real_words_that_look_like_junk(self):
        # Bare ambiguous words are only junk in brackets — leave real titles alone.
        self.assertEqual(clean("Full Clip"), "Full Clip")
        self.assertEqual(clean("Free Bird"), "Free Bird")

    def test_never_empties(self):
        # If cleaning would remove everything, fall back to the original.
        self.assertEqual(clean("FINAL"), "FINAL")


if __name__ == "__main__":
    unittest.main()


class CleanerUpgrades2026_07_02(unittest.TestCase):
    """Measured from Sonny's 164 real title fixes."""

    def _c(self, s):
        from soundbox_personal.discovery import _clean_search_title
        return _clean_search_title(s)

    def test_underscores_normalized(self):
        self.assertEqual(self._c("beanie_god_-_MECHA_FINAL"), "beanie god - MECHA")

    def test_key_bpm_prefix_stripped(self):
        self.assertEqual(self._c("4A - 140 - CRASH TEST - WODD"), "CRASH TEST - WODD")

    def test_master_with_digits(self):
        self.assertEqual(self._c("44 LEANIN' FINAL MASTER694 (2)"), "44 LEANIN'")

    def test_dotted_versions(self):
        self.assertEqual(self._c("Bartholomew Kuma v2.3"), "Bartholomew Kuma")
        self.assertEqual(self._c("CANDY CRUSH v.7"), "CANDY CRUSH")

    def test_leading_duration_and_trailing_year(self):
        self.assertEqual(self._c("(3.40) KHOLD - CLOUT CHASER"), "KHOLD - CLOUT CHASER")
        self.assertEqual(self._c("AG yonkers flip 2021"), "AG yonkers flip")

    def test_protections_hold(self):
        # Real bracket tags and number-titles survive.
        self.assertEqual(self._c("GNARLY (DENNETT REMIX)"), "GNARLY (DENNETT REMIX)")
        self.assertEqual(self._c("Area 51"), "Area 51")
        self.assertEqual(self._c("555 (MONXX)"), "555 (MONXX)")

    def test_date_stamps_stripped_but_personal_tags_kept(self):
        # Sonny's own edit tags stay ON PURPOSE: a coverless own-edit is his
        # visual cue for collection cleanup. Dates are generic WIP junk.
        self.assertEqual(self._c("JUST HANGIN AROUND (SONNY X JEZTER) 03-28-25"),
                         "JUST HANGIN AROUND (SONNY X JEZTER)")
        self.assertEqual(self._c("Monxx- Dinero (sonnyedit)v5"),
                         "Monxx- Dinero (sonnyedit)")

    def test_feat_split(self):
        from soundbox_personal.discovery import _split_artists
        self.assertEqual(_split_artists("Far East Movement feat. The Cataracs"),
                         ["Far East Movement", "The Cataracs"])


class SuggestArtist(unittest.TestCase):
    """Uploader-consensus suggestions (human-gated)."""

    def _with_results(self, results, title="SOME TRACK"):
        import soundbox_personal.discovery as af
        real = af._flat_search
        af._flat_search = lambda q, n=6: results
        try:
            return af.suggest_artist(title)
        finally:
            af._flat_search = real

    def test_repeat_uploader_consensus(self):
        r = self._with_results([
            {"title": "SOME TRACK", "uploader": "GRiZ"},
            {"title": "SOME TRACK (VIP)", "uploader": "GRiZ"},
            {"title": "some track mix ep 4", "uploader": "randomer"},
        ])
        self.assertEqual(r[0], "GRiZ")

    def test_dominant_single_match(self):
        r = self._with_results([
            {"title": "SOME TRACK", "uploader": "GRiZ"},
            {"title": "monday mix episode 8", "uploader": "pags"},
        ])
        self.assertEqual(r[0], "GRiZ")

    def test_rival_edits_stay_silent(self):
        self.assertIsNone(self._with_results([
            {"title": "SOME TRACK (A Edit)", "uploader": "A"},
            {"title": "SOME TRACK (B Remix)", "uploader": "B"},
            {"title": "SOME TRACK (C Flip)", "uploader": "C"},
        ]))

    def test_tie_stays_silent(self):
        self.assertIsNone(self._with_results([
            {"title": "SOME TRACK", "uploader": "A"},
            {"title": "SOME TRACK", "uploader": "A"},
            {"title": "SOME TRACK", "uploader": "B"},
            {"title": "SOME TRACK", "uploader": "B"},
        ]))

    def test_vague_title_stays_silent(self):
        self.assertIsNone(self._with_results(
            [{"title": "x", "uploader": "A"}], title="x"))


class BlockedArt(unittest.TestCase):
    """Deleted art must STAY deleted (user decision) — only explicit refetch wins."""

    def _cache(self, td):
        from pathlib import Path
        from soundbox_personal.discovery import ArtCache
        return ArtCache(Path(td))

    def test_block_survives_rescans_and_reads_as_blocked(self):
        import tempfile
        from soundbox_personal.discovery import ArtResult
        with tempfile.TemporaryDirectory() as td:
            c = self._cache(td)
            c.store_ok("1", ArtResult(b"\xff\xd8x", "image/jpeg", 0.9,
                                      "https://soundcloud.com/a/t", "t"))
            c.block("1")
            self.assertEqual(c.status_kind("1"), "blocked")
            self.assertIsNone(c.image_path("1"))          # image gone
            self.assertIsNotNone(c.status("1"))           # fill worker SKIPS it
            c.retry_misses()
            c.retry_weak()
            self.assertEqual(c.status_kind("1"), "blocked")  # re-scans can't wake it

    def test_explicit_store_overrides_block(self):
        import tempfile
        from soundbox_personal.discovery import ArtResult
        with tempfile.TemporaryDirectory() as td:
            c = self._cache(td)
            c.block("1")
            c.store_ok("1", ArtResult(b"\xff\xd8y", "image/jpeg", 1.0,
                                      "https://soundcloud.com/a/t2", "t2"))
            self.assertEqual(c.status_kind("1"), "cover")

    def test_new_entries_record_manual_or_auto_provenance(self):
        import tempfile
        from soundbox_personal.discovery import ArtResult
        with tempfile.TemporaryDirectory() as td:
            c = self._cache(td)
            result = ArtResult(b"\xff\xd8x", "image/jpeg", 0.9,
                               "https://soundcloud.com/a/t", "t")
            c.store_ok("manual", result)
            c.store_ok("auto", result, origin="auto")
            self.assertEqual(c.index["manual"]["origin"], "manual")
            self.assertEqual(c.index["auto"]["origin"], "auto")


class DefaultPhotoPool(unittest.TestCase):
    def _image(self, path, color, size=(900, 300)):
        from PIL import Image
        Image.new("RGB", size, color).save(path, "PNG")

    def test_import_copies_normalizes_and_survives_source_move(self):
        import tempfile
        from pathlib import Path
        from PIL import Image
        from soundbox import artencode
        from soundbox_personal.discovery import ArtCache

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "somewhere" / "wide.png"
            source.parent.mkdir()
            self._image(source, "magenta")
            cache = ArtCache(root / "art")
            result = cache.import_default_photos([source])
            self.assertEqual(result, {"added": 1, "duplicates": 0, "errors": []})
            row = cache.default_photos()[0]
            managed = row["path"]
            self.assertTrue(managed.is_file())
            self.assertTrue(artencode.is_device_safe(managed.read_bytes()))
            with Image.open(managed) as image:
                self.assertLessEqual(max(image.size), artencode.MAX_SIDE)
            source.unlink()
            cache.configure_default_tracks(["blank"])
            self.assertEqual(cache.image_path("blank"), managed)

    def test_assignments_are_randomized_stable_and_exactly_balanced(self):
        import tempfile
        from pathlib import Path
        from soundbox_personal.discovery import ArtCache

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sources = []
            for i, color in enumerate(("red", "green", "blue")):
                source = root / f"{i}.png"
                self._image(source, color)
                sources.append(source)
            cache = ArtCache(root / "art")
            cache.import_default_photos(sources)
            tids = [str(i) for i in range(10)]
            cache.configure_default_tracks(tids)
            first = {tid: cache.image_path(tid) for tid in tids}
            counts = sorted(row["tracks"] for row in cache.default_photos())
            self.assertEqual(counts, [3, 3, 4])

            reopened = ArtCache(root / "art")
            reopened.configure_default_tracks(reversed(tids))
            self.assertEqual(
                {tid: reopened.image_path(tid) for tid in tids}, first)

    def test_adding_a_pool_photo_moves_only_the_minimum_tracks(self):
        import tempfile
        from pathlib import Path
        from soundbox_personal.discovery import ArtCache

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            first_sources = []
            for i, color in enumerate(("red", "green", "blue")):
                source = root / f"first-{i}.png"
                self._image(source, color)
                first_sources.append(source)
            cache = ArtCache(root / "art")
            cache.import_default_photos(first_sources)
            tids = [str(i) for i in range(12)]
            cache.configure_default_tracks(tids)
            before = {tid: cache.image_path(tid) for tid in tids}

            added = root / "added.png"
            self._image(added, "yellow")
            cache.import_default_photos([added])
            after = {tid: cache.image_path(tid) for tid in tids}

            # Four photos × three tracks: the new bucket needs exactly three,
            # and no unrelated assignment should churn beyond those moves.
            self.assertEqual(sum(before[tid] != after[tid] for tid in tids), 3)
            self.assertEqual(
                sorted(row["tracks"] for row in cache.default_photos()),
                [3, 3, 3, 3])

    def test_real_covers_and_avatars_win_but_blocks_receive_defaults(self):
        import io
        import tempfile
        from pathlib import Path
        from PIL import Image
        from soundbox_personal.discovery import ArtCache, ArtResult

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "default.png"
            self._image(source, "purple")
            cache = ArtCache(root / "art")
            cache.import_default_photos([source])
            cache.configure_default_tracks(["missing", "blocked", "cover", "avatar"])
            cache.block("blocked")

            buf = io.BytesIO()
            Image.new("RGB", (100, 100), "orange").save(buf, "JPEG")
            cover = ArtResult(buf.getvalue(), "image/jpeg", 1.0,
                              "https://soundcloud.com/a/song", "song")
            avatar = ArtResult(buf.getvalue(), "image/jpeg", 1.0,
                               "https://soundcloud.com/a", "a")
            cache.store_ok("cover", cover)
            cache.store_ok("avatar", avatar)

            self.assertEqual(cache.status_kind("missing"), "default")
            self.assertEqual(cache.status_kind("blocked"), "default")
            self.assertEqual(cache.status("blocked"), "blocked")
            self.assertEqual(cache.status_kind("cover"), "cover")
            self.assertEqual(cache.status_kind("avatar"), "avatar")
            self.assertNotEqual(cache.image_path("missing"),
                                cache.canonical_image_path("cover"))
            self.assertEqual(cache.image_path("cover"),
                             cache.canonical_image_path("cover"))

    def test_delete_rebalances_without_touching_source_or_track_art(self):
        import io
        import tempfile
        from pathlib import Path
        from PIL import Image
        from soundbox_personal.discovery import ArtCache, ArtResult

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sources = []
            for i, color in enumerate(("cyan", "yellow")):
                source = root / f"source-{i}.png"
                self._image(source, color)
                sources.append(source)
            cache = ArtCache(root / "art")
            cache.import_default_photos(sources)
            cache.configure_default_tracks([str(i) for i in range(7)])

            buf = io.BytesIO()
            Image.new("RGB", (80, 80), "black").save(buf, "JPEG")
            cache.store_ok("real", ArtResult(
                buf.getvalue(), "image/jpeg", 1.0,
                "https://soundcloud.com/artist/track", "track"))
            real = cache.canonical_image_path("real")
            rows = cache.default_photos()
            removed_path = rows[0]["path"]
            self.assertEqual(cache.remove_default_photos([rows[0]["id"]]), 1)

            self.assertFalse(removed_path.exists())
            self.assertTrue(all(source.exists() for source in sources))
            self.assertEqual(cache.canonical_image_path("real"), real)
            self.assertTrue(real.is_file())
            self.assertEqual(len(cache.default_photos()), 1)
            self.assertEqual(cache.default_photos()[0]["tracks"], 7)

    def test_duplicate_managed_image_is_not_added_twice(self):
        import tempfile
        from pathlib import Path
        from soundbox_personal.discovery import ArtCache

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "same.png"
            self._image(source, "white")
            cache = ArtCache(root / "art")
            first = cache.import_default_photos([source])
            second = cache.import_default_photos([source])
            self.assertEqual(first["added"], 1)
            self.assertEqual(second["duplicates"], 1)
            self.assertEqual(len(cache.default_photos()), 1)


class ArtWorkerProvenance(unittest.TestCase):
    def test_background_fill_marks_new_cover_auto(self):
        import tempfile
        from pathlib import Path
        from desktop.workers import ArtFillWorker
        from soundbox_personal import discovery as artfetch
        from soundbox.library import Library, Track

        with tempfile.TemporaryDirectory() as td:
            audio = Path(td) / "song.mp3"
            audio.touch()
            track = Track("1", "Artist - Song", "Artist", audio.as_uri())
            lib = Library({"1": track}, [], "test")
            cache = artfetch.ArtCache(Path(td) / "art")
            result = artfetch.ArtResult(
                b"image", "image/jpeg", 0.9,
                "https://soundcloud.com/artist/song", "Artist - Song")
            real = artfetch.fetch_best
            artfetch.fetch_best = lambda *_args: result
            try:
                worker = ArtFillWorker(lib, cache, {}, {}, provider=artfetch)
                worker.DELAY = 0
                worker.run()
            finally:
                artfetch.fetch_best = real
            self.assertEqual(cache.index["1"]["origin"], "auto")
