import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]


class Launcher(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        p=ROOT/'contributions/touch-window-grip/attachments/touch-window-grip-launch'
        text=p.read_text().split("<<'PY'\n",1)[1].rsplit('\nPY',1)[0];self.ns={'__name__':'tested_launcher'};exec(compile(text,str(p),'exec'),self.ns)
    def tearDown(self):self.tmp.cleanup()
    def file(self,name,stamp):
        p=self.root/name;p.write_text('cookie');os.utime(p,ns=(stamp,stamp));return str(p)
    def test_newest_then_valid_and_path_with_spaces(self):
        old=self.file('.mutter-Xwaylandauth.a old',1);new=self.file('.mutter-Xwaylandauth.z new',2);calls=[]
        def run(*a,**k):calls.append(k['env']['XAUTHORITY']);return subprocess.CompletedProcess([],0)
        self.assertEqual(self.ns['choose'](self.root,None,':0',run=run),new);self.assertEqual(calls,[new])
    def test_invalid_inherited_cookie_then_valid_newest(self):
        old=self.file('inherited',1);new=self.file('.mutter-Xwaylandauth.new',2);calls=[]
        def run(*a,**k):
            path=k['env']['XAUTHORITY'];calls.append(path);return subprocess.CompletedProcess([],0 if path==new else 1)
        self.assertEqual(self.ns['choose'](self.root,old,':0',run=run),new);self.assertEqual(calls,[old,new])
    def test_no_display_never_probes(self):
        new=self.file('.mutter-Xwaylandauth.new',2)
        self.assertEqual(self.ns['choose'](self.root,None,None,run=lambda *a,**k:self.fail('probe')),new)
    def test_total_probe_time_is_bounded(self):
        self.file('.mutter-Xwaylandauth.new',2);now=[0]
        def run(*a,**k):now[0]+=k['timeout'];raise subprocess.TimeoutExpired([],k['timeout'])
        self.ns['choose'](self.root,None,':0',clock=lambda:now[0],wait=lambda n:now.__setitem__(0,now[0]+n),run=run)
        self.assertLessEqual(now[0],60)
    def test_probe_closes_successful_connection(self):
        import ctypes
        from types import SimpleNamespace
        from unittest.mock import Mock
        lib=SimpleNamespace(XOpenDisplay=Mock(return_value=123),XCloseDisplay=Mock())
        with patch.object(ctypes,'CDLL',return_value=lib):exec(self.ns['PROBE'],{})
        lib.XCloseDisplay.assert_called_once_with(123)

if __name__=='__main__':unittest.main()
