"""One captured manifest binds its model/digest despite a later replacement."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from soundbox import manifest
from soundbox.dual_ipod import _read_existing_manifest, composite_device_drift
from soundbox.library import Library


class CompositeManifestCapture(unittest.TestCase):
    def setUp(self):
        area=tempfile.TemporaryDirectory();self.addCleanup(area.cleanup)
        self.root=Path(area.name);self.path=manifest.manifest_path(self.root)
        self.path.parent.mkdir()
        self.first=dict(schema=3,profile=manifest.COMPOSITE_EXPORT_PROFILE,playlists={},files={},generation=1)
        self.second={**self.first,'generation':2}
        self.before=json.dumps(self.first).encode();self.after=json.dumps(self.second).encode()
        self.path.write_bytes(self.before)

    def replace_after_capture(self):
        original=Path.read_bytes;reads=[]
        def read(path):
            raw=original(path)
            if path==self.path:
                reads.append(path)
                self.path.write_bytes(self.after)
            return raw
        return read,reads

    def test_planning_model_payload_and_digest_are_from_one_fresh_read(self):
        read,reads=self.replace_after_capture()
        with patch.object(Path,'read_bytes',read):
            data,raw,digest=_read_existing_manifest(self.root)
        self.assertEqual(len(reads),1)
        self.assertEqual(data,self.first)
        self.assertEqual(raw,self.before)
        self.assertEqual(digest,hashlib.sha256(self.before).hexdigest())
        self.assertEqual(self.path.read_bytes(),self.after)
        # The next operation performs its own new read.
        self.assertEqual(_read_existing_manifest(self.root)[0],self.second)

    def test_poll_recovery_binding_hashes_the_same_observed_payload(self):
        read,reads=self.replace_after_capture()
        with patch.object(Path,'read_bytes',read), \
                patch('soundbox.dual_ipod._matching_interrupted_recovery',return_value=None) as recovery:
            self.assertIsNone(composite_device_drift(Library({},[],'synthetic'),self.root))
        self.assertEqual(len(reads),1)
        self.assertEqual(recovery.call_args.args[1],self.first)
        self.assertEqual(recovery.call_args.args[2],hashlib.sha256(self.before).hexdigest())
        self.assertEqual(self.path.read_bytes(),self.after)

    def test_strict_parser_keeps_legacy_profile_pioneer_exclusion_and_error_behavior(self):
        for profile in [manifest.COMPOSITE_EXPORT_PROFILE,*manifest.LEGACY_COMPOSITE_EXPORT_PROFILES]:
            value={**self.first,'profile':profile,'files':{'/PIONEER/rekordbox/export.pdb':{'owned':True},
                                                        '/Music/fixture.wav':{'owned':True}}}
            raw=json.dumps(value).encode()
            parsed=manifest.parse_composite(raw)
            self.assertEqual(parsed['files'],{'/Music/fixture.wav':{'owned':True}})
            parsed['files'].clear()
            self.assertTrue(manifest.parse_composite(raw)['files'])
        for raw in (b'[]',b'{}',b'invalid',b'\xff',b'\xef\xbb\xbf'+self.before):
            with self.subTest(raw=raw[:10]):
                with self.assertRaises(ValueError):manifest.parse_composite(raw)
