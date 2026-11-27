"""User-supplied vector icon preserves neutral shapes and follows saved accents."""
from pathlib import Path
import hashlib
import plistlib
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import numpy as np
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from desktop import app_icon, theme
from scripts.check_tests import qt_test_python_command


_NATIVE_ICON_PROBE='''import ctypes,sys,traceback
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
from soundbox.mac_icon import refresh_running_icon,_native_image
app=QApplication([]);png=Path(sys.argv[1]).read_bytes();failed=False
def capture():
 refresh_running_icon(png)
 with _native_image(png) as (send,cls,image):
  nsapp=send(cls('NSApplication'),'sharedApplication');actual=send(nsapp,'applicationIconImage')
  data=send(actual,'TIFFRepresentation')
  path=send(cls('NSString'),'stringWithUTF8String:',types=(ctypes.c_char_p,),args=(sys.argv[2].encode(),))
  assert send(data,'writeToFile:atomically:',ctypes.c_bool,(ctypes.c_void_p,ctypes.c_bool),(path,True))
def guarded(callback,finish=False):
 global failed
 try:
  callback()
 except BaseException:
  failed=True;traceback.print_exc()
 finally:
  if failed or finish:app.quit()
QTimer.singleShot(0,lambda:guarded(lambda:refresh_running_icon(png)))
QTimer.singleShot(750,lambda:guarded(capture,finish=True))
result=app.exec();sys.exit(1 if failed else result)
'''


def native_icon_probe(png,tiff):
    """A callback failure or hung owned probe must fail the test, not the gate."""
    import os
    return subprocess.run(qt_test_python_command(_NATIVE_ICON_PROBE,png,tiff,platform='cocoa'),
                          capture_output=True,text=True,timeout=20,
                          env={**os.environ,'QT_QPA_PLATFORM':'cocoa'})


def pixels(image):
    return np.ndarray((image.height(), image.width(), 4), dtype=np.uint8,
                      buffer=image.bits(), strides=(image.bytesPerLine(), 4, 1)).copy()


