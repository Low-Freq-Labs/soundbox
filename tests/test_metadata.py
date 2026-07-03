import unittest

from soundbox.metadata import (
    NAME, TAG, UNKNOWN, build_registry, clean_artist, derive_artist, effective_artist,
)


class TestDeriveArtist(unittest.TestCase):
    def test_tag_wins_over_everything(self):
        # Baked-in rekordbox Artist tag is trusted, even with an editor in parens.
        self.assertEqual(derive_artist("Song (XOTIX flip)", "Real Artist"), ("Real Artist", TAG))

    def test_editor_flip(self):
        self.assertEqual(derive_artist("$UICIDEBOY$ - Song (XOTIX flip)", ""), ("XOTIX", NAME))

    def test_editor_remix(self):
        self.assertEqual(derive_artist("311 - Song (REALEZT remix)", ""), ("REALEZT", NAME))

    def test_nested_last_transformation_wins(self):
        # A VIP of a remix → the VIP author (most recent) is the artist.
        self.assertEqual(
            derive_artist("Song (POLKLYPZ remix)(ZEYDAX VIP)", ""), ("ZEYDAX", NAME))

    def test_editor_in_square_brackets(self):
        self.assertEqual(
            derive_artist("Princess Diana [Super Future Flip] - Ice Spice & Nicki Minaj", ""),
            ("Super Future", NAME))

    def test_multiword_editor(self):
        self.assertEqual(
            derive_artist("Ariginal (Blunts & Blondes Remix)", ""), ("Blunts & Blondes", NAME))

    def test_generic_edit_descriptor_rejected(self):
        # "Radio Edit" isn't an editor → fall back to the before-dash artist.
        self.assertEqual(derive_artist("Deadmau5 - Strobe (Radio Edit)", ""), ("Deadmau5", NAME))

    def test_original_mix_not_an_editor(self):
        self.assertEqual(derive_artist("Deadmau5 - Strobe (Original Mix)", ""), ("Deadmau5", NAME))

    def test_front_split_no_parens(self):
        self.assertEqual(
            derive_artist("NGHTMRE & Subtronics - Nuclear Bass Face (feat. Boogie T)", ""),
            ("NGHTMRE & Subtronics", NAME))

    def test_no_dash_is_unknown(self):
        self.assertEqual(derive_artist("Skrawberries", ""), ("Unknown Artist", UNKNOWN))


class TestEffectiveArtist(unittest.TestCase):
    def test_override_wins(self):
        self.assertEqual(
            effective_artist("42", "Song (XOTIX flip)", "Tagged", {"42": "Manual"}), "Manual")

    def test_falls_back_to_derive(self):
        self.assertEqual(effective_artist("1", "A - B", "", {}), "A")

    def test_blank_override_ignored(self):
        self.assertEqual(effective_artist("1", "A - B", "", {"1": "  "}), "A")


class TestArtistRegistry(unittest.TestCase):
    def setUp(self):
        # UNOWN, ohra, DENNETT (various spellings) all seen as real artists.
        self.reg = build_registry(
            ["UNOWN", "UNOWN", "ohra", "DENNETT", "dennett", "Dennett_",
             "DENNETT x MACKAI"])

    def test_clean_artist_trims_edge_junk(self):
        self.assertEqual(clean_artist("Dennett_"), "Dennett")
        self.assertEqual(clean_artist("  - Dennett ~ "), "Dennett")
        self.assertEqual(clean_artist("A$AP"), "A$AP")  # internal chars kept

    def test_dedup_casing_and_junk(self):
        for spelling in ("dennett", "DENNETT", "Dennett_", "  dennett "):
            self.assertEqual(self.reg.canonical(spelling), "DENNETT")

    def test_collab_kept_separate(self):
        self.assertEqual(self.reg.canonical("DENNETT x MACKAI"), "DENNETT x MACKAI")

    def test_rescue_unknown_from_title_with_junk(self):
        # No tag, not derivable -> rescued from a known artist named in the title.
        self.assertEqual(
            effective_artist("1", "UNOWN_ SHAMBLES", "", None, True, self.reg), "UNOWN")

    def test_rescue_does_not_match_midword(self):
        # 'ohra' inside 'XYZsohrab' must NOT smash into the artist ohra.
        self.assertEqual(
            effective_artist("1", "XYZsohrab", "", None, True, self.reg), "Unknown Artist")

    def test_derived_artist_is_canonicalized(self):
        # Editor-in-parens derives 'dennett', registry canonicalizes to 'DENNETT'.
        self.assertEqual(
            effective_artist("1", "Song (dennett flip)", "", None, True, self.reg), "DENNETT")

    def test_override_canonicalized_to_majority_spelling(self):
        # 2026-07-02 rule change (Sonny): an artist can NEVER appear under two
        # casings — even an override folds to the most-tracks spelling.
        self.assertEqual(
            effective_artist("1", "x", "", {"1": "dennett"}, True, self.reg), "DENNETT")
        # Without a registry the override still returns verbatim.
        self.assertEqual(
            effective_artist("1", "x", "", {"1": "dennett"}, True, None), "dennett")


