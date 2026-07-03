import unittest

from soundbox.artfetch import (
    _clean_search_title as clean, _core_title, _split_artists, _match_score,
    MIN_CONFIDENCE,
)


class TestCoreAndSplit(unittest.TestCase):
    def test_core_title_strips_prefix_and_brackets(self):
        self.assertEqual(_core_title("SAKURO - CORROSION"), "CORROSION")
        self.assertEqual(_core_title("GNARLY (DENNETT REMIX)"), "GNARLY")
        self.assertEqual(_core_title("Just A Title"), "Just A Title")

    def test_split_artists(self):
        self.assertEqual(_split_artists("DENNETT x MACKAI"), ["DENNETT", "MACKAI"])
        self.assertEqual(_split_artists("FLY VS HORSE"), ["FLY", "HORSE"])
        self.assertEqual(_split_artists("VICIOUS & VOLATIVE"), ["VICIOUS", "VOLATIVE"])
        self.assertEqual(_split_artists("Zana, Lyente, Sonny Wern"),
                         ["Zana", "Lyente", "Sonny Wern"])
        self.assertEqual(_split_artists("SAKURO"), ["SAKURO"])


class TestMatchScoreAccuracy(unittest.TestCase):
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
        from soundbox.artfetch import _clean_search_title
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
        from soundbox.artfetch import _split_artists
        self.assertEqual(_split_artists("Far East Movement feat. The Cataracs"),
                         ["Far East Movement", "The Cataracs"])


class SuggestArtist(unittest.TestCase):
    """Uploader-consensus suggestions (human-gated)."""

    def _with_results(self, results, title="SOME TRACK"):
        import soundbox.artfetch as af
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
        from soundbox.artfetch import ArtCache
        return ArtCache(Path(td))

    def test_block_survives_rescans_and_reads_as_blocked(self):
        import tempfile
        from soundbox.artfetch import ArtResult
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
        from soundbox.artfetch import ArtResult
        with tempfile.TemporaryDirectory() as td:
            c = self._cache(td)
            c.block("1")
            c.store_ok("1", ArtResult(b"\xff\xd8y", "image/jpeg", 1.0,
                                      "https://soundcloud.com/a/t2", "t2"))
            self.assertEqual(c.status_kind("1"), "cover")