class ApplicationIcon(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_authored_purple_is_exact_and_saved_colors_preserve_alpha_and_neutral_frame(self):
        source = app_icon.accented_image('#7b2fbe')
        original = pixels(source)
        ys,xs=np.nonzero(original[:,:,3])
        self.assertGreaterEqual(min(xs.min(),ys.min()),64)
        self.assertLessEqual(max(xs.max(),ys.max()),959)
        neutral = ((original[:, :, :3] == (23, 21, 28)).all(axis=2)
                   | (original[:, :, :3] == (8, 8, 12)).all(axis=2)
                   | (original[:, :, 3] == 0))
        # Compare solid neutral interiors; antialiased ink/accent boundaries
        # can round to an ink value in one hue and a neighboring value in another.
        neutral = np.logical_and.reduce([np.roll(np.roll(neutral,y,axis=0),x,axis=1)
                                         for y in (-1,0,1) for x in (-1,0,1)])
        for color in ('#168fc0', '#00ee44', '#f07818', '#000000', '#ffffff'):
            actual = pixels(app_icon.accented_image(color))
            np.testing.assert_array_equal(actual[:, :, 3], original[:, :, 3])
            np.testing.assert_array_equal(actual[neutral], original[neutral])
        from PySide6.QtGui import QImage
        path = Path(__file__).parents[1]/'assets/icon_1024.png'
        np.testing.assert_array_equal(original, pixels(QImage(str(path)).convertToFormat(
            QImage.Format_RGBA8888)))
        fresh = app_icon.accented_image(theme.DEFAULT_ACCENT)
        self.assertEqual(fresh.pixelColor(round((128+18)*1024/292),
                                         round((3+18)*1024/292)).name(), '#ffffff')

    def test_vector_border_and_lettering_follow_accent_and_highlights_survive(self):
        path = Path(__file__).parents[1]/'assets/soundbox-original-sb-icon.svg'
        before = hashlib.sha256(path.read_bytes()).digest()
        for value in ('#168fc0', '#00ee44', '#f07818'):
            image = app_icon.accented_image(value)
            color = QColor(value)
            # The authored 256-unit tile sits inside an 18-unit transparent
            # margin, keeping the Dock artwork clear of its running indicator.
            def at(x,y):return image.pixelColor(round((x+18)*1024/292),round((y+18)*1024/292))
            np.testing.assert_allclose(
                at(128,3).getRgb()[:3], color.getRgb()[:3], atol=1)
            np.testing.assert_allclose(
                at(66,65).getRgb()[:3], color.getRgb()[:3], atol=1)
            self.assertEqual(at(144,43).name(), '#08080c')
            self.assertEqual(at(128,16).name(), '#17151c')
        self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), before)

    def test_reapplying_the_saved_accent_does_not_render_or_replace_the_icon(self):
        app = SimpleNamespace(setWindowIcon=mock.Mock())
        with mock.patch.object(app_icon, 'accented_image', wraps=app_icon.accented_image) as render:
            self.assertTrue(app_icon.apply_saved_icon(app, '#168FC0'))
            self.assertFalse(app_icon.apply_saved_icon(app, '#168fc0'))
            render.assert_called_once()
            app.setWindowIcon.assert_called_once()

    def test_persistent_icon_keeps_signed_contents_and_never_targets_development_python(self):
        from soundbox import mac_icon
        from PySide6.QtCore import QByteArray,QBuffer,QIODevice
        self.assertIsNone(mac_icon.application_bundle())
        with tempfile.TemporaryDirectory(prefix='soundbox-icon-test-') as area:
            bundle=Path(area).resolve()/'Fixture.app';contents=bundle/'Contents';contents.mkdir(parents=True)
            original=plistlib.dumps(dict(CFBundleIdentifier='test.soundbox.icon',CFBundleName='Fixture'))
            (contents/'Info.plist').write_bytes(original)
            encoded=QByteArray();buffer=QBuffer(encoded);buffer.open(QIODevice.WriteOnly)
            self.assertTrue(app_icon.accented_image('#ffffff',256).save(buffer,'PNG'));buffer.close()
            # A separate process proves metadata survives the caller's lifetime.
            png=Path(area)/'icon.png';png.write_bytes(bytes(encoded))
            subprocess.run(qt_test_python_command(
                'from pathlib import Path; import sys; from soundbox.mac_icon import set_custom_icon; '
                'set_custom_icon(Path(sys.argv[1]),Path(sys.argv[2]).read_bytes())',
                bundle,png,platform='cocoa'),check=True,timeout=20)
            self.assertTrue((bundle/'Icon\r').is_file())
            self.assertEqual((contents/'Info.plist').read_bytes(),original)
            self.assertEqual(list(contents.iterdir()),[contents/'Info.plist'])
            app=SimpleNamespace(setWindowIcon=mock.Mock())
            with mock.patch.object(mac_icon,'application_bundle',return_value=bundle), \
                    mock.patch.object(mac_icon,'set_custom_icon') as persist:
                app_icon.apply_saved_icon(app,'#ffffff',persist=False);persist.assert_not_called()

    def test_first_saved_color_refreshes_native_dock_after_launch_and_never_replays_an_old_color(self):
        from soundbox import mac_icon
        app=SimpleNamespace(setWindowIcon=mock.Mock());callbacks=[]
        with mock.patch.object(mac_icon,'application_bundle',return_value=Path('/private/Fixture.app')), \
                mock.patch.object(mac_icon,'set_custom_icon') as persist, \
                mock.patch.object(mac_icon,'refresh_running_icon') as native, \
                mock.patch.object(app_icon.QTimer,'singleShot',side_effect=lambda ms,fn:callbacks.append((ms,fn))):
            self.assertTrue(app_icon.apply_saved_icon(app,'#ffffff'))
            self.assertEqual([ms for ms,_ in callbacks],[0,750]);persist.assert_called_once();native.assert_not_called()
            callbacks[0][1]();native.assert_called_once()
            old=callbacks[1][1];self.assertTrue(app_icon.apply_saved_icon(app,'#168fc0'))
            old();self.assertEqual(native.call_count,1)
            callbacks[-1][1]();self.assertEqual(native.call_count,2)
            self.assertEqual(app._soundbox_icon_persisted,'#168fc0')

    def test_failed_metadata_write_retries_same_color_without_replacing_qt_icon_again(self):
        from soundbox import mac_icon
        app=SimpleNamespace(setWindowIcon=mock.Mock())
        with mock.patch.object(mac_icon,'application_bundle',return_value=Path('/private/Fixture.app')), \
                mock.patch.object(mac_icon,'set_custom_icon',side_effect=[OSError('temporary refusal'),None]) as persist, \
                mock.patch.object(app_icon.QTimer,'singleShot'):
            self.assertTrue(app_icon.apply_saved_icon(app,'#ffffff'))
            self.assertFalse(hasattr(app,'_soundbox_icon_persisted'))
            self.assertTrue(app_icon.apply_saved_icon(app,'#ffffff'))
            self.assertEqual(persist.call_count,2);app.setWindowIcon.assert_called_once()
            self.assertEqual(app._soundbox_icon_persisted,'#ffffff')

    def test_native_running_image_changes_on_first_commit_in_a_fresh_process(self):
        from io import BytesIO
        from PIL import Image,ImageCms
        with tempfile.TemporaryDirectory(prefix='soundbox-native-icon-') as area:
            root=Path(area).resolve();png=root/'saved.png';tiff=root/'native.tiff'
            self.assertTrue(app_icon.accented_image('#168fc0',256).save(str(png)))
            completed=native_icon_probe(png,tiff)
            self.assertEqual(completed.returncode,0,completed.stdout+completed.stderr)
            # macOS26 returns a Retina, half-float display-space snapshot. Use
            # native ImageIO and normalize its ICC profile for a real RGB check.
            native=root/'native.png'
            subprocess.run(['/usr/bin/sips','-s','format','png',str(tiff),'--out',str(native)],check=True,capture_output=True)
            actual=Image.open(native)
            if actual.info.get('icc_profile'):
                actual=ImageCms.profileToProfile(actual,BytesIO(actual.info['icc_profile']),ImageCms.createProfile('sRGB'),outputMode='RGBA')
            source=Image.open(png).convert('RGBA');a=np.array(source);b=np.array(actual.convert('RGBA').resize(source.size))
            solid=(a[:,:,3]==255)&(a[:,:,0]==0x16)&(a[:,:,1]==0x8f)&(a[:,:,2]==0xc0)
            self.assertGreater(int(solid.sum()),1000)
            self.assertLess(float(np.abs(a[solid,:3].astype(float)-b[solid,:3]).mean()),2)

    def test_native_callback_failure_exits_nonzero_with_its_traceback(self):
        for phase in ('first-refresh','capture'):
            with self.subTest(phase=phase),tempfile.TemporaryDirectory(
                    prefix='soundbox-native-icon-failure-') as area:
                png=Path(area)/'saved.png';tiff=Path(area)/'absent-parent'/'never-created.tiff'
                if phase=='first-refresh':
                    png.write_bytes(b'owned invalid PNG fixture')
                    expected='ValueError: Persistent application icon requires bounded PNG data'
                else:
                    self.assertTrue(app_icon.accented_image('#168fc0',256).save(str(png)))
                    expected='AssertionError'
                completed=native_icon_probe(png,tiff)
                self.assertNotEqual(completed.returncode,0)
                self.assertIn(expected,completed.stderr)
                self.assertFalse(tiff.exists())