if __name__ == "__main__":
    unittest.main()


class HyphenDerivation(unittest.TestCase):
    """Rule 3.5 — DJ hyphen styles, validated against real library shapes."""

    def _a(self, name):
        return derive_artist(name, "")[0]

    def test_hyphen_space_styles(self):
        self.assertEqual(self._a("CALCIUM- GUTTER"), "CALCIUM")
        self.assertEqual(self._a("Arbityr -Badboy (final v1)"), "Arbityr")
        self.assertEqual(self._a("DACK JANIELS & GRAVEROT- DEATH MOTH (FINAL)"),
                         "DACK JANIELS & GRAVEROT")
        self.assertEqual(self._a("63rd- Kill Zero V20"), "63rd")

    def test_underscore_styles(self):
        self.assertEqual(self._a("ABYSS_-_FEINT_MSTR_1"), "ABYSS")
        self.assertEqual(self._a("IZOLATE_X_YUREI_-_CANDY_CRUSH_v.7"), "IZOLATE X YUREI")
        self.assertEqual(self._a("KrabbeR-_DATA-DISTORTION_V4"), "KrabbeR")
        self.assertEqual(self._a("Brokaw_-_Tough_Love_Master_2"), "Brokaw")

    def test_bare_hyphen_single_token_left(self):
        self.assertEqual(self._a("Adele-Fire To the Rain (DRINKURWATER Refill)"), "Adele")
        self.assertEqual(self._a("DIGIWONK-Square 2.0"), "DIGIWONK")
        self.assertEqual(self._a("GRIMII-WIDDIM (final)"), "GRIMII")

    def test_bare_hyphen_multiword_left_bails(self):
        # Reversed "TITLE-artist" style must NOT invent an artist.
        self.assertEqual(self._a("EVERY NIGHT-BADTRIP_"), "Unknown Artist")
        self.assertEqual(self._a("Had Enough-slwmo"), "Unknown Artist")

    def test_hyphenated_artists_protected(self):
        self.assertEqual(self._a("K-NINE New Castle"), "Unknown Artist")   # K too short
        self.assertEqual(self._a("INFEKT-FEARLESS-THE-DOT-SPECIAL-V1"),
                         "Unknown Artist")                                  # multi-hyphen
        self.assertEqual(self._a("KLEED-PAPER-ROUTE"), "Unknown Artist")

    def test_version_junk_right_bails(self):
        self.assertEqual(self._a("Giving_Up-v2"), "Unknown Artist")

    def test_garbage_bails(self):
        self.assertEqual(self._a("---"), "Unknown Artist")
        self.assertEqual(self._a("JUST HANGIN AROUND (SONNY X JEZTER) 03-28-25"),
                         "Unknown Artist")

    def test_spaced_hyphen_rule_still_first(self):
        self.assertEqual(self._a("JAY-Z - 99 Problems"), "JAY-Z")


