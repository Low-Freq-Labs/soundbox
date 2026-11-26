"""Feature credits move as a complete pair; collaborations retain identity."""
import unittest

from soundbox import metadata


class FeaturedCredits(unittest.TestCase):
    def test_plain_credit_pair_does_not_derive_on_acknowledgement(self):
        from unittest import mock
        with mock.patch.object(metadata, 'derive_artist', side_effect=AssertionError('unneeded derivation')):
            self.assertEqual(metadata.cleanup_credit_pair('Manual Title', 'Original Artist'),
                             ('Manual Title', 'Original Artist'))

    def test_current_and_original_feature_placements_produce_the_same_pair(self):
        for title, artist in (('Whiplash', 'Gardella feat. Seth David'),
                              ('Gardella feat. Seth David -  Whiplash', '')):
            with self.subTest(title=title):
                expected = ('Whiplash (feat. Seth David)', 'Gardella')
                self.assertEqual(metadata.cleanup_credit_pair(title, artist), expected)
                self.assertEqual(metadata.cleanup_credit_pair(*expected), expected)

    def test_direct_collaborations_are_never_converted_to_features(self):
        for artist in ('Capochino, Seth David', 'A & B', 'A x B', 'Chase & Status',
                       'Earth, Wind & Fire', 'A vs. B'):
            with self.subTest(artist=artist):
                self.assertEqual(metadata.cleanup_credit_pair('Get That', artist), ('Get That', artist))

    def test_marker_variants_and_unicode_keep_all_contributors(self):
        for marker in ('ft.', 'FT', 'feat.', 'featuring', 'feat.'):
            space = '' if marker == 'feat.' else ' '
            self.assertEqual(metadata.cleanup_credit_pair('Title', f'BØRIS {marker}{space}Éko & Guest'),
                             ('Title (feat. Éko & Guest)', 'BØRIS'))
        self.assertEqual(metadata.cleanup_credit_pair('Title', 'Band Featuring'), ('Title', 'Band Featuring'))
        self.assertEqual(metadata.cleanup_credit_pair('Title', 'Featuring Band'), ('Title', 'Featuring Band'))
        for credit in ('BØRIS (feat. Éko & Guest)', 'BØRIS [featuring Éko & Guest]'):
            self.assertEqual(metadata.cleanup_credit_pair('Title', credit), ('Title (feat. Éko & Guest)', 'BØRIS'))
        self.assertEqual(metadata.cleanup_credit_pair('Title', 'BØRIS (feat. Éko'), ('Title', 'BØRIS (feat. Éko'))

    def test_existing_title_feature_keeps_its_wording_and_position(self):
        for title in ('Song (ft. Guest)', 'Song [featuring Guest]', 'Song feat.Guest'):
            self.assertEqual(metadata.cleanup_credit_pair(title, 'Artist feat. Guest'), (title, 'Artist'))

    def test_named_remix_or_edition_still_owns_the_effective_artist(self):
        for kind in ('Remix', 'Edition'):
            title = f'Original feat. Guest - Song (So Sus {kind})'
            artist = metadata.effective_artist('1', title, 'Original feat. Guest')
            self.assertEqual(artist, 'So Sus')
            self.assertEqual(metadata.effective_title('1', title, artist, source_artist='Original feat. Guest'), title)
        title = 'Song (Remixer feat. Guest Remix)'
        self.assertEqual(metadata.effective_artist('1', title, 'Original'), 'Remixer')
        self.assertEqual(metadata.effective_title('1', title, 'Remixer', source_artist='Original'), title)

    def test_disabling_artist_derivation_keeps_tag_over_title_editor(self):
        self.assertEqual(metadata.effective_artist('1', 'Song (Remixer feat. Guest Remix)', 'Original',
                                                 derive=False), 'Original')
        self.assertEqual(metadata.effective_artist('1', 'Song', 'Original feat. Guest', derive=False), 'Original')

    def test_effective_fields_use_raw_artist_before_it_is_changed(self):
        artist = metadata.effective_artist('1', 'Whiplash', 'Gardella feat. Seth David')
        title = metadata.effective_title('1', 'Whiplash', artist, source_artist='Gardella feat. Seth David')
        self.assertEqual((title, artist), ('Whiplash (feat. Seth David)', 'Gardella'))

    def test_feature_inside_reversed_title_preserves_trusted_artist_and_song(self):
        registry = metadata.build_registry(['WODD', 'EASY TO LOVE Ft. LIA'], ['WODD'])
        raw = 'EASY TO LOVE Ft. LIA - WODD'
        self.assertEqual(metadata.cleanup_credit_pair(raw, '', registry=registry),
                         ('EASY TO LOVE Ft. LIA', 'WODD'))
        artist = metadata.effective_artist('1', raw, '', registry=registry)
        self.assertEqual(artist, 'WODD')
        self.assertEqual(metadata.effective_title('1', raw, artist, source_artist='', registry=registry),
                         'EASY TO LOVE Ft. LIA')

    def test_keep_originals_and_explicit_manual_values_remain_unchanged(self):
        raw_artist = 'Gardella feat. Seth David'
        self.assertEqual(metadata.effective_artist('1', 'Whiplash', raw_artist, cleanup=False), raw_artist)
        self.assertEqual(metadata.effective_title('1', 'Whiplash', raw_artist, cleanup=False,
                                                source_artist=raw_artist), 'Whiplash')
        self.assertEqual(metadata.effective_artist('1', 'Song', raw_artist, {'1': 'Chosen feat. Guest'}),
                         'Chosen feat. Guest')
        self.assertEqual(metadata.effective_title('1', 'Song', 'Artist', {'1': 'Chosen exact title'},
                                                source_artist=raw_artist), 'Chosen exact title')


