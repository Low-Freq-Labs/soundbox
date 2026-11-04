"""Operation-local artwork indexing preserves ascending allocation and retries."""
import random
import unittest

from soundbox.stock_artwork import _FreeArtworkSlots


class FreeArtworkSlots(unittest.TestCase):
    def test_partial_scan_reuses_first_duplicate_match_and_first_free_offset(self):
        slots=_FreeArtworkSlots([0,10,20,30,40])
        values={0:'red',10:'blue',20:'red',30:'green',40:'blue'}
        reads=[]
        def read(offset):
            reads.append(offset)
            return values[offset]
        self.assertEqual(slots.pop_matching('blue',read),10)
        self.assertEqual(reads,[0,10])
        self.assertEqual(slots.pop_matching('red',read),0)
        self.assertEqual(reads,[0,10])
        self.assertEqual(slots.pop_matching('absent',read),None)
        self.assertEqual(reads,[0,10,20,30,40])
        self.assertEqual(slots.pop_first(),20)
        self.assertEqual(slots.pop_matching('blue',read),40)
        self.assertEqual(slots.pop_matching('red',read),None)
        self.assertEqual(slots.pop_first(),30)
        self.assertIsNone(slots.pop_first())

    def test_failed_read_retries_before_cached_later_match_and_retains_other_failures(self):
        slots=_FreeArtworkSlots([0,10,20,30])
        attempts={}
        def read(offset):
            attempts[offset]=attempts.get(offset,0)+1
            if offset in (0,20) and attempts[offset]==1:
                return None
            return 'same'
        self.assertEqual(slots.pop_matching('absent',read),None)
        self.assertEqual(slots.pop_matching('same',read),0)
        self.assertEqual(attempts,{0:2,10:1,20:1,30:1})
        self.assertEqual(slots.pop_matching('same',read),10)
        self.assertEqual(attempts[20],1)
        self.assertEqual(slots.pop_matching('same',read),20)
        self.assertEqual(attempts[20],2)
        self.assertEqual(slots.pop_matching('same',read),30)

    def test_generated_allocations_equal_original_linear_search_with_transient_failures(self):
        for seed in range(40):
            rng=random.Random(seed)
            offsets=list(range(50))
            values={offset:str(rng.randrange(8)) for offset in offsets}
            failures={offset:rng.randrange(3) for offset in offsets}
            attempts=[{},{}]
            def reader(which,offset):
                tries=attempts[which].get(offset,0)+1;attempts[which][offset]=tries
                return None if tries<=failures[offset] else values[offset]
            old=list(offsets);known={};new=_FreeArtworkSlots(offsets)
            for _ in range(60):
                digest=str(rng.randrange(10));matched=None
                for offset in old:
                    value=known.get(offset)
                    if value is None:
                        value=reader(0,offset)
                        if value is not None:known[offset]=value
                    if value==digest:
                        matched=offset;old.remove(offset);break
                actual=new.pop_matching(digest,lambda offset:reader(1,offset))
                self.assertEqual(actual,matched,(seed,digest))
                if matched is None:
                    self.assertEqual(new.pop_first(),old.pop(0) if old else None)