class TrustedFlip(unittest.TestCase):
    """Registry-gated reversed 'TITLE - ARTIST' flip (2026-07-02, from Sonny's
    real wrong-derivation samples). Only flips toward TRUSTED (tag/override)
    artists; derived-only names never gate."""

    def setUp(self):
        self.reg = build_registry(
            ["Some Artist"], trusted_names=["ZONDO", "WODD", "DEXTER"])

    def _a(self, title):
        return effective_artist("1", title, "", None, True, self.reg)

    def test_flip_with_junk_tail(self):
        self.assertEqual(self._a("I Wanna Rock - ZONDO final"), "ZONDO")

    def test_flip_wodd_key_bpm_format(self):
        self.assertEqual(self._a("4A - 140 - CRASH TEST - WODD"), "WODD")

    def test_flip_plain(self):
        self.assertEqual(self._a("Toma - DEXTER"), "DEXTER")

    def test_no_flip_when_right_untrusted(self):
        # Normal "Artist - Title": right side isn't a trusted artist → keep left.
        self.assertEqual(self._a("NEWGUY - Some Song"), "NEWGUY")

    def test_no_flip_when_left_trusted(self):
        # Trusted left side never flips, even if right is also trusted.
        self.assertEqual(self._a("WODD - DEXTER"), "WODD")

    def test_tag_never_flips(self):
        # A rekordbox tag is truth — origin TAG bypasses the flip entirely.
        self.assertEqual(
            effective_artist("1", "x - ZONDO", "Real Guy", None, True, self.reg),
            "Real Guy")

    def test_hyphen_style_flip(self):
        self.assertEqual(self._a("Had Enough-WODD"), "WODD")


class EditorStyleTrim(unittest.TestCase):
    def test_genre_word_trimmed_from_editor(self):
        self.assertEqual(derive_artist("My Neck (Netgate Techno Edit)", "")[0],
                         "Netgate")

    def test_reboot_keyword(self):
        self.assertEqual(derive_artist("Like A G6 (Moore Kismet Reboot)", "")[0],
                         "Moore Kismet")


class StemFallback(unittest.TestCase):
    def test_stem_rescues_unknown(self):
        reg = build_registry(["x"], trusted_names=["WODD"])
        self.assertEqual(
            effective_artist("1", "WOMANIZER", "", None, True, reg,
                             stem="WODD - WOMANIZER"), "WODD")

    def test_stem_ignored_when_title_derives(self):
        reg = build_registry(["x"])
        self.assertEqual(
            effective_artist("1", "GUY - Song", "", None, True, reg,
                             stem="somebody else - thing"), "GUY")


class NeatTitle(unittest.TestCase):
    """Redundant edge-artist stripping for display/export (2026-07-02)."""

    def _n(self, name, artist):
        from soundbox.metadata import neat_title
        return neat_title(name, artist)

    def test_leading_artist_stripped(self):
        self.assertEqual(self._n("Zero - Enter The Dragon", "Zero"),
                         "Enter The Dragon")
        self.assertEqual(self._n("ZERO- Enter The Dragon", "Zero"),
                         "Enter The Dragon")  # case + tight hyphen

    def test_trailing_artist_stripped(self):
        self.assertEqual(self._n("GARDEN - WODD", "WODD"), "GARDEN")
        self.assertEqual(self._n("Emotional Response - WODD", "WODD"),
                         "Emotional Response")

    def test_mid_title_artist_tags_survive(self):
        self.assertEqual(self._n("MEGA PUNCH WODD REMIX", "WODD"),
                         "MEGA PUNCH WODD REMIX")  # no separator at edge
        self.assertEqual(self._n("Dom Dolla - Girls (DENNETT Remix)", "DENNETT"),
                         "Dom Dolla - Girls (DENNETT Remix)")

    def test_wrong_artist_untouched(self):
        self.assertEqual(self._n("Zero - Enter The Dragon", "WODD"),
                         "Zero - Enter The Dragon")

    def test_never_leaves_nothing(self):
        # "ARTIST - ARTIST" collapses to the name once (still a valid title);
        # a bare artist-only name is left alone (stripping would empty it).
        self.assertEqual(self._n("WODD - WODD", "WODD"), "WODD")
        self.assertEqual(self._n("Zero", "Zero"), "Zero")

    def test_effective_title_override_wins(self):
        from soundbox.metadata import effective_title
        self.assertEqual(effective_title("1", "Zero - Enter The Dragon", "Zero",
                                         {"1": "My Name"}), "My Name")
        self.assertEqual(effective_title("1", "Zero - Enter The Dragon", "Zero",
                                         {}), "Enter The Dragon")

    def test_collab_edge_variants(self):
        # Exact collab string strips (regex path).
        self.assertEqual(self._n("1SEC x Strange Language - Darkness Master",
                                 "1SEC x Strange Language"), "Darkness Master")
        # Different separator in the artist field ('&' vs 'x') — set path.
        self.assertEqual(self._n("1SEC x Strange Language - Darkness Master",
                                 "1SEC & Strange Language"), "Darkness Master")
        # Reversed order.
        self.assertEqual(self._n("Strange Language x 1SEC - Darkness Master",
                                 "1SEC x Strange Language"), "Darkness Master")
        # Left side is a SUBSET of the collab (one of the two artists).
        self.assertEqual(self._n("1SEC - Darkness Master",
                                 "1SEC x Strange Language"), "Darkness Master")
        # A different artist in the segment must NOT strip.
        self.assertEqual(self._n("Zeds Dead x NGHTMRE - Frontlines",
                                 "1SEC x Strange Language"),
                         "Zeds Dead x NGHTMRE - Frontlines")


