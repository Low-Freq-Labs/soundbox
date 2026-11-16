"""Tag display recency follows verified identities without source reordering."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtCore import QItemSelectionModel,Qt
from PySide6.QtWidgets import QApplication,QTableView

from soundbox import smart_playlist_order as order
from soundbox.library import Library,LibraryTag,Playlist,Track


def library():
    tracks={identity:Track(identity,name,'',f'file:///synthetic/{identity}.wav',
        source_uuid='uuid-'+identity,source_tag_ids=[],added_ts=timestamp)
        for identity,name,timestamp in [('one','Zebra',1),('two','Alpha',2),('three','Middle',3)]}
    rule=('<NODE Id="1" LogicalOperator="1" AutomaticUpdate="0">'
          '<CONDITION PropertyName="myTag" Operator="8" ValueLeft="11"/>'
          '</NODE>')
    return Library(tracks,[Playlist('Smart',[],['three','two'],'100'),
                           Playlist('Regular',[],['three','two'],'200')],
        Path('/synthetic/master.db'),source_kind='rekordbox-db',source_library_id='source-library',
        source_tags=[LibraryTag('11','Dubstep',source_uuid='tag-uuid')],
        source_playlist_catalog={'nodes':[
            dict(ID='100',UUID='smart-uuid',Attribute=4,rb_local_deleted=0,SmartList=rule),
            dict(ID='200',UUID='regular-uuid',Attribute=0,rb_local_deleted=0,SmartList=None)]})


def verified(before,identity='one'):
    result=deepcopy(before);result.tracks_by_id[identity].source_tag_ids=['11']
    result.playlists[0].keys.append(identity)
    return result


class SmartPlaylistOrder(unittest.TestCase):
    def test_existing_native_assignments_reverse_collection_order_without_local_history(self):
        current=library()
        # These are the relative source dates from the reported five-track case.
        names=['Mocha','IF I DIE YOUNG','SUPERSONIC','HOODRAT SHIT','STINK MODE']
        dates=[539.969,555.008,623.838,630.610,637.420]
        current.tracks_by_id={str(i):Track(str(i),name,'',f'file:///synthetic/{i}.wav',
            source_uuid=f'uuid-{i}',source_tag_ids=['11'],source_tag_times={'11':date},
            added_ts=100-i) for i,(name,date) in enumerate(zip(names,dates))}
        current.playlists[0].keys=list(current.tracks_by_id)
        rows=[{'id':identity} for identity in current.tracks_by_id]
        pristine=deepcopy(current)
        actual=order.order_rows(rows,current,'100',None)
        self.assertEqual([current.tracks_by_id[row['id']].name for row in actual],names[::-1])
        self.assertEqual(current,pristine)

    def test_only_matching_active_tags_supply_dates_and_equal_dates_keep_default_order(self):
        current=library();current.playlists[0].keys=['one','two','three']
        for track in current.tracks_by_id.values():track.source_tag_ids=['11','22']
        current.tracks_by_id['one'].source_tag_times={'11':10,'22':1000}
        current.tracks_by_id['two'].source_tag_times={'11':20}
        current.tracks_by_id['three'].source_tag_times={'11':20}
        rows=[{'id':identity} for identity in ('three','two','one')]
        self.assertEqual(order.order_rows(rows,current,'100',None),rows)
        # Removing the relevant tag invalidates its old date, even if another tag remains.
        current.tracks_by_id['three'].source_tag_ids=['22']
        self.assertEqual([r['id'] for r in order.order_rows(rows,current,'100',None)],['two','one','three'])

    def test_source_dates_win_over_local_ranks_and_corrupt_preferences(self):
        before=library();current=verified(before)
        ranks=order.record(None,order.capture(before,['one'],'11',True),current)
        current.tracks_by_id['two'].source_tag_ids=['11']
        current.tracks_by_id['one'].source_tag_times={'11':10}
        current.tracks_by_id['two'].source_tag_times={'11':20}
        rows=[{'id':identity} for identity in ('three','two','one')]
        for state in (ranks,{'unknown':'state'}):
            self.assertEqual([r['id'] for r in order.order_rows(rows,current,'100',state)],['two','one','three'])

    def test_any_positive_rule_tag_can_supply_latest_date_with_signed_native_ids(self):
        current=library();current.playlists[0].keys=['one','two','three']
        current.source_tags.append(LibraryTag('2275574927','Second',source_uuid='second-tag'))
        current.source_playlist_catalog['nodes'][0]['SmartList']=(
            '<NODE LogicalOperator="2"><CONDITION PropertyName="myTag" Operator="8" ValueLeft="11"/>'
            '<CONDITION PropertyName="myTag" Operator="8" ValueLeft="-2019392369"/>'
            '<CONDITION PropertyName="myTag" Operator="9" ValueLeft="33"/></NODE>')
        for identity,assigned,dates in (
                ('one',['11','2275574927'],{'11':10,'2275574927':30}),
                ('two',['11'],{'11':20}),
                ('three',['33'],{'33':1000})):
            current.tracks_by_id[identity].source_tag_ids=assigned
            current.tracks_by_id[identity].source_tag_times=dates
        rows=[{'id':identity} for identity in ('three','two','one')]
        self.assertEqual([r['id'] for r in order.order_rows(rows,current,'100',None)],['one','two','three'])

    def test_dates_cannot_reorder_regular_playlists_or_unbound_source_rows(self):
        current=library();current.playlists[0].keys=['one','two','three']
        for i,track in enumerate(current.tracks_by_id.values()):
            track.source_tag_ids=['11'];track.source_tag_times={'11':30-i*10}
        rows=[{'id':identity} for identity in ('three','two','one')]
        self.assertIs(order.order_rows(rows,current,'200',None),rows)
        for problem in ('imported','binding','uuid','not-member','negative-rule'):
            with self.subTest(problem=problem):
                copy=deepcopy(current);track=copy.tracks_by_id['one']
                if problem=='imported':track.imported=True
                elif problem=='binding':copy.source_binding_issues['one']='unbound'
                elif problem=='uuid':track.source_uuid=''
                elif problem=='not-member':copy.playlists[0].keys.remove('one')
                else:copy.source_playlist_catalog['nodes'][0]['SmartList']=copy.source_playlist_catalog['nodes'][0]['SmartList'].replace('Operator="8"','Operator="9"')
                result=order.order_rows(rows,copy,'100',None)
                if problem=='negative-rule':self.assertIs(result,rows)
                else:self.assertEqual([r['id'] for r in result],['two','three','one'])

    def test_invalid_native_dates_preserve_existing_default_order(self):
        current=library();current.playlists[0].keys=['one','two','three']
        rows=[{'id':identity} for identity in ('three','two','one')]
        for invalid in (float('nan'),float('inf'),-1,True,'2026-11-09',253402300800,10**500):
            with self.subTest(invalid=invalid):
                current.tracks_by_id['one'].source_tag_ids=['11']
                current.tracks_by_id['one'].source_tag_times={'11':invalid}
                self.assertIs(order.order_rows(rows,current,'100',None),rows)

    def test_latest_assignment_promotes_only_matching_verified_smart_members(self):
        before=library();captured=order.capture(before,['one'],'11',True)
        after=verified(before);pristine=deepcopy(after)
        ranks=order.record(None,captured,after)
        rows=[{'id':identity} for identity in ('three','two','one')]
        self.assertEqual([r['id'] for r in order.order_rows(rows,after,'100',ranks)],['one','three','two'])
        self.assertEqual(order.order_rows(rows,after,'200',ranks),rows)
        self.assertEqual(after,pristine)
        # A later real addition wins over earlier recency and Collection age.
        second=order.capture(after,['two'],'11',True)
        latest=verified(after,'two')
        newer=order.record(ranks,second,latest)
        self.assertEqual([r['id'] for r in order.order_rows(rows,latest,'100',newer)],['two','one','three'])
        self.assertEqual(ranks['sequence'],1)

    def test_removal_noop_irrelevant_or_negative_rule_cannot_promote(self):
        before=library();before.tracks_by_id['two'].source_tag_ids=['11']
        self.assertIsNone(order.capture(before,['two'],'11',True))
        self.assertIsNone(order.capture(before,['one'],'11',False))
        for rule in ('<NODE LogicalOperator="1"><CONDITION PropertyName="genre" Operator="8" ValueLeft="11"/></NODE>',
                     '<NODE LogicalOperator="1"><CONDITION PropertyName="myTag" Operator="9" ValueLeft="11"/></NODE>',
                     'broken xml','<!DOCTYPE x [<!ENTITY y "bad">]><NODE LogicalOperator="1"/>'):
            before.source_playlist_catalog['nodes'][0]['SmartList']=rule
            self.assertIsNone(order.capture(before,['one'],'11',True))

    def test_signed_native_tag_ids_match_without_changing_rule_bytes(self):
        before=library();before.source_tags=[LibraryTag('2275574927','Tag',source_uuid='tag-uuid')]
        rule=before.source_playlist_catalog['nodes'][0]['SmartList'].replace('"11"','"-2019392369"')
        before.source_playlist_catalog['nodes'][0]['SmartList']=rule
        self.assertIsNotNone(order.capture(before,['one'],'2275574927',True))
        self.assertEqual(before.source_playlist_catalog['nodes'][0]['SmartList'],rule)

    def test_each_native_identity_and_actual_membership_is_required(self):
        before=library();captured=order.capture(before,['one'],'11',True)
        for change in ('source','library','tag','track_uuid','track_id','playlist_uuid','rule','membership','assignment'):
            with self.subTest(change=change):
                after=verified(before)
                if change=='source':after.source='/other/master.db'
                elif change=='library':after.source_library_id='other'
                elif change=='tag':after.source_tags=[replace(after.source_tags[0],source_uuid='other-tag')]
                elif change=='track_uuid':after.tracks_by_id['one'].source_uuid='other-track'
                elif change=='track_id':after.tracks_by_id['one'].source_native_id='other-native-id'
                elif change=='playlist_uuid':after.source_playlist_catalog['nodes'][0]['UUID']='other-list'
                elif change=='rule':after.source_playlist_catalog['nodes'][0]['SmartList']+=' '
                elif change=='membership':after.playlists[0].keys.remove('one')
                elif change=='assignment':after.tracks_by_id['one'].source_tag_ids=[]
                old={'unchanged':'sentinel'}
                self.assertIs(order.record(old,captured,after),old)

    def test_logical_binding_can_change_while_exact_native_id_and_uuid_remain(self):
        before=library();track=before.tracks_by_id.pop('one')
        before.tracks_by_id['sb-logical']=replace(track,track_id='sb-logical',source_native_id='one')
        captured=order.capture(before,['sb-logical'],'11',True)
        after=verified(library())
        ranks=order.record(None,captured,after)
        self.assertEqual(list(next(iter(ranks['playlists'].values()))),['uuid-one'])

    def test_recorded_recency_cannot_follow_replaced_source_playlist_or_track(self):
        before=library();after=verified(before)
        ranks=order.record(None,order.capture(before,['one'],'11',True),after)
        rows=[{'id':identity} for identity in ('three','two','one')]
        for change in ('source','library','playlist','track','tag','rule'):
            with self.subTest(change=change):
                current=deepcopy(after)
                if change=='source':current.source='/other/master.db'
                elif change=='library':current.source_library_id='other'
                elif change=='playlist':current.source_playlist_catalog['nodes'][0]['UUID']='reused-id'
                elif change=='track':current.tracks_by_id['one'].source_uuid='reused-id'
                elif change=='tag':current.source_tags=[replace(current.source_tags[0],source_uuid='reused-tag-id')]
                else:current.source_playlist_catalog['nodes'][0]['SmartList']+=' '
                self.assertEqual(order.order_rows(rows,current,'100',ranks),rows)

    def test_corrupt_or_exhausted_order_preserves_existing_preferences(self):
        before=library();after=verified(before);captured=order.capture(before,['one'],'11',True)
        for state in ({'unknown':'state'},dict(version=1,sequence=True,playlists={}),
                      dict(version=1,sequence=2**63-1,playlists={})):
            pristine=deepcopy(state)
            with self.assertRaises(ValueError):order.record(state,captured,after)
            self.assertEqual(state,pristine)
        rows=[{'id':'three'},{'id':'one'}]
        self.assertIs(order.order_rows(rows,after,'100',{'unknown':'state'}),rows)


class SmartPlaylistTagHandoff(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def host(self):
        from desktop.app import MainWindow
        from desktop.rekordbox_playlists import NativePlaylistsMixin
        from desktop.model import TrackModel
        w=SimpleNamespace(lib=library(),settings={},_refresh_worker=None,_state_maintenance=False,
            _shutting_down=False,_device_operation_owned=lambda:False,tag_controller=SimpleNamespace(request=mock.Mock(return_value=True)),
            _sync_tags=mock.Mock(),_sync_rekordbox_connection=mock.Mock(),
            _finish_tag_refresh=mock.Mock(),_finish_edit_status=mock.Mock(),_flash_status=mock.Mock(),
            stack=SimpleNamespace(currentIndex=lambda:0))
        w.model=TrackModel();w.table=QTableView();w.table.setModel(w.model);self.addCleanup(w.table.deleteLater)
        w._rows_for=lambda tracks:[dict(id=t.track_id,title=t.name) for t in sorted(tracks,key=lambda t:-t.added_ts)]
        w._native_view_playlist=lambda name:next(p for p in w.lib.playlists if p.name==name)
        w._native_presentation=lambda:w.lib
        w._native_membership_rows=lambda playlist,rows:NativePlaylistsMixin._native_membership_rows(w,playlist,rows)
        w._native_row_identity=NativePlaylistsMixin._native_row_identity
        w._browser_seat=lambda:MainWindow._browser_seat(w)
        w._restore_browser_seat=lambda seat:MainWindow._restore_browser_seat(w,seat)
        w._selected_ids=lambda:[w.model._rows[i.row()]['id'] for i in w.table.selectionModel().selectedRows()]
        w.saves=[];w.save_ok=True
        def save():w.saves.append(deepcopy(w.settings));return w.save_ok
        w._save_state=save
        def refresh(lib):
            w.lib=lib;w.model.set_rows(MainWindow._playlist_rows(w,'Smart'))
        w._on_refresh_done=refresh
        return w

    def select(self,w,*identities):
        selection=w.table.selectionModel();selection.clearSelection()
        for index,row in enumerate(w.model._rows):
            if row['id'] in identities:
                selection.select(w.model.index(index,0),QItemSelectionModel.Select|QItemSelectionModel.Rows)

    def test_saved_context_uses_original_tag_targets_but_preserves_latest_selection_and_sort(self):
        from desktop.app import MainWindow
        from desktop.model import COLUMNS
        w=self.host();w.model.set_rows([dict(id='one',title='Zebra'),dict(id='two',title='Alpha')])
        self.select(w,'one');MainWindow._assign_tag(w,'11',True)
        self.select(w,'two')
        MainWindow._tag_saved(w,verified(w.lib))
        self.assertEqual([row['id'] for row in w.model._all_rows],['one','three','two'])
        self.assertEqual(w._selected_ids(),['two'])
        self.assertIn(order.KEY,w.saves[0])
        column=next(i for i,(key,*_) in enumerate(COLUMNS) if key=='title')
        w.model.sort(column,Qt.AscendingOrder)
        w.model.set_rows(MainWindow._playlist_rows(w,'Smart'))
        self.assertEqual([row['id'] for row in w.model._rows],['two','three','one'])
        self.assertEqual(w.lib.playlists[0].keys,['three','two','one'])

    def test_failed_local_persistence_keeps_old_order_after_successful_source_save(self):
        from desktop.app import MainWindow
        w=self.host();w.model.set_rows([dict(id='one',title='Zebra')]);self.select(w,'one')
        MainWindow._assign_tag(w,'11',True);w.save_ok=False
        MainWindow._tag_saved(w,verified(w.lib))
        self.assertNotIn(order.KEY,w.settings)
        self.assertEqual([row['id'] for row in w.model._rows],['three','two','one'])
        self.assertEqual(w.lib.tracks_by_id['one'].source_tag_ids,['11'])
        self.assertIn('Tags saved;',w._flash_status.call_args.args[0])

    def test_rejected_request_cannot_replace_context_and_source_switch_cannot_publish_it(self):
        from desktop.app import MainWindow
        w=self.host();w.model.set_rows([dict(id='one',title='Zebra')]);self.select(w,'one')
        MainWindow._assign_tag(w,'11',True);original=w._tag_order_context
        w.tag_controller.request.return_value=False
        MainWindow._assign_tag(w,'11',False);self.assertIs(w._tag_order_context,original)
        readback=verified(w.lib);w.lib=replace(w.lib,source='/new/master.db')
        refresh=mock.Mock();w._on_refresh_done=refresh
        MainWindow._tag_saved(w,readback)
        refresh.assert_not_called();self.assertNotIn(order.KEY,w.settings)
        self.assertIsNone(w._tag_order_context);self.assertEqual(w.saves,[])


if __name__=='__main__':unittest.main()
