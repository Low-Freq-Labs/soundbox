"""A renewed omission witness cannot bypass an older full-source shadow."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from desktop.export_dialog import captured_ipod_noop_result
from soundbox import manifest,stock_ipod
from soundbox.stock_ipod_info import read_stock_ipod_snapshot
from soundbox.stock_plan_observations import NegativeSourceOmissions,STOCK_SOURCE_PROFILE
from soundbox.stock_sync_reuse import stock_selection_reusable,stock_shadow_selection_current
from soundbox.sync import DeviceSession
from tests import test_stock_noop_acceptance_guards as fixtures


class StockShadowNoopRouting(unittest.TestCase):
    def setUp(self):
        # Reuse requires canonical source identities; macOS /var is an alias
        # of /private/var, so create the disposable fixture under its real root.
        self.enterContext(mock.patch.object(tempfile,'tempdir',str(Path(tempfile.gettempdir()).resolve())))
        self.case=fixtures.StockNoopAcceptanceGuards();self.case.setUp()
        self.addCleanup(self.case.doCleanups)

    def test_witness_only_omission_completion_requires_full_shadow_acceptance_then_stays_clean(self):
        case=self.case;plan,native_before=case.pending_omission()
        shadow_before=case.case.shadow.path_for(case.case.key).read_bytes()
        # Reproduce the installed older completion: verified native readback
        # renews only the witness, leaving full-source shadow membership old.
        session=DeviceSession(case.f.ipod,list(plan.source_guard_paths),create=False)
        witness,warning=stock_ipod.committed_stock_export_witness(plan,session)
        self.assertFalse(warning);self.assertIsNotNone(witness)
        case.store.save(witness)
        self.assertEqual(case.case.shadow.path_for(case.case.key).read_bytes(),shadow_before)
        self.assertEqual(case.status(witness).state,'local_changes')
        native=stock_ipod.capture_stock_ipod_native(case.f.ipod,include_artwork=True)
        snapshot=read_stock_ipod_snapshot(case.f.ipod,volume_uuid='generated-volume',native_capture=native)
        shadow=case.case.shadow.load(case.case.key)
        omissions=NegativeSourceOmissions(root=case.omissions,profile=STOCK_SOURCE_PROFILE)
        self.assertTrue(stock_selection_reusable(case.f.library,[case.f.playlist],snapshot,
                                                witness,shadow,omissions))
        self.assertFalse(stock_shadow_selection_current(case.f.library,[case.f.playlist],shadow))
        retry=case.plan(witness)
        self.assertFalse(retry.requires_commit)
        self.assertEqual(case.case.shadow.path_for(case.case.key).read_bytes(),shadow_before)
        accepted=captured_ipod_noop_result(retry,case.store)
        current=case.case.shadow.load(case.case.key)
        self.assertTrue(stock_shadow_selection_current(case.f.library,[case.f.playlist],current))
        self.assertEqual(case.status(accepted.catalog_witness).state,'up_to_date')
        self.assertEqual(case.native_snapshot(),native_before)
        self.assertEqual(current.committed_at,shadow.committed_at)
        self.assertEqual(current.generation,shadow.generation)

    def test_shadow_guard_checks_order_labels_and_projection_without_media_reads(self):
        case=self.case;library=case.f.library;playlist=case.f.playlist
        shadow=case.case.shadow.load(case.case.key)
        self.assertTrue(stock_shadow_selection_current(library,[playlist],shadow))
        row=manifest.entry_for(playlist)
        for field,value in [('tracks',['different']),('sig','different'),('name','different'),
                            ('folder_path',['different']),('pid','different')]:
            with self.subTest(field=field):
                changed=replace(shadow,playlists={manifest.playlist_key(playlist):{**row,field:value}})
                self.assertFalse(stock_shadow_selection_current(library,[playlist],changed))
        self.assertFalse(stock_shadow_selection_current(library,[playlist,playlist],shadow))
        self.assertFalse(stock_shadow_selection_current(library,[playlist],None))
        self.assertFalse(stock_shadow_selection_current(library,[],shadow))
        with_device_name=replace(shadow,playlists={manifest.playlist_key(playlist):{**row,'device_name':'Leaf'}})
        self.assertTrue(stock_shadow_selection_current(library,[playlist],with_device_name))
        library.hidden_ids=frozenset({'old'})
        self.assertFalse(stock_shadow_selection_current(library,[playlist],shadow))