class TidyTitle(unittest.TestCase):
    """Display-grade junk stripping (mined from Sonny's 259 title fixes)."""

    def _t(self, s):
        from soundbox.metadata import tidy_title
        return tidy_title(s)

    def test_trailing_junk_runs(self):
        self.assertEqual(self._t("ups and downs davert flip FINAL"),
                         "ups and downs davert flip")
        self.assertEqual(self._t("SILLY DUB [FINAL] 140"), "SILLY DUB")
        self.assertEqual(self._t("44 LEANIN' FINAL MASTER694 (2)"), "44 LEANIN'")
        self.assertEqual(self._t("Bartholomew Kuma 150bpm v2.3"), "Bartholomew Kuma")
        self.assertEqual(self._t("laserbeam Hostage Remix final"),
                         "laserbeam Hostage Remix")

    def test_bracketed_junk_anywhere(self):
        self.assertEqual(self._t("DESIGNER (FINAL_)"), "DESIGNER")
        self.assertEqual(self._t("R28D MASTER(FINAL)"), "R28D MASTER")  # lone master kept
        self.assertEqual(self._t("Ewokz - Run It Up (4)"), "Ewokz - Run It Up")

    def test_leading_patterns(self):
        self.assertEqual(self._t("4A - 140 - CRASH TEST"), "CRASH TEST")

    def test_dialed_back_to_top_trash_only(self):
        # Sonny 2026-07-02: years/master/wip/mN/durations stay MANUAL.
        self.assertEqual(self._t("AG yonkers flip 2021"), "AG yonkers flip 2021")
        self.assertEqual(self._t("(3.40) KHOLD - CLOUT CHASER"),
                         "(3.40) KHOLD - CLOUT CHASER")
        self.assertEqual(self._t("HIDE FINAL M2"), "HIDE FINAL M2")
        self.assertEqual(self._t("Tough Love Mastered"), "Tough Love Mastered")

    def test_protected_real_titles(self):
        self.assertEqual(self._t("Darkness Master"), "Darkness Master")
        self.assertEqual(self._t("Finally Moving"), "Finally Moving")
        self.assertEqual(self._t("THE SEARCH (FULL PLAYABLE)"),
                         "THE SEARCH (FULL PLAYABLE)")   # full/playable never touched
        self.assertEqual(self._t("SPECTRA [FULL]"), "SPECTRA [FULL]")
        self.assertEqual(self._t("Area 51"), "Area 51")  # mid/odd numbers stay

    def test_never_empties(self):
        self.assertEqual(self._t("FINAL"), "FINAL")
        self.assertEqual(self._t("v2"), "v2")

    def test_bpm_key_bracket_group(self):
        self.assertEqual(self._t("RIGHT NOW (FINAL) [140 BPM _ D# MINOR]"),
                         "RIGHT NOW")

    def test_junk_list_is_customizable(self):
        from soundbox.metadata import tidy_title
        # user adds a plain word → safe end/bracket semantics
        self.assertEqual(tidy_title("BANGER promo", ["promo"]), "BANGER")
        self.assertEqual(tidy_title("BANGER (PROMO)", ["promo"]), "BANGER")
        self.assertEqual(tidy_title("promo tape mix", ["promo"]),
                         "promo tape mix")  # never mid/leading
        # user deletes everything → nothing is ever dropped
        self.assertEqual(tidy_title("SILLY DUB [FINAL] 140 v2", []),
                         "SILLY DUB [FINAL] 140 v2")
        # raw regex entries work; invalid regex is skipped, never crashes
        self.assertEqual(tidy_title("TRACK xx123", [r"x{2}\d+"]), "TRACK")
        self.assertEqual(tidy_title("TRACK ok", ["([bad"]), "TRACK ok")

    def test_defaults_visible_and_used_when_none(self):
        from soundbox.metadata import DEFAULT_TITLE_JUNK, tidy_title
        self.assertIn("final", DEFAULT_TITLE_JUNK)
        self.assertEqual(tidy_title("GUTTER FINAL v2", None), "GUTTER")
        self.assertEqual(tidy_title("GUTTER FINAL v2", list(DEFAULT_TITLE_JUNK)),
                         "GUTTER")


