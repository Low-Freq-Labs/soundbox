"""Skipped pure reconstruction retains fresh source/target and policy proofs."""
from copy import deepcopy
from types import SimpleNamespace
import json
import unittest
from unittest.mock import patch

from tests import test_cdj_integration as fixtures
from soundbox import cdj_export, cdj_incremental, cdj_transaction, my_settings
from soundbox.cdj_usb import CATALOG_RENDER_POLICY, DATABASE_FILES, digest


class CdjNoop(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.CdjIntegrationTests()
        self.fixture.setUpClass();self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        first=self.fixture.plan();cdj_transaction.commit(first);first.close()

    def plan(self, captures=None):
        case=self.fixture
        with patch('soundbox.cdj_source.capture_metadata',
                   side_effect=captures or (lambda *_a,**_k:deepcopy(case.request))) as capture:
            plan=cdj_export.plan_export(case.lib,[case.playlist],case.target,token=case.token,
                volume_uuid=case.volume,recovery_root=case.recovery)
        case.plans.append(plan)
        self.assertEqual(capture.call_count,2)
        return plan

    def test_repeated_export_skips_renderer_but_keeps_two_captures_and_target_preflight(self):
        target=self.fixture.target
        paths=[*DATABASE_FILES,cdj_transaction.MANIFEST]
        before={p:(target/p).read_bytes() for p in paths}
        original=cdj_incremental.make_plan
        with patch.object(cdj_incremental,'run_renderer',side_effect=AssertionError('No-op rendered')), \
                patch.object(cdj_incremental,'make_plan',wraps=original) as preflight:
            plan=self.plan()
        self.assertEqual(preflight.call_count,1)
        self.assertEqual(plan.changes,())
        self.assertFalse(plan.requires_commit)
        self.assertEqual({p:(target/p).read_bytes() for p in paths},before)

    def test_display_membership_ids_never_enter_export_or_settings_manifests(self):
        case=self.fixture
        manifest=json.loads((case.target/cdj_transaction.MANIFEST).read_text())
        self.assertNotIn('playlist_track_ids',manifest['view'])
        case.request['tracks'][0]['title']='Changed native fixture'
        changed=self.plan()
        self.assertNotIn('playlist_track_ids',changed.manifest['view'])
        cdj_transaction.commit(changed)
        settings=cdj_export.plan_settings(case.target,{'device_name':'USB fixture name'},
            token=case.token,volume_uuid=case.volume,recovery_root=case.recovery)
        case.plans.append(settings)
        self.assertNotIn('playlist_track_ids',settings.manifest['view'])
        cdj_transaction.commit(settings)
        repeat=self.plan()
        self.assertEqual(repeat.changes,())
        self.assertFalse(repeat.requires_commit)

    def test_source_change_at_second_capture_fails_before_any_skipped_render(self):
        changed=deepcopy(self.fixture.request);changed['source_revision']='changed'
        with patch.object(cdj_incremental,'run_renderer',side_effect=AssertionError('Rendered')):
            with self.assertRaisesRegex(ValueError,'Source metadata/cues/grid changed'):
                self.plan([deepcopy(self.fixture.request),changed])

    def test_target_change_after_noop_predicate_still_fails_preflight(self):
        original=cdj_incremental.make_plan
        def changed(*args,**kwargs):
            path=self.fixture.target/DATABASE_FILES[0]
            raw=path.read_bytes();path.write_bytes(bytes([raw[0]^1])+raw[1:])
            return original(*args,**kwargs)
        with patch.object(cdj_incremental,'make_plan',side_effect=changed), \
                patch.object(cdj_incremental,'run_renderer',side_effect=AssertionError('Rendered')):
            with self.assertRaisesRegex(ValueError,'changed outside SoundBox'):
                self.plan()

    def test_old_or_missing_policy_rebuilds_and_publishes_new_policy(self):
        for policy in (None,'old-policy'):
            with self.subTest(policy=policy):
                path=self.fixture.target/cdj_transaction.MANIFEST
                value=json.loads(path.read_text())
                if policy is None:value.pop('render_policy',None)
                else:value['render_policy']=policy
                path.write_text(json.dumps(value))
                original=cdj_incremental.run_renderer
                with patch.object(cdj_incremental,'run_renderer',wraps=original) as renderer:
                    plan=self.plan()
                self.assertIn('merge',[call.args[0] for call in renderer.call_args_list])
                self.assertEqual(plan.manifest['render_policy'],CATALOG_RENDER_POLICY)
                self.assertTrue(plan.requires_commit)
                cdj_transaction.commit(plan)

    def test_changed_track_extra_still_uses_full_render_and_merge(self):
        self.fixture.request['track_extras'][0]['cue_update_count']+=1
        original=cdj_incremental.run_renderer
        with patch.object(cdj_incremental,'run_renderer',wraps=original) as renderer:
            plan=self.plan()
        self.assertEqual([call.args[0] for call in renderer.call_args_list],['render','merge'])
        self.assertTrue(plan.requires_commit)

    def test_selected_render_request_retains_root_values_and_detaches_mutable_inputs(self):
        captured = self.fixture.request
        captured['tracks'][0]['title'] = 'Changed selected native song'
        retained = {'nested': ['retained']}
        captured.update(retained_first=retained, retained_second=retained)
        first, second = deepcopy(captured), deepcopy(captured)
        roots = []
        def copied(value, *args, **kwargs):
            result = deepcopy(value, *args, **kwargs)
            if isinstance(value, dict) and value.get('source_revision') == captured['source_revision']:
                roots.append(result)
            return result
        original_renderer = cdj_incremental.run_renderer
        def render(command, *args, **kwargs):
            if command == 'render':
                request = roots[-1]
                self.assertEqual(list(request)[:len(captured)], list(captured))
                self.assertEqual(request['retained_first'], retained)
                self.assertIs(request['retained_first'], request['retained_second'])
                self.assertIsNot(request['retained_first'], first['retained_first'])
                self.assertEqual(request['tracks'][0]['title'], 'Changed selected native song')
                self.assertIsNot(request['tracks'][0], first['tracks'][0])
                self.assertIsNot(request['tracks'][0]['cues'], first['tracks'][0]['cues'])
                self.assertIs(request['track_extras'][0], first['track_extras'][0])
                request['retained_first']['nested'].append('render-private')
            return original_renderer(command, *args, **kwargs)
        with patch.object(cdj_incremental, 'deepcopy', side_effect=copied), \
                patch.object(cdj_incremental, 'run_renderer', side_effect=render):
            plan = self.plan([first, second])
        self.assertTrue(plan.requires_commit)
        self.assertEqual(retained, {'nested': ['retained']})
        self.assertEqual(first['retained_first'], retained)
        self.assertEqual(captured['tracks'][0]['title'], 'Changed selected native song')

    def change_player_profile(self):
        path=self.fixture.target/'PIONEER/MYSETTING.DAT'
        raw=path.read_bytes()
        path.write_bytes(my_settings.patch(path.name,raw,{'tempo_range':'wide'},expected_sha256=digest(raw)))
        return path

    def payloads(self):
        target=self.fixture.target
        return {path.relative_to(target).as_posix():path.read_bytes()
                for folder in ('PIONEER','Contents') for path in (target/folder).rglob('*') if path.is_file()}

    def test_player_profile_change_refreshes_only_manifest_once_then_skips_renderer(self):
        profile=self.change_player_profile();before=self.payloads()
        manifest_path=self.fixture.target/cdj_transaction.MANIFEST
        original_manifest=manifest_path.read_bytes()
        self.assertEqual(cdj_export.status_hint(self.fixture.lib,self.fixture.target,token=self.fixture.token,
                                               volume_uuid=self.fixture.volume),0)
        self.assertEqual(manifest_path.read_bytes(),original_manifest)
        original=cdj_incremental.run_renderer
        with patch.object(cdj_incremental,'run_renderer',wraps=original) as renderer:
            plan=self.plan()
        self.assertEqual([call.args[0] for call in renderer.call_args_list],['merge'])
        self.assertEqual(plan.changes,())
        self.assertTrue(plan.requires_commit)
        self.assertEqual(plan.manifest['files']['PIONEER/MYSETTING.DAT']['sha256'],digest(profile.read_bytes()))
        for path in DATABASE_FILES:
            self.assertEqual(plan.manifest['files'][path]['sha256'],digest(before[path]))
        write=cdj_transaction.CdjSession.atomic_write
        publications=[]
        def record(session,path,data):
            publications.append(path.relative_to(session.target).as_posix())
            return write(session,path,data)
        with patch.object(cdj_transaction.CdjSession,'atomic_write',record), \
                patch.object(cdj_transaction.CdjSession,'atomic_copy',side_effect=AssertionError('Payload write')), \
                patch.object(cdj_transaction.CdjSession,'rename',side_effect=AssertionError('Payload rename')):
            cdj_transaction.commit(plan)
        self.assertEqual(set(publications),{cdj_transaction.JOURNAL,cdj_transaction.MANIFEST})
        self.assertEqual(self.payloads(),before)
        with patch.object(cdj_incremental,'run_renderer',side_effect=AssertionError('Repeated profile render')):
            next_plan=self.plan()
        self.assertFalse(next_plan.requires_commit)
        self.assertEqual(next_plan.changes,())

    def test_profile_manifest_failure_recovers_and_retry_keeps_all_payloads_exact(self):
        self.change_player_profile();before=self.payloads()
        manifest_path=self.fixture.target/cdj_transaction.MANIFEST
        old=manifest_path.read_bytes();plan=self.plan()
        write=cdj_transaction.CdjSession.atomic_write;failed=False
        def fail_once(session,path,data):
            nonlocal failed
            if path==manifest_path and not failed:
                failed=True;raise OSError('Synthetic manifest storage failure')
            return write(session,path,data)
        with patch.object(cdj_transaction.CdjSession,'atomic_write',fail_once):
            with self.assertRaisesRegex(OSError,'Synthetic manifest storage failure'):
                cdj_transaction.commit(plan)
        self.assertEqual(manifest_path.read_bytes(),old)
        self.assertFalse((self.fixture.target/cdj_transaction.JOURNAL).exists())
        self.assertEqual(self.payloads(),before)
        retry=self.plan();self.assertTrue(retry.requires_commit)
        cdj_transaction.commit(retry)
        self.assertEqual(self.payloads(),before)
        with patch.object(cdj_incremental,'run_renderer',side_effect=AssertionError('Repeated profile render')):
            self.assertFalse(self.plan().requires_commit)

    def test_profile_change_during_candidate_preflight_still_fails_closed(self):
        self.change_player_profile()
        original=cdj_incremental.make_plan
        def changed(*args,**kwargs):
            path=self.fixture.target/'PIONEER/MYSETTING.DAT';raw=path.read_bytes()
            path.write_bytes(bytes([raw[0]^1])+raw[1:])
            return original(*args,**kwargs)
        with patch.object(cdj_incremental,'make_plan',side_effect=changed):
            with self.assertRaisesRegex(ValueError,'USB settings changed during preparation'):
                self.plan()

    def test_newer_manifest_before_or_after_preflight_is_never_adopted_as_old_authority(self):
        self.change_player_profile();before=self.payloads()
        path=self.fixture.target/cdj_transaction.MANIFEST
        original_payload=path.read_bytes();original=cdj_incremental.make_plan
        def replace_manifest():
            newer=json.loads(original_payload);newer['next_track_id']+=1000
            raw=json.dumps(newer).encode();path.write_bytes(raw);return raw
        def changed_before(*args,**kwargs):
            replace_manifest()
            return original(*args,**kwargs)
        with patch.object(cdj_incremental,'make_plan',side_effect=changed_before):
            with self.assertRaisesRegex(ValueError,'manifest changed during preparation'):
                self.plan()
        newer_payload=path.read_bytes()
        self.assertNotEqual(newer_payload,original_payload)
        self.assertEqual(self.payloads(),before)
        path.write_bytes(original_payload)
        def changed_after(*args,**kwargs):
            prepared=original(*args,**kwargs)
            replace_manifest()
            return prepared
        with patch.object(cdj_incremental,'make_plan',side_effect=changed_after):
            plan=self.plan()
        self.assertEqual(plan.old_manifest,original_payload)
        newer_payload=path.read_bytes()
        with patch.object(cdj_transaction.CdjSession,'atomic_write',side_effect=AssertionError('Premature publication')):
            with self.assertRaisesRegex(ValueError,'manifest changed after approval'):
                cdj_transaction.commit(plan)
        self.assertEqual(path.read_bytes(),newer_payload)
        self.assertEqual(self.payloads(),before)

    def test_manifest_reader_pairs_one_bounded_payload_with_its_validated_model(self):
        case=self.fixture;path=case.target/cdj_transaction.MANIFEST
        original_payload=path.read_bytes();newer=json.loads(original_payload);newer['next_track_id']+=1000
        newer_payload=json.dumps(newer).encode()
        session=cdj_transaction.CdjSession(case.target,[],token=case.token,volume_uuid=case.volume)
        original=session.read_optional;reads=[]
        def replaced(relative, *, limit):
            raw=original(relative,limit=limit)
            if relative==cdj_transaction.MANIFEST:
                reads.append((relative,limit));path.write_bytes(newer_payload)
            return raw
        with patch.object(session,'read_optional',side_effect=replaced):
            value,raw=cdj_transaction.read_manifest(session)
        self.assertEqual(reads,[(cdj_transaction.MANIFEST,8*1024*1024)])
        self.assertEqual(raw,original_payload)
        self.assertEqual(value,json.loads(original_payload))
        self.assertEqual(path.read_bytes(),newer_payload)

    def test_explicit_settings_patch_never_overwrites_newer_unselected_player_fields(self):
        path=self.fixture.target/'PIONEER/MYSETTING.DAT'
        payloads_before=self.payloads()
        raw=path.read_bytes();fields=my_settings.inspect(path.name,raw)['fields']
        key=next(k for k,v in fields.items() if k!='tempo_range' and len(v['choices'])>1 and v['value'] is not None)
        token=next(choice for choice in fields[key]['choices'] if choice!=fields[key]['value'])
        newer=my_settings.patch(path.name,raw,{key:token},expected_sha256=digest(raw))
        original=cdj_export.make_plan
        def settings_plan():
            plan=cdj_export.plan_settings(self.fixture.target,{'my_settings':{path.name:{'tempo_range':'wide'}}},
                token=self.fixture.token,volume_uuid=self.fixture.volume,recovery_root=self.fixture.recovery)
            self.fixture.plans.append(plan)
            return plan
        def before(*args,**kwargs):
            path.write_bytes(newer)
            return original(*args,**kwargs)
        with patch.object(cdj_export,'make_plan',side_effect=before):
            with self.assertRaisesRegex(ValueError,'USB settings changed during preparation'):
                settings_plan()
        self.assertEqual(path.read_bytes(),newer)
        self.assertEqual(self.payloads(),{**payloads_before,'PIONEER/MYSETTING.DAT':newer})
        path.write_bytes(raw)
        def missing(*args,**kwargs):
            path.unlink()
            return original(*args,**kwargs)
        with patch.object(cdj_export,'make_plan',side_effect=missing):
            with self.assertRaisesRegex(ValueError,'USB settings changed during preparation'):
                settings_plan()
        self.assertFalse(path.exists())
        path.write_bytes(raw)
        def after(*args,**kwargs):
            prepared=original(*args,**kwargs)
            path.write_bytes(newer)
            return prepared
        with patch.object(cdj_export,'make_plan',side_effect=after):
            plan=settings_plan()
        payloads=self.payloads()
        with patch.object(cdj_transaction.CdjSession,'atomic_write',side_effect=AssertionError('Premature publication')):
            with self.assertRaisesRegex(ValueError,'USB changed after approval'):
                cdj_transaction.commit(plan)
        self.assertEqual(self.payloads(),payloads)
        self.assertEqual(path.read_bytes(),newer)


class CatalogReuseContract(unittest.TestCase):
    def test_input_signature_binds_entire_dto_extras_art_and_every_source_epoch(self):
        dto=dict(id=1,source_path='/synthetic/song.wav',analysis_source='/synthetic/ANLZ0000.DAT',
                 cues=[dict(kind=0,time_ms=100)],my_tags=[1],title='Fixture')
        extra=dict(source_id=1,cue_update_count=1,analysis_data_update_count=2,information_update_count=3)
        epochs={path:[1,index,100,1000] for index,path in enumerate(
            ['/synthetic/song.wav',*['/synthetic/ANLZ0000.'+ext for ext in ('DAT','EXT','2EX')]])}
        with patch.object(cdj_incremental,'source_epoch',side_effect=lambda path:epochs[str(path)]):
            original=cdj_incremental.input_signature(dto,extra,'art')
            self.assertNotEqual(cdj_incremental.input_signature(dto,{**extra,'cue_update_count':2},'art'),original)
            self.assertNotEqual(cdj_incremental.input_signature({**dto,'cues':[]},extra,'art'),original)
            self.assertNotEqual(cdj_incremental.input_signature({**dto,'my_tags':[2]},extra,'art'),original)
            self.assertNotEqual(cdj_incremental.input_signature(dto,extra,'new-art'),original)
            for path in epochs:
                epochs[path][-1]+=1
                self.assertNotEqual(cdj_incremental.input_signature(dto,extra,'art'),original)
                epochs[path][-1]-=1

    def test_every_database_profile_byte_and_policy_is_required(self):
        files=tuple((path,path.encode()) for path in [*DATABASE_FILES,'PIONEER/MYSETTING.DAT','PIONEER/djprofile.nxs'])
        native=SimpleNamespace(files=files)
        committed=dict(render_policy=CATALOG_RENDER_POLICY,
                       files={p:dict(size=len(raw),sha256=digest(raw)) for p,raw in files})
        self.assertTrue(cdj_incremental._catalog_bytes_unchanged(native,committed))
        for index,(path,raw) in enumerate(files):
            altered=[*files];altered[index]=(path,raw+b'changed opaque bytes')
            self.assertFalse(cdj_incremental._catalog_bytes_unchanged(SimpleNamespace(files=altered),committed))
        self.assertFalse(cdj_incremental._catalog_bytes_unchanged(SimpleNamespace(files=files[:2]),committed))
        for policy in (None,'old-policy'):
            self.assertFalse(cdj_incremental._catalog_bytes_unchanged(native,{**committed,'render_policy':policy}))

    def test_order_duplicate_membership_and_unambiguous_full_track_set_are_required(self):
        view=dict(track_rows=[dict(id='1',audio='/one'),dict(id='2',audio='/two')],
                  playlists=[dict(id='3',parent='0',title='Fixture',folder=False,
                                  entries=[('One','/one'),('Two','/two'),('One','/one')])])
        catalog=dict(nodes=[dict(id=3,parent=0,name='Fixture',folder=False,tracks=[1,2,1])],track_ids={1,2})
        self.assertTrue(cdj_incremental._catalog_unchanged(catalog,view))
        changed=deepcopy(catalog);changed['nodes'][0]['tracks']=[1,1,2]
        self.assertFalse(cdj_incremental._catalog_unchanged(changed,view))
        self.assertFalse(cdj_incremental._catalog_unchanged({**catalog,'track_ids':{1}},view))
        view['track_rows'].append(dict(id='3',audio='/two'))
        self.assertFalse(cdj_incremental._catalog_unchanged(catalog,view))
