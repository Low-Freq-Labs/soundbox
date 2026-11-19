"""Pinned bootstrap keeps internal helper processes Qt-free before app entry."""
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import PyInstaller


ROOT=Path(__file__).resolve().parents[1]
HOOK=ROOT/'soundbox/packaged_worker_hook.py'
PYINSTALLER=Path(PyInstaller.__file__).parent


class PackagedWorkerBootstrap(unittest.TestCase):
    def test_desktop_and_other_argument_positions_do_not_import_bootstrap_utils(self):
        for arguments in ([],['--check-startup'],['--soundbox-worker-other'],['file','--soundbox-worker','cache']):
            with self.subTest(arguments=arguments), \
                    mock.patch.object(sys,'argv',['Soundbox',*arguments]), \
                    mock.patch.dict(sys.modules,{'_pyi_rth_utils':None}):
                runpy.run_path(str(HOOK))

    def test_actual_installed_qt_runtime_hook_imports_no_qt_for_both_engine_roles(self):
        with tempfile.TemporaryDirectory() as area:
            script='''
import importlib.abc,runpy,sys
sys._MEIPASS=sys.argv[1]
sys.path.insert(0,sys.argv[2])
custom,standard,role=sys.argv[3:]
sys.argv=['Soundbox','--soundbox-worker',role,sys._MEIPASS,'3','4']
from _pyi_rth_utils import qt
original=qt.create_embedded_qt_conf
class NoQt(importlib.abc.MetaPathFinder):
    def find_spec(self,name,path=None,target=None):
        if name=='PySide6' or name.startswith(('PySide6.','desktop.')) or name in ('desktop','soundbox.macos_defaults'):
            raise AssertionError('Engine bootstrap imported '+name)
sys.meta_path.insert(0,NoQt())
runpy.run_path(custom)
assert qt.create_embedded_qt_conf is not original
assert qt.create_embedded_qt_conf('PySide6','ignored') is None
runpy.run_path(standard)
assert qt._registered_qt_bindings=='PySide6'
assert not any(name.startswith(('PySide6','desktop')) or name=='soundbox.macos_defaults' for name in sys.modules)
'''
            for role in ('cache','native-read'):
                for unpacked in (area,str(Path(area)/'Contents/Frameworks')):
                    with self.subTest(role=role,unpacked=unpacked):
                        result=subprocess.run([sys.executable,'-c',script,unpacked,
                            str(PYINSTALLER/'fake-modules'),str(HOOK),
                            str(PYINSTALLER/'hooks/rthooks/pyi_rth_pyside6.py'),role],
                            capture_output=True,text=True,timeout=10)
                        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_normal_desktop_retains_original_qt_configuration_helper(self):
        script='''
import runpy,sys
sys._MEIPASS=sys.argv[1]
sys.path.insert(0,sys.argv[2])
custom=sys.argv[3]
sys.argv=['Soundbox']
from _pyi_rth_utils import qt
original=qt.create_embedded_qt_conf
runpy.run_path(custom)
assert qt.create_embedded_qt_conf is original
assert qt._registered_qt_bindings is None
assert not any(name.startswith('PySide6') for name in sys.modules)
'''
        with tempfile.TemporaryDirectory() as area:
            result=subprocess.run([sys.executable,'-c',script,area,
                str(PYINSTALLER/'fake-modules'),str(HOOK)],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_pinned_packager_puts_explicit_custom_hook_before_implied_qt_hook(self):
        from PyInstaller.depend.analysis import PyiModuleGraph
        self.assertEqual(PyInstaller.__version__,'6.21.0')
        self.assertIn('pyinstaller==6.21.0',(ROOT/'requirements-core.txt').read_text())
        standard=str(PYINSTALLER/'hooks/rthooks/pyi_rth_pyside6.py')
        graph=mock.Mock()
        graph._available_rthooks={'PySide6':[standard]}
        graph._make_toc.return_value=[('PySide6','unused','PYMODULE')]
        graph.add_script.side_effect=lambda path:path
        hooks=PyiModuleGraph.analyze_runtime_hooks(graph,[str(HOOK)])
        self.assertEqual(hooks,[os.path.abspath(HOOK),standard])
        self.assertIn("runtime_hooks=[str(Path(SPECPATH) / 'soundbox' / 'packaged_worker_hook.py')]",
                      (ROOT/'Soundbox.spec').read_text())
