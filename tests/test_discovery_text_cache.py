"""Pure text reuse keeps current provider candidates and preferences fresh."""
import unittest
from unittest.mock import patch

from soundbox_personal import discovery as discovery


class DiscoveryTextCache(unittest.TestCase):
    def setUp(self):
        self.helpers = (discovery._clean_search_text, discovery._normalized_text)
        for helper in self.helpers:
            helper.cache_clear()
        self.exclusions = discovery._EXCLUDED_ACCOUNTS
        self.addCleanup(discovery.configure_source_exclusions, self.exclusions)
        for helper in self.helpers:
            self.addCleanup(helper.cache_clear)

    @staticmethod
    def entries():
        values = (
            ('ADÉLA - Café', 'Ade\u0301la', None),
            ('ADÉLA - Café', 'Ade\u0301la', None),
            ('Café [clip]', 'ADÉLA', None),
            ('Café', 'Fixture label', ['ADÉLA']),
            ('Café', 'Unrelated publisher', None),
            ('GLORILLA - POP DAT THANG (DEBBIE CHECK X EYEWITNESS REMIX)', 'Debbie Check', None),
            ('POP DAT THANGG (SOMEONE ELSE REMIX)', 'Debbie Check', None),
            ('SIMON SAYS (MYTHM FLIP) (FREE DL)', 'MYTHM', None),
            ('SIMON SAYS VIP', 'MYTHM', None),
            ('Grindin n Windin', 'Fixture Artist', None),
        )
        return [dict(title=title, uploader=uploader, artists=artists,
                     webpage_url=f'https://soundcloud.com/fixture-{i}/recording',
                     thumbnail=f'https://example.invalid/{i}.jpg', duration=60+i)
                for i, (title, uploader, artists) in enumerate(values)]

    def uncached(self, artist, title, *, allow_excerpt):
        with patch.object(discovery, '_clean_search_title', discovery._clean_search_text.__wrapped__), \
                patch.object(discovery, '_norm', discovery._normalized_text.__wrapped__):
            return discovery._liked_candidates(artist, title, allow_excerpt=allow_excerpt)

    def test_complete_rankings_ties_and_current_entries_exclusions_stay_exact(self):
        entries = self.entries()
        queries = (
            ('ADÉLA', 'ADÉLA - Café'),
            ('DEBBIE CHECK', 'POP DAT THANGG (EYEWITNESS X DEBBIE CHECK REMIX)'),
            ('MYTHM', 'PHAROAHE MONCH - SIMON SAYS (MYTHM FLIP)'),
            ('Fixture Artist', 'Grind n Wind'),
        )
        with patch('soundbox_personal.connection.liked_entries', return_value=entries), \
                patch('urllib.request.urlopen', side_effect=AssertionError('network forbidden')):
            for artist, title in queries:
                for allow_excerpt in (False, True):
                    with self.subTest(artist=artist, allow_excerpt=allow_excerpt):
                        expected = self.uncached(artist, title, allow_excerpt=allow_excerpt)
                        for _ in range(2):
                            self.assertEqual(discovery._liked_candidates(
                                artist, title, allow_excerpt=allow_excerpt), expected)
            original = discovery._liked_candidates(*queries[0])
            tied = [row['url'] for row in original if row['title'] == 'ADÉLA - Café']
            self.assertEqual(tied, [entries[0]['webpage_url'], entries[1]['webpage_url']])
            # Neither the account snapshot nor exclusion decisions are cached.
            discovery.configure_source_exclusions(['fixture-0'])
            entries[1].update(title='Unrelated recording', uploader='Unrelated publisher', duration=999)
            entries.append(dict(entries[0], webpage_url='https://soundcloud.com/fresh/recording'))
            expected = self.uncached(*queries[0], allow_excerpt=False)
            actual = discovery._liked_candidates(*queries[0])
            self.assertEqual(actual, expected)
            self.assertNotEqual(actual, original)
            self.assertNotIn(entries[0]['webpage_url'], [row['url'] for row in actual])
            self.assertIn(entries[-1]['webpage_url'], [row['url'] for row in actual])

    def test_nonstring_fallbacks_and_errors_are_unchanged(self):
        class UnhashableText(str):
            __hash__ = None

        for outer, cached in ((discovery._norm, discovery._normalized_text),
                              (discovery._clean_search_title, discovery._clean_search_text)):
            for value in (None, False, 0, [], {}, (), UnhashableText('ADÉLA - Café')):
                with self.subTest(helper=outer.__name__, value=value):
                    self.assertEqual(outer(value), cached.__wrapped__(value))
            for value in (['truthy'], {'truthy': True}, 42):
                with self.subTest(helper=outer.__name__, value=value):
                    try:
                        cached.__wrapped__(value)
                    except Exception as expected:
                        with self.assertRaises(type(expected)):
                            outer(value)
                    else:
                        self.fail('Fixture must exercise the original invalid-input path')

    def test_text_reuse_is_bounded_and_keyed_by_original_string(self):
        for helper in self.helpers:
            first = helper('ADÉLA - Café [140 BPM]')
            self.assertEqual(helper('ADÉLA - Café [140 BPM]'), first)
            self.assertEqual(helper.cache_info().hits, 1)
            self.assertEqual(helper.cache_info().maxsize, 8192)
            for i in range(8193):
                helper(f'Unique fixture text {i}')
            self.assertEqual(helper.cache_info().currsize, 8192)
            misses = helper.cache_info().misses
            self.assertEqual(helper('ADÉLA - Café [140 BPM]'), first)
            self.assertEqual(helper.cache_info().misses, misses + 1)
