"""Full scan/resume uses generated covers and a fake provider; no live state."""
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import sqlite3
from unittest.mock import patch

from PIL import Image

from soundbox.artwork import ArtCache
from soundbox.art_review_scan import ReviewScanStore, context_stamp


def jpeg(color):
    output = io.BytesIO()
    Image.new("RGB", (32,32), color).save(output,"JPEG")
    return output.getvalue()


class FullLibraryScan(unittest.TestCase):
    def test_adding_previews_preserves_existing_queue_and_epoch(self):
        area=Path(self.temp.name)/"old-format"
        area.mkdir()
        db=sqlite3.connect(area/"checks.sqlite3")
        db.execute("CREATE TABLE epoch (id INTEGER PRIMARY KEY,value INTEGER NOT NULL)")
        db.execute("INSERT INTO epoch VALUES (1,5)")
        db.execute("CREATE TABLE checks (tid TEXT PRIMARY KEY,stamp TEXT,epoch INTEGER,result TEXT)")
        result=dict(candidates=[dict(url="saved",thumb="image")])
        db.execute("INSERT INTO checks VALUES (?,?,?,?)",("saved","exact",5,json.dumps(result)))
        db.commit();db.close()
        store=ReviewScanStore(area)
        self.addCleanup(store.close)
        self.assertEqual(store.get("saved","exact"),result)
        self.assertEqual(store.epoch,5)
        self.assertIsNone(store.preview_get(result["candidates"][0]))

    def test_preview_lru_is_bounded_without_evicting_suggestions_or_changing_epoch(self):
        from soundbox import art_review_scan
        store=ReviewScanStore(Path(self.temp.name)/"preview-cache")
        self.addCleanup(store.close)
        a,b,c=[dict(url=name,thumb="image") for name in ("a","b","c")]
        store.put("track","exact",dict(candidates=[a,b,c]))
        with patch.object(art_review_scan,"PREVIEW_COUNT",2),patch.object(art_review_scan,"PREVIEW_BYTES",2048):
            store.preview_put(a,b"a"*600)
            store.preview_put(b,b"b"*600)
            self.assertEqual(store.preview_get(a),b"a"*600)
            store.preview_put(c,b"c"*600)
            self.assertIsNone(store.preview_get(b))
            store.preview_put(b,b"b"*2010)
            self.assertIsNone(store.preview_get(a))
            self.assertIsNone(store.preview_get(c))
            self.assertEqual(store.preview_get(b),b"b"*2010)
            store.preview_put(a,b"x"*(512*1024+1))
            self.assertIsNone(store.preview_get(a))
        self.assertEqual(store.get("track","exact"),dict(candidates=[a,b,c]))
        self.assertEqual(store.epoch,0)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/"art"
        self.root.mkdir()
        self.red,self.blue = jpeg("red"),jpeg("blue")

    def fixture(self,count):
        index={}
        seed=self.root/"seed.jpg"
        seed.write_bytes(self.red)
        rows=[]
        for i in range(count):
            tid=str(i)
            os.link(seed,self.root/f"{tid}.jpg")
            entry=dict(status="ok",ext="jpg",src=f"https://soundcloud.com/artist/current-{tid}",conf=1)
            if i%3!=2:
                entry["origin"]="manual" if i%3==1 else "auto"
            index[tid]=entry
            rows.append((tid,"Artist",f"Song {i}",entry.get("origin","Unrecorded")))
        (self.root/"index.json").write_text(json.dumps(index))
        return ArtCache(self.root),rows

    def provider(self, *, miss=False):
        blue=self.blue
        class Provider:
            calls=0
            previews=0
            def search_candidates(self,artist,title):
                self.calls+=1
                return [] if miss else [dict(title=title,uploader=artist,score=.95,
                    thumb="synthetic",url="https://soundcloud.com/artist/proposed-"+title.replace(" ","-"))]
            def preview_image(self,candidate):
                self.previews+=1
                return blue
        return Provider()

    def scan(self,cache,rows,provider,**kwargs):
        from desktop.artreview import _ReviewScan
        worker=_ReviewScan(cache,provider,rows,**kwargs)
        worker.DELAY=0
        with patch.object(worker._cancel,"wait",return_value=False):
            worker.run()
        self.assertEqual(worker.error,"")
        self.assertEqual(worker.failed,0)
        return worker

    def test_tracking_duplicate_is_skipped_before_preview_and_real_alternative_survives(self):
        cache,rows=self.fixture(1)
        provider=self.provider()
        duplicate=dict(title="Same cover",uploader="Artist",score=1,
                       url=cache.index["0"]["src"]+"?utm_source=api")
        different=dict(title="Different cover",uploader="Artist",score=.95,
                       url="https://soundcloud.com/artist/different")
        with patch.object(provider,"search_candidates",return_value=[duplicate,different]):
            worker=self.scan(cache,rows,provider)
        self.assertEqual(provider.previews,1)
        self.assertEqual(worker.failed,0)
        store=ReviewScanStore(self.root.parent/"art_review_scan")
        self.addCleanup(store.close)
        snapshot=cache.cover_snapshot("0")
        saved=store.get("0",context_stamp(snapshot.entry,snapshot.digest,rows[0][1],rows[0][2],()))
        self.assertEqual(saved["candidates"],[different])
        self.assertEqual(snapshot.data,self.red)

    def test_later_saved_comparison_and_keep_restore_before_first_new_online_search(self):
        from desktop.artreview import _ReviewScan
        cache,rows=self.fixture(3)
        self.scan(cache,[rows[2]],self.provider())
        cache.keep_review("1",cache.cover_snapshot("1"),"Artist","Song 1")
        worker=_ReviewScan(cache,self.provider(),rows)
        seen=[]; restored=[]; progress=[]
        worker.scanned.connect(lambda tid,result: seen.append(tid))
        worker.restored.connect(lambda *counts: restored.append(counts))
        worker.progress.connect(lambda *counts: progress.append(counts))
        def search(*_):
            self.assertEqual(seen,["1","2"])
            self.assertEqual(restored,[(1,1,0,1)])
            self.assertEqual(progress,[(0,1)])
            return []
        with patch.object(worker.provider,"search_candidates",side_effect=search) as online, \
                patch.object(worker._cancel,"wait",return_value=False):
            worker.run()
        self.assertEqual(worker.error,"")
        online.assert_called_once_with("Artist","Song 0")
        self.assertEqual(progress[-1],(1,1))

    def test_changed_image_bytes_requeue_saved_check_and_review_decision(self):
        cache,rows=self.fixture(2)
        self.scan(cache,rows,self.provider(miss=True))
        cache.keep_review("1",cache.cover_snapshot("1"),"Artist","Song 1")
        for tid in ("0","1"):
            replacement=self.root/f"{tid}.replacement"
            replacement.write_bytes(jpeg("green"))
            replacement.replace(self.root/f"{tid}.jpg")
        provider=self.provider()
        worker=self.scan(cache,rows,provider)
        self.assertEqual((worker.reused,worker.kept,provider.calls),(0,0,2))

    def test_more_than_three_thousand_manual_legacy_and_auto_covers_are_checked_and_resumed(self):
        cache,rows=self.fixture(3101)
        before=(self.root/"index.json").read_bytes()
        provider=self.provider()
        worker=self.scan(cache,rows,provider)
        self.assertEqual(provider.calls,3101)
        self.assertEqual(worker.reused,0)
        resumed=self.provider()
        from desktop.artreview import _ReviewScan
        worker=_ReviewScan(ArtCache(self.root),resumed,rows)
        progress=[]
        worker.progress.connect(lambda *counts: progress.append(counts))
        worker.run()
        self.assertEqual(worker.error,"")
        self.assertEqual(progress,[(0,0)])
        self.assertEqual(worker.reused,3101)
        self.assertEqual(resumed.calls,0)
        self.assertEqual(resumed.previews,0)
        self.assertEqual((self.root/"index.json").read_bytes(),before)
        self.assertEqual((self.root/"1.jpg").read_bytes(),self.red)
        self.assertFalse((self.root/"review").exists())
        self.assertLess((self.root.parent/"art_review_scan/checks.sqlite3").stat().st_size,8*1024**2)

    def test_keep_is_respected_but_artist_title_or_cover_changes_invalidate_prior_checks(self):
        cache,rows=self.fixture(3)
        cache.keep_review("1",cache.cover_snapshot("1"),"Artist","Song 1")
        provider=self.provider()
        first=self.scan(cache,rows,provider)
        self.assertEqual((first.kept,provider.calls),(1,2))
        changed=[(*rows[0][:1],"Other Artist",*rows[0][2:]),rows[1],rows[2]]
        next_provider=self.provider()
        second=self.scan(cache,changed,next_provider)
        self.assertEqual((second.kept,second.reused,next_provider.calls),(1,1,1))
        self.assertEqual(cache.index["1"]["origin"],"manual")

    def test_successful_misses_are_cached_and_explicit_recheck_invalidates_them(self):
        cache,rows=self.fixture(2)
        first=self.provider(miss=True)
        self.scan(cache,rows,first)
        second=self.provider()
        self.assertEqual(self.scan(cache,rows,second).reused,2)
        self.assertEqual(second.calls,0)
        self.scan(cache,rows,second,fresh=True)
        self.assertEqual(second.calls,2)

    def test_close_preserves_completed_checks_for_resume(self):
        from desktop.artreview import _ReviewScan
        cache,rows=self.fixture(3)
        provider=self.provider()
        worker=_ReviewScan(cache,provider,rows)
        worker.DELAY=0
        worker.scanned.connect(lambda *_: worker.stop())
        worker.run()
        self.assertEqual(provider.calls,1)
        resumed=self.provider()
        again=self.scan(cache,rows,resumed)
        self.assertEqual((again.reused,resumed.calls),(1,2))

    def test_context_and_symlink_guards(self):
        self.assertNotEqual(context_stamp({},"digest","A","Title",()),context_stamp({},"digest","B","Title",()))
        target=Path(self.temp.name)/"target"
        target.mkdir()
        alias=Path(self.temp.name)/"alias"
        alias.symlink_to(target,target_is_directory=True)
        with self.assertRaises(ValueError):
            ReviewScanStore(alias)

    def test_repeated_failures_pause_without_recording_false_misses(self):
        from desktop.artreview import _ReviewScan
        cache,rows=self.fixture(8)
        provider=self.provider()
        with patch.object(provider,"search_candidates",side_effect=OSError("offline")) as request:
            worker=_ReviewScan(cache,provider,rows)
            worker.DELAY=0
            with patch.object(worker._cancel,"wait",return_value=False):
                worker.run()
        self.assertEqual(request.call_count,5)
        self.assertEqual(worker.failed,5)
        self.assertIn("resume",worker.error)
        resumed=self.provider()
        self.scan(cache,rows,resumed)
        self.assertEqual(resumed.calls,8)