class ArtistAliases(unittest.TestCase):
    """User merges: 'unownn' → 'UNOWN' can never appear as its own artist."""

    def setUp(self):
        self.reg = build_registry(
            ["UNOWN", "UNOWN", "unownn"], trusted_names=["UNOWN"],
            aliases={"unownn": "UNOWN"})

    def test_alias_canonicalizes_everywhere(self):
        self.assertEqual(self.reg.canonical("unownn"), "UNOWN")
        self.assertEqual(self.reg.canonical("UNOWNN"), "UNOWN")
        # tag / derived / override all flow through canonical()
        self.assertEqual(effective_artist("1", "x", "unownn", None, True, self.reg),
                         "UNOWN")
        self.assertEqual(effective_artist("1", "x", "", {"1": "unownn"}, True,
                                          self.reg), "UNOWN")

    def test_alias_text_in_title_rescues_to_target(self):
        self.assertEqual(effective_artist("1", "unownn- sickbeat", "", None, True,
                                          self.reg), "UNOWN")

    def test_alias_inherits_trust(self):
        self.assertTrue(self.reg.is_trusted("unownn"))

    def test_names_lists_canonicals_only(self):
        self.assertIn("UNOWN", self.reg.names())
        self.assertNotIn("unownn", self.reg.names())


class SuggestMerges(unittest.TestCase):
    def test_typo_variant_merges_toward_more_tracks(self):
        from soundbox.metadata import suggest_merges
        out = suggest_merges({"UNOWN": 41, "unownn": 3, "DENNETT": 20})
        self.assertEqual([(v, t) for v, t, _ in out], [("unownn", "UNOWN")])

    def test_distinct_names_and_collabs_not_suggested(self):
        from soundbox.metadata import suggest_merges
        out = suggest_merges({"UNOWN": 41, "UNOWN x SHEM": 2,
                              "Kruzie": 5, "SAKURO": 9})
        self.assertEqual(out, [])

    def test_punctuation_variants_merge(self):
        from soundbox.metadata import suggest_merges
        out = suggest_merges({"A$AP TWELVYY": 2, "ASAP TWELVYY": 6})
        self.assertEqual([(v, t) for v, t, _ in out],
                         [("A$AP TWELVYY", "ASAP TWELVYY")])

    def test_one_suggestion_per_variant(self):
        from soundbox.metadata import suggest_merges
        out = suggest_merges({"yeschef": 1, "YES CHEF": 4, "YESCHEF!": 1})
        variants = [v for v, _t, _r in out]
        self.assertEqual(len(variants), len(set(variants)))

    def test_alias_chains_resolve(self):
        reg = build_registry(["YES CHEF"] * 4,
                             aliases={"yeschef": "YESCHEF!", "YESCHEF!": "YES CHEF"})
        self.assertEqual(reg.canonical("yeschef"), "YES CHEF")
        self.assertEqual(reg.canonical("YESCHEF!"), "YES CHEF")
