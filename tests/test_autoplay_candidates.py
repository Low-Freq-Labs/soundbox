"""Shuffle refills use scalar identities; only drawn candidates check files."""
import random
from types import MethodType, SimpleNamespace
import unittest

from desktop.app import MainWindow


class AutoplayCandidates(unittest.TestCase):
    def window(self, order, available=None):
        available=set(order if available is None else available)
        window=SimpleNamespace(_autoplay_mode='random',_autoplay_order=list(order),_autoplay_index=0,
            _autoplay_random_seen={order[0]} if order else set(),_autoplay_random_queue=[],
            _autoplay_rng=random.Random(7),probed=[])
        def playable(index):
            window.probed.append(window._autoplay_order[index])
            return window._autoplay_order[index] in available
        window._autoplay_playable=playable
        window.next=MethodType(MainWindow._next_autoplay_index,window)
        return window,available

    def accept(self, window, index):
        window._autoplay_index=index
        window._autoplay_random_seen.add(window._autoplay_order[index])

    def test_large_refill_checks_one_drawn_candidate_instead_of_every_library_path(self):
        window,_=self.window([str(i) for i in range(3300)])
        index=window.next()
        self.assertIsNotNone(index)
        self.assertNotEqual(index,0)
        self.assertEqual(window.probed,[window._autoplay_order[index]])

    def test_missing_candidates_are_bounded_and_rechecked_on_a_later_attempt(self):
        window,available=self.window(['current','missing','later'],{'current'})
        self.assertIsNone(window.next())
        self.assertEqual(set(window.probed),{'missing','later'})
        self.assertEqual(len(window.probed),2)
        available.add('later');window.probed.clear()
        self.assertEqual(window.next(),2)
        self.assertIn('later',window.probed)
        self.assertEqual(len(window.probed),len(set(window.probed)))

    def test_cycles_keep_each_playable_identity_once_and_never_repeat_at_boundary(self):
        window,_=self.window(['a','b','a','missing','c','d'],{'a','b','c','d'})
        played=['a']
        for _ in range(3):
            index=window.next();self.assertIsNotNone(index);self.accept(window,index)
            played.append(window._autoplay_order[index])
        self.assertEqual(set(played),{'a','b','c','d'})
        self.assertEqual(len(played),len(set(played)))
        next_index=window.next();self.assertIsNotNone(next_index)
        self.assertNotEqual(window._autoplay_order[next_index],played[-1])

    def test_disappearing_queued_song_is_skipped_without_using_old_availability(self):
        window,available=self.window(['a','b','c'])
        window._autoplay_random_queue=[2,1];available.remove('b')
        self.assertEqual(window.next(),2)
        self.assertEqual(window.probed,['b','c'])

    def test_single_song_empty_and_disabled_modes_do_not_probe_or_repeat(self):
        for order in ([],['a'],['a','a']):
            window,_=self.window(order)
            self.assertIsNone(window.next());self.assertEqual(window.probed,[])
        window,_=self.window(['a','b']);window._autoplay_mode=''
        self.assertIsNone(window.next());self.assertEqual(window.probed,[])

    def test_directional_modes_retain_order_and_skip_unavailable_songs(self):
        window,_=self.window(['a','missing','c'],{'a','c'})
        window._autoplay_mode='down';self.assertEqual(window.next(),2)
        window._autoplay_index=2;window._autoplay_mode='up';self.assertEqual(window.next(),0)
