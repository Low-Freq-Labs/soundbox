"""Cue-layer precedence over captured packets, with per-read parse reuse."""
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from scripts.check_fixtures import cdj_reference
from soundbox.cdj_device_cues import read


class CapturedDeviceCues(unittest.TestCase):
    def test_classic_and_extended_layers_keep_precedence_and_insertion_order(self):
        with tempfile.TemporaryDirectory() as area:
            dat=Path(area)/'ANLZ0000.DAT';dat.touch()
            ext=dat.with_suffix('.EXT');ext.touch()
            def cue(slot,time,comment=''):
                return SimpleNamespace(hot_cue=slot,time=time,loop_time=0xffffffff,
                                       comment=comment)
            layers={
                '.DAT': {'PCOB':[cue(1,1),cue(0,10,'classic memory')],
                         'PCO2':[cue(1,3),cue(0,10,'extended memory'),cue(4,50)]},
                '.EXT': {'PCOB':[cue(1,2),cue(0,20)],
                         'PCO2':[cue(1,4,'final hot'),cue(0,30)]},
            }
            visited=[]
            class Parsed:
                def __init__(self,suffix):self.suffix=suffix
                def getall_tags(self,name):
                    visited.append((self.suffix,name))
                    return [SimpleNamespace(content=SimpleNamespace(entries=layers[self.suffix][name]))]
            with mock.patch('pyrekordbox.anlz.AnlzFile.parse_file',
                            side_effect=lambda path:Parsed(path.suffix)) as parse:
                rows=read(dat)
                self.assertEqual(parse.call_args_list,[mock.call(dat),mock.call(ext)])
                self.assertEqual(visited,[('.DAT','PCOB'),('.EXT','PCOB'),('.DAT','PCO2'),('.EXT','PCO2')])
                self.assertEqual([(r['kind'],r['time_ms'],r['comment']) for r in rows],
                    [(1,4,'final hot'),(0,10,'extended memory'),(0,20,''),(5,50,''),(0,30,'')])
                layers['.EXT']['PCO2'][0].comment='fresh next read'
                self.assertEqual(read(dat)[0]['comment'],'fresh next read')
                self.assertEqual(parse.call_count,4)

    def test_native_fixture_cues_are_exact_with_two_packet_parses(self):
        from pyrekordbox.anlz import AnlzFile
        with tempfile.TemporaryDirectory() as area:
            source=cdj_reference()/'analysis/ANLZ0000.DAT'
            dat=Path(area)/source.name
            for extension in ('.DAT','.EXT'):
                shutil.copyfile(source.with_suffix(extension),dat.with_suffix(extension))
            before={path:path.read_bytes() for path in (dat,dat.with_suffix('.EXT'))}
            with mock.patch('pyrekordbox.anlz.AnlzFile.parse_file',wraps=AnlzFile.parse_file) as parse:
                rows=read(dat)
            self.assertEqual(parse.call_count,2)
            self.assertEqual([(r['kind'],r['time_ms']) for r in rows],
                [(3,20924),(2,13967),(1,54),(6,41795),(9,97445),(8,69619),(7,62663),(5,27880)])
            self.assertTrue(all(not r['active_loop'] and r['loop_time_ms'] is None for r in rows))
            self.assertEqual({path:path.read_bytes() for path in before},before)


if __name__=='__main__':
    unittest.main()
