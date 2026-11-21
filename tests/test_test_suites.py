"""Named test areas stay static, cumulative, explicit and separate from full gates."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from scripts.test_suites import LEVELS,ROOT,load_catalog,normalize_selector,suite_plan


class TestSuitePlanning(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='soundbox-suite-plan-')
        self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        (self.root/'scripts').mkdir();(self.root/'tests').mkdir()
        for module in ('test_alpha','test_beta','test_recovery','test_native_deck'):
            (self.root/'tests'/f'{module}.py').write_text('''raise AssertionError('Catalog planning imported a test module')
class Contract:
    def test_one(self):pass
    def test_two(self):pass
''')
        self.catalog=dict(version=1,suites={
            'library':dict(description='Library and rekordbox',quick=['tests/test_alpha.py'],
                           integration=['test_beta.Contract'],recovery=['test_recovery']),
            'player':dict(description='Playback',quick=['tests.test_native_deck.Contract.test_one'],
                          integration=['test_alpha'],recovery=['test_native_deck.Contract'])})
        self.write(self.catalog)

    def write(self,value):
        (self.root/'scripts/test-suites.json').write_text(json.dumps(value))

    def test_paths_dotted_names_and_direct_focus_need_no_catalog(self):
        for selector in ('tests/test_alpha.py','tests.test_alpha','test_alpha'):
            self.assertEqual(normalize_selector(selector,self.root),'test_alpha')
        self.assertEqual(normalize_selector('tests.test_alpha.Contract.test_one',self.root),
                         'test_alpha.Contract.test_one')
        (self.root/'scripts/test-suites.json').unlink()
        plan=suite_plan([],selectors=['test_alpha.Contract.test_one'],root=self.root)
        self.assertEqual(plan['suites'],[])
        self.assertEqual(plan['phases'],dict(offscreen=['test_alpha.Contract.test_one'],native=[]))
        for selector in ('test_missing','../test_alpha','tests/../test_alpha.py','test_alpha.bad-name',12):
            with self.subTest(selector=selector),self.assertRaises(ValueError):
                normalize_selector(selector,self.root)

    def test_levels_are_cumulative_and_area_union_has_one_canonical_selector(self):
        self.assertEqual(LEVELS,('quick','integration','recovery'))
        plans=[suite_plan(['library'],level,root=self.root) for level in LEVELS]
        self.assertEqual(plans[0]['selectors'],['test_alpha'])
        self.assertEqual(plans[1]['selectors'],['test_alpha','test_beta.Contract'])
        self.assertEqual(plans[2]['selectors'],['test_alpha','test_beta.Contract','test_recovery'])
        combined=suite_plan(['library','player','library'],'integration',
                            selectors=['tests/test_alpha.py'],root=self.root)
        self.assertEqual(combined['suites'],['library','player'])
        self.assertEqual(len(combined['selectors']),3)
        self.assertEqual(combined['selectors'],sorted(combined['selectors']))

    def test_native_and_cocoa_override_routing_do_not_import_test_modules(self):
        plan=suite_plan(['library','player'],root=self.root)
        self.assertEqual(plan['phases']['offscreen'],['test_alpha'])
        self.assertEqual(plan['phases']['native'],['test_native_deck.Contract.test_one'])
        override=suite_plan(['library','player'],cocoa=True,root=self.root)
        self.assertEqual(override['phases']['offscreen'],[])
        self.assertEqual(override['phases']['native'],plan['selectors'])
        # Every generated module raises at import. Reaching these assertions also
        # proves catalog inspection and planning used static parsing only.
        self.assertEqual(load_catalog(self.root)['player']['quick'],
                         ['test_native_deck.Contract.test_one'])

    def test_bad_catalog_structure_duplicate_keys_and_static_refs_are_rejected(self):
        changes=(lambda r:r.update(version=True),lambda r:r.update(version=2),
                 lambda r:r.update(extra=True),lambda r:r.update(suites={}),
                 lambda r:r['suites'].update({'bad key':r['suites']['library']}),
                 lambda r:r['suites']['library'].update(deep=[]),
                 lambda r:r['suites']['library'].pop('integration'),
                 lambda r:r['suites']['library'].update(description=' '),
                 lambda r:r['suites']['library'].update(quick=[]),
                 lambda r:r['suites']['library'].update(integration='test_beta'),
                 lambda r:r['suites']['library'].update(integration=['test_alpha']),
                 lambda r:r['suites']['library'].update(integration=['test_missing']),
                 lambda r:r['suites']['library'].update(integration=['test_beta.Missing']),
                 lambda r:r['suites']['library'].update(integration=['test_beta.Contract.test_missing']))
        for change in changes:
            document=deepcopy(self.catalog);change(document);self.write(document)
            with self.subTest(change=change),self.assertRaises(ValueError):load_catalog(self.root)
        (self.root/'scripts/test-suites.json').write_text('{"version":1,"version":1,"suites":{}}')
        with self.assertRaisesRegex(ValueError,'Duplicate catalog key'):load_catalog(self.root)

    def test_unknown_area_level_and_empty_selections_fail_without_guessing(self):
        for args in ((['missing'],'quick'),(['library'],'deep'),([], 'quick')):
            with self.subTest(args=args),self.assertRaises(ValueError):
                suite_plan(*args,root=self.root)
        for names,selectors in (('library',()),([], 'test_alpha'),([3],())):
            with self.subTest(names=names,selectors=selectors),self.assertRaises(ValueError):
                suite_plan(names,selectors=selectors,root=self.root)


class MaintainedTestSuiteCatalog(unittest.TestCase):
    def test_every_test_module_has_a_named_area_and_all_references_are_static_valid(self):
        catalog=load_catalog()
        covered={selector for suite in catalog.values()
                 for level in LEVELS for selector in suite[level] if '.' not in selector}
        discovered={path.stem for path in (ROOT/'tests').glob('test_*.py') if path.is_file()}
        self.assertEqual(covered,discovered,
                         'The deepest union must cover every complete test module, not only a sample method')