class ConstituentArtistCase(unittest.TestCase):
    def test_detached_export_and_firmware_labels_keep_the_same_complete_pair(self):
        from pathlib import Path
        from soundbox import engine, stock_ipod, stock_ipod_catalog, dual_ipod
        from soundbox.library import Library, Playlist, Track
        from soundbox.export_snapshot import capture_export
        track = Track('1', 'Whiplash', 'Gardella feat. seth david', 'file:///generated/song.wav')
        playlist = Playlist('Set', [], ['1'], 'one')
        registry = metadata.build_registry(['Gardella', 'Seth David', 'Seth David', 'dennett x Another',
                                            'DENNETT', 'DENNETT'])
        library = Library({'1': track}, [playlist], '/generated/master.db', artist_registry=registry)
        captured = capture_export(library, [playlist])
        frozen = captured.materialize().library
        self.assertEqual(frozen.artist_registry.canonical('dennett x Another'), 'DENNETT x Another')
        expected = ('Whiplash (feat. Seth David)', 'Gardella')
        row = engine.track_row(track, registry=registry)
        self.assertEqual((row['title'], row['artist']), expected)
        for context in (library, frozen):
            actual_track = context.tracks_by_id['1']
            self.assertEqual(stock_ipod._effective_track_metadata(actual_track, Path('/generated/song.wav'),
                derive_artists=True, artist_overrides=None, title_overrides=None, library=context), expected)
            self.assertEqual(dual_ipod._effective_display_metadata(context, actual_track, Path('/generated/song.wav'),
                derive_artists=True, artist_overrides=None, title_overrides=None), expected)
            catalog = dict(stock_ipod_catalog.catalog_metadata(actual_track, context))
            self.assertEqual((catalog['title'], catalog['artist']), expected)

    def test_actual_credit_shapes_share_solo_majority_without_losing_collaborators(self):
        credits = (['DENNETT'] * 25 + ['DENNETT & MACHAKI'] * 2
                   + ['MACHAKI & DENNETT', 'FLOZONE x DENNETT', 'Don Jamal x Dennett', 'dennett x capo lee'])
        registry = metadata.build_registry(credits, credits)
        for context in (registry, metadata.freeze_registry(registry)):
            self.assertEqual(context.canonical('Don Jamal x Dennett'), 'Don Jamal x DENNETT')
            self.assertEqual(context.canonical('dennett x capo lee'), 'DENNETT x capo lee')
            self.assertEqual(context.canonical('DENNETT & MACHAKI'), 'DENNETT & MACHAKI')
            self.assertEqual(context.canonical('MACHAKI & DENNETT'), 'MACHAKI & DENNETT')

    def test_source_tags_and_repeated_constituents_do_not_double_vote(self):
        registry = metadata.build_registry(['DENNETT', 'dennett x dennett'], ['dennett'] * 100)
        self.assertEqual(registry.canonical('dennett x dennett'), 'DENNETT x DENNETT')
        registry = metadata.build_registry(['DENNETT', 'DENNETT', 'dennett x Other'], ['dennett'] * 100)
        self.assertEqual(registry.canonical('dennett x Other'), 'DENNETT x Other')

    def test_collaboration_majority_also_controls_the_same_solo_artist(self):
        registry = metadata.build_registry(['DENNETT x A'] * 10 + ['dennett'])
        for context in (registry, metadata.freeze_registry(registry)):
            self.assertEqual(context.canonical('dennett'), 'DENNETT')
            self.assertEqual(context.canonical('dennett x A'), 'DENNETT x A')

    def test_collaboration_order_and_separators_are_preserved(self):
        credits = ['ALPHA'] * 5 + ['Beta'] * 2 + ['alpha & beta', 'beta, alpha', 'alpha X beta', 'alpha vs. beta']
        registry = metadata.build_registry(credits)
        for raw, expected in (('alpha & beta', 'ALPHA & beta'), ('beta, alpha', 'beta, ALPHA'),
                              ('alpha X beta', 'ALPHA X beta'), ('alpha vs. beta', 'ALPHA vs. beta')):
            # beta occurs in four collaborations and two solos; constituent
            # appearances, rather than solo-only counts, determine its spelling.
            self.assertEqual(registry.canonical(raw), expected)

    def test_ambiguous_group_and_explicit_whole_group_alias_stay_whole(self):
        registry = metadata.build_registry(['Earth, Wind & Fire'] * 3 + ['earth, wind & fire'])
        self.assertEqual(registry.canonical('earth, wind & fire'), 'Earth, Wind & Fire')
        registry = metadata.build_registry(['CHASE'] * 5 + ['STATUS'] * 5 + ['Chase & Status'],
                                           aliases={'Variant Group': 'Chase & Status'})
        self.assertEqual(registry.canonical('Variant Group'), 'Chase & Status')
        self.assertEqual(registry.canonical('Chase & Status'), 'Chase & Status')

    def test_unicode_components_and_exact_boundaries_preserve_identity(self):
        registry = metadata.build_registry(['BØRIS'] * 4 + ['Éko'] * 3 + ['børis x éko'])
        self.assertEqual(registry.canonical('børis x éko'), 'BØRIS x Éko')
        self.assertEqual(registry.canonical('børisish x éko'), 'børisish x Éko')
        self.assertEqual(registry.canonical('BØRIS & Unrelated Group'), 'BØRIS & Unrelated Group')

    def test_manual_collaboration_identity_survives_while_majority_case_applies(self):
        registry = metadata.build_registry(['DENNETT'] * 4 + ['dennett x Another Artist'])
        self.assertEqual(metadata.effective_artist('1', 'Wrong - Song', 'Wrong',
            {'1': 'dennett x Another Artist'}, registry=registry), 'DENNETT x Another Artist')
        aliased = metadata.build_registry(['DENNETT'] * 4 + ['dennett x Another Artist'],
            aliases={'Picked Group': 'dennett x Another Artist'})
        self.assertEqual(metadata.effective_artist('1', 'Wrong - Song', 'Wrong',
            {'1': 'Picked Group'}, registry=aliased), 'dennett x Another Artist')

    def test_only_explicit_title_credit_spans_receive_component_casing(self):
        registry = metadata.build_registry(['DENNETT'] * 4 + ['Guest'] * 2 + ['dennett x guest'])
        self.assertEqual(metadata.canonical_title_credits('dennett is a song word', registry),
                         'dennett is a song word')
        self.assertEqual(metadata.canonical_title_credits('Song (dennett x guest Remix)', registry),
                         'Song (DENNETT x Guest Remix)')
        self.assertEqual(metadata.canonical_title_credits('Song (feat. dennett)', registry),
                         'Song (feat. DENNETT)')
        self.assertEqual(metadata.effective_title('1', 'Song (dennett Remix)', 'DENNETT',
                         {'1': 'My exact (dennett Remix)'}, registry=registry), 'My exact (dennett Remix)')
        self.assertEqual(metadata.effective_title('1', 'Song (dennett Remix)', 'DENNETT',
                         registry=registry, cleanup=False), 'Song (dennett Remix)')

    def test_title_credit_ornamentation_survives_artist_edge_cleanup(self):
        registry = metadata.build_registry(['Lumia _Police'] * 2)
        title = 'Move your body (Lumia _Police_ Edit)'
        self.assertEqual(metadata.canonical_title_credits(title, registry), title)
        registry = metadata.build_registry(['RJ WLKR'] * 3 + ['SPOONE'], aliases={'RJ-WLKR': 'RJ WLKR'})
        self.assertEqual(metadata.canonical_title_credits('Song (RJ-WLKR x spoone Flip)', registry),
                         'Song (RJ WLKR x SPOONE Flip)')
