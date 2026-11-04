"""A status observation reuses pure rows while retaining exact playlist hashes."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from soundbox import cdj_export, manifest, metadata
from soundbox.cdj_usb import digest
from soundbox.library import Library, Playlist, Track


def legacy_hint(library, playlists, titles=None, artists=None):
    rows=[]
    for playlist in playlists:
        tracks=[]
        for track in library.resolve(playlist):
            tracks.append([track.track_id,track.source_uuid,str(track.path),track.bpm,track.key,
                track.source_cue_revision,
                metadata.effective_title(track.track_id,track.name,track.artist,titles,library.title_junk),
                metadata.effective_artist(track.track_id,track.name,track.artist,artists,True,
                                          library.artist_registry,track.path.stem),
                getattr(library,'info_overrides',{}).get(track.track_id,{})])
        rows.append([manifest.playlist_key(playlist),playlist.name,playlist.folder_path,tracks])
    return digest(json.dumps(rows,sort_keys=True,separators=(',',':')).encode())


class CdjHints(unittest.TestCase):
    def setUp(self):
        tracks={str(i):Track(str(i),f'Artist - Song {i} (FINAL)','Artist',
                            f'file:///synthetic/{i}.wav',source_uuid=f'uuid-{i}',
                            source_cue_revision=f'cue-{i}') for i in range(1,4)}
        self.playlists=[Playlist('A',['Folder'],keys=['1','2','1','missing'],pid='a'),
                        Playlist('B',[],keys=['2','3','1'],pid='b')]
        self.library=Library(tracks,self.playlists,'synthetic.db')
        self.library.hidden_ids={'3'}
        self.library.info_overrides={'1':{'comment':'Fixture'}}
        self.previous=dict(selection=[dict(key=manifest.playlist_key(p)) for p in self.playlists],
            playlist_hints={manifest.playlist_key(p):legacy_hint(self.library,[p]) for p in self.playlists})

    def status(self, **kwargs):
        with patch.object(cdj_export,'CdjSession',return_value=object()), \
                patch.object(cdj_export,'load_manifest',return_value=self.previous):
            return cdj_export.status_hint(self.library,'synthetic',token=1,volume_uuid='fixture',**kwargs)

    def test_overlaps_and_duplicates_hash_exactly_once_per_distinct_track_per_poll(self):
        before=deepcopy(self.library)
        with patch.object(metadata,'effective_title',wraps=metadata.effective_title) as title, \
                patch.object(metadata,'effective_artist',wraps=metadata.effective_artist) as artist:
            self.assertEqual(self.status(),0)
            self.assertEqual(title.call_count,2)
            self.assertEqual(artist.call_count,2)
            self.assertEqual(self.status(titles={'1':'A new effective title'}),1)
            self.assertEqual(title.call_count,4)
            self.assertEqual(artist.call_count,4)
        self.assertEqual(self.library,before)
        for playlist in self.playlists:
            self.assertEqual(cdj_export.selection_hint(self.library,[playlist]),
                             legacy_hint(self.library,[playlist]))

    def test_new_poll_observes_membership_order_cue_and_display_changes(self):
        self.assertEqual(self.status(),0)
        self.playlists[0].keys=['2','1','1','missing']
        self.assertEqual(self.status(),1)
        self.playlists[0].keys=['1','2','1','missing']
        self.library.tracks_by_id['1'].source_cue_revision='new-cue'
        self.assertEqual(self.status(),1)
        self.library.tracks_by_id['1'].source_cue_revision='cue-1'
        self.library.info_overrides['1']['comment']='Changed'
        self.assertEqual(self.status(),1)

    def test_legacy_combined_hint_and_unmanaged_library_keep_existing_behavior(self):
        self.previous.pop('playlist_hints')
        self.previous['selection_hint']=legacy_hint(self.library,self.playlists)
        self.assertEqual(self.status(),0)
        self.previous['library_managed']=False
        self.assertIsNone(self.status())
