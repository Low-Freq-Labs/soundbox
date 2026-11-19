"""Grouped COW patches match legacy sequential semantics without bulk quadratic copies."""
from copy import deepcopy
from itertools import combinations
import unittest

from soundbox.state_repository import InvalidState, apply_state_patch


def legacy(document,replacements,removals):
    # Independent original sequential implementation, deliberately not calling
    # the grouped implementation or sharing its traversal logic.
    for path,value,remove in [(p,v,False) for p,v in replacements.items()]+[(p,None,True) for p in removals]:
        candidate=dict(document);parent=candidate
        for key in path[:-1]:
            if remove and key not in parent:break
            previous=parent.get(key,{})
            if not isinstance(previous,dict):raise InvalidState('State patch crosses a non-dictionary value')
            child=dict(previous);parent[key]=child;parent=child
        else:
            if remove:parent.pop(path[-1],None)
            else:parent[path[-1]]=value
            document=candidate
    return document


class GroupedStatePatch(unittest.TestCase):
    def test_generated_shared_disjoint_missing_and_nested_operations_match_sequential_values_and_order(self):
        document={'settings':{'ranks':{'scope':{'a':1,'b':2},'other':{'z':3}},'scalar':7},
                  'titles':{'old':'old'},'untouched':{'nested':[1,2]}}
        paths=(('settings','ranks','scope','a'),('settings','ranks','scope','new'),
               ('settings','ranks','absent','new'),('titles','old'),('titles','new'),('missing','leaf'))
        for count in range(4):
            for selected in combinations(paths,count):
                for split in range(count+1):
                    replacements={path:{'new':['value']} for path in selected[:split]}
                    removals=selected[split:]
                    before=deepcopy(document)
                    expected=legacy(document,replacements,removals)
                    actual=apply_state_patch(document,replacements,removals)
                    self.assertEqual(actual,expected)
                    # Dictionary insertion order is part of serialized state.
                    self.assertEqual(repr(actual),repr(expected))
                    self.assertEqual(document,before)
                    self.assertIs(actual['untouched'],document['untouched'])

    def test_invalid_crossing_and_overlaps_keep_original_policy(self):
        for replacements,removals in (({('a','b'):1},()),({},(('a','b'),))):
            with self.assertRaises(InvalidState):apply_state_patch({'a':7},replacements,removals)
            with self.assertRaises(InvalidState):legacy({'a':7},replacements,removals)
        for replacements,removals in (({('a',):1,('a','b'):2},()),
                ({('a',):1},(('a',),)),({},(('a',),('a','b'))),
                ({('',):1},()),({('a',1):1},())):
            with self.assertRaises(InvalidState):apply_state_patch({},replacements,removals)

    def test_missing_removal_keeps_unaffected_owners_and_non_dictionary_errors(self):
        document={'branch':{'old':1},'scalar':3}
        self.assertIs(apply_state_patch(document,{},(('absent','path'),)),document)
        self.assertIs(apply_state_patch(document,{},(('branch','missing'),)),document)
        changed=apply_state_patch(document,{('new','leaf'):2},(('branch','missing'),))
        self.assertIs(changed['branch'],document['branch']);self.assertEqual(changed['new'],{'leaf':2})

    def test_bulk_16384_promotions_copy_each_of_five_affected_ancestors_once(self):
        copies=[]
        class Counted(dict):
            def __iter__(self):return super().__iter__()
            def keys(self):copies.append(id(self));return super().keys()
        ranks=Counted({f'u{i}':1 for i in range(100000)})
        scopes=Counted(scope=ranks,untouched={'other':1})
        recency=Counted(version=1,sequence=1,playlists=scopes)
        settings=Counted(smart_playlist_recency=recency,other={'retained':True})
        document=Counted(settings=settings,other={'retained':True})
        replacements={('settings','smart_playlist_recency','playlists','scope',f'u{i}'):2
                      for i in range(16384)}
        result=apply_state_patch(document,replacements)
        self.assertEqual(len(copies),5);self.assertEqual(set(copies),{id(v) for v in (document,settings,recency,scopes,ranks)})
        self.assertIs(result['other'],document['other'])
        self.assertIs(result['settings']['other'],settings['other'])
        self.assertIs(result['settings']['smart_playlist_recency']['playlists']['untouched'],scopes['untouched'])
        actual=result['settings']['smart_playlist_recency']['playlists']['scope']
        self.assertEqual(sum(value==2 for value in actual.values()),16384)
        self.assertEqual(ranks['u0'],1);self.assertEqual(actual['u99999'],1)
