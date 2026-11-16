import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
POWER=ROOT/'contributions/power-key-single-press/attachments'
DROID=ROOT/'contributions/droidloom-on-demand-launch/attachments'

def module(path,name):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

class Service:
    def __init__(self,fragment='',active=False,enabled=''):
        self.fragment=fragment;self.active=active;self.enabled=enabled;self.calls=[];self.fail=None
    def __call__(self,*args,check=True):
        self.calls.append(args);op=args[0];rc=0;out=''
        if op=='is-enabled':out=self.enabled or 'disabled';rc=0 if self.enabled else 1
        elif op=='is-active':out='active' if self.active else 'inactive';rc=0 if self.active else 3
        elif op=='show':out=self.fragment
        elif op in ('start','restart'):self.active=True
        elif op=='stop':self.active=False
        elif op=='enable':self.enabled='enabled-runtime' if '--runtime' in args else 'enabled'
        elif op=='disable':self.enabled='';self.active=False if '--now' in args else self.active
        if op==self.fail:rc=1
        if rc and check:raise subprocess.CalledProcessError(rc,args,out)
        return subprocess.CompletedProcess(args,rc,out,'')

class Power(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.m=module(POWER/'install-power-key.py','power')
        self.source=self.root/'source';shutil.copytree(POWER,self.source)
        self.hardware=self.root/'sys';dpms=self.hardware/'drm/card1-DSI-1/dpms';dpms.parent.mkdir(parents=True);dpms.write_text('On\n')
        self.x=self.m.Installer(home=self.root/'user',source=self.source,state=self.root/'state',hardware=self.hardware)
        self.service=Service(str(self.x.files[self.m.UNIT][0]));self.x.ctl=self.service
        self.uid=patch.object(self.m.os,'geteuid',return_value=1000);self.uid.start()
    def tearDown(self):self.uid.stop();self.tmp.cleanup()
    def seed(self):
        for name,(p,_) in self.x.files.items():p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(('original-'+name).encode());p.chmod(0o640)
        self.service.active=True;self.service.enabled='enabled-runtime'
    def test_check_does_not_create_user_files_or_change_service(self):
        self.x.report();self.assertFalse(self.x.home.exists());self.assertFalse(self.x.state.exists());self.assertTrue(all(x[0] in ('show-environment','show','is-active') for x in self.service.calls))
    def test_missing_hardware_stops_before_writes(self):
        shutil.rmtree(self.hardware)
        with self.assertRaises(ValueError):self.x.apply()
        self.assertFalse(self.x.home.exists());self.assertFalse(self.x.state.exists())
    def test_missing_attachment_stops_before_writes(self):
        (self.source/'tb-power-key.py').unlink()
        with self.assertRaises(ValueError):self.x.apply()
        self.assertFalse(self.x.home.exists())
    def test_fresh_apply_restart_and_rollback(self):
        self.x.apply();self.assertIn(('restart',self.m.UNIT),self.service.calls);self.x.rollback()
        self.assertFalse(any(p.exists() for p,_ in self.x.files.values()));self.assertFalse(self.service.active);self.assertEqual(self.service.enabled,'')
    def test_custom_files_permissions_and_service_state_restored(self):
        self.seed();self.x.apply();self.x.rollback()
        for name,(p,_) in self.x.files.items():self.assertEqual(p.read_bytes(),('original-'+name).encode());self.assertEqual(p.stat().st_mode&0o777,0o640)
        self.assertTrue(self.service.active);self.assertEqual(self.service.enabled,'enabled-runtime')
    def test_reapply_preserves_original_snapshot(self):
        self.seed();self.x.apply();before=(self.x.state/'tb-power-key.py.before').read_bytes();self.x.apply();self.assertEqual(before,(self.x.state/'tb-power-key.py.before').read_bytes());self.x.rollback();self.assertEqual(self.x.files['tb-power-key.py'][0].read_bytes(),before)
    def test_changed_file_blocks_apply_and_rollback_without_service_mutation(self):
        self.x.apply();p=self.x.files['tb-power-key.py'][0];p.write_text('user change');calls=len(self.service.calls)
        for op in [self.x.apply,self.x.rollback]:
            with self.assertRaises(ValueError):op()
        self.assertEqual(p.read_text(),'user change');self.assertFalse(any(x[0] in ('stop','disable','restart') for x in self.service.calls[calls:]))
    def test_corrupt_backup_blocks_rollback_before_mutation(self):
        self.seed();self.x.apply();(self.x.state/'tb-power-key.py.before').write_text('bad');before=len(self.service.calls)
        with self.assertRaises(ValueError):self.x.rollback()
        self.assertEqual(len(self.service.calls),before)
    def test_interrupted_first_install_can_restore_originals(self):
        self.seed();self.service.fail='restart'
        with self.assertRaises(subprocess.CalledProcessError):self.x.apply()
        self.service.fail=None;self.x.rollback();self.assertEqual(self.x.files['tb-power-key.py'][0].read_text(),'original-tb-power-key.py')
    def test_interrupted_upgrade_accepts_prior_package_files(self):
        self.seed();self.x.apply();(self.source/'tb-power-key.py').write_text('upgraded\n')
        real=self.m.atomic;count=0
        def interrupted(p,data,mode=0o600):
            nonlocal count
            if p in [v[0] for v in self.x.files.values()]:
                count+=1
                if count==2:raise OSError('interrupted')
            return real(p,data,mode)
        with patch.object(self.m,'atomic',side_effect=interrupted):
            with self.assertRaises(OSError):self.x.apply()
        self.x.rollback();self.assertEqual(self.x.files['tb-power-key.py'][0].read_text(),'original-tb-power-key.py')
    def test_symlink_target_rejected(self):
        target=self.x.files['tb-power-key.py'][0];target.parent.mkdir(parents=True);outside=self.root/'outside';outside.write_text('private');target.symlink_to(outside)
        with self.assertRaises(ValueError):self.x.apply()
        self.assertEqual(outside.read_text(),'private')
    def test_legacy_rollback_does_not_guess_or_delete(self):
        self.seed()
        with self.assertRaises(ValueError):self.x.rollback()
        self.assertTrue(all(p.exists() for p,_ in self.x.files.values()))

class Droid(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.m=module(DROID/'install-on-demand.py','droid')
        self.m.home=self.root/'user';self.m.source=self.root/'source';shutil.copytree(DROID,self.m.source);self.m.state=self.root/'state'
        self.m.files={'launch':self.m.home/'.local/bin/droidloom-launch','fix-launchers.py':self.m.home/'.local/bin/droidloom-fix-launchers','watch-launchers.py':self.m.home/'.local/bin/droidloom-watch-launchers','droidloom-watch-launchers.service':self.m.home/'.config/systemd/user'/self.m.unit}
        self.m.DROIDLOOM=self.root/'droidloomctl';self.m.DROIDLOOM.write_text('fake')
        self.service=Service();self.m.ctl=self.service
        self.patches=[patch.object(self.m.os,'geteuid',return_value=1000),patch.object(self.m.shutil,'which',return_value='/fixture/tool'),patch.dict(os.environ,{'XDG_RUNTIME_DIR':str(self.root/'runtime')}),patch.object(self.m.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'',''))]
        for p in self.patches:p.start()
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()
    def seed(self):
        for name,p in self.m.files.items():p.parent.mkdir(parents=True,exist_ok=True);p.write_text('original-'+name);p.chmod(0o640)
        self.service.active=True;self.service.enabled='enabled'
    def test_check_readonly(self):
        self.m.preflight();self.assertFalse(self.m.home.exists());self.assertFalse(self.m.state.exists())
    def test_fresh_apply_rollback(self):
        self.m.apply();self.assertIn(('restart',self.m.unit),self.service.calls);self.m.rollback();self.assertFalse(any(p.exists() for p in self.m.files.values()))
    def test_repeat_install_and_custom_originals_restore(self):
        self.seed();self.m.apply();backup=(self.m.state/'launch.before').read_bytes();self.m.apply();self.m.rollback();self.assertEqual(self.m.files['launch'].read_bytes(),backup);self.assertTrue(self.service.active);self.assertEqual(self.service.enabled,'enabled')
    def test_user_edit_rejected_on_repeat_apply_and_rollback(self):
        self.m.apply();self.m.files['launch'].write_text('edited');count=len(self.service.calls)
        for action in [self.m.apply,self.m.rollback]:
            with self.assertRaises(ValueError):action()
        self.assertEqual(self.m.files['launch'].read_text(),'edited');self.assertFalse(any(x[0] in ('disable','restart') for x in self.service.calls[count:]))
    def test_corrupted_backup_rejected(self):
        self.seed();self.m.apply();(self.m.state/'launch.before').write_text('bad');count=len(self.service.calls)
        with self.assertRaises(ValueError):self.m.rollback()
        self.assertEqual(len(self.service.calls),count+1) # readonly show-environment
    def test_rollback_does_not_require_droidloom_to_still_exist(self):
        self.m.apply();self.m.DROIDLOOM.unlink();self.m.rollback();self.assertFalse(self.m.files['launch'].exists())
    def test_failed_restart_leaves_recoverable_snapshot(self):
        self.seed();self.service.fail='restart'
        with self.assertRaises(subprocess.CalledProcessError):self.m.apply()
        self.service.fail=None;self.m.rollback();self.assertEqual(self.m.files['launch'].read_text(),'original-launch')
    def test_symlink_target_blocks_install(self):
        p=self.m.files['launch'];p.parent.mkdir(parents=True);outside=self.root/'outside';outside.write_text('private');p.symlink_to(outside)
        with self.assertRaises(ValueError):self.m.apply()
        self.assertEqual(outside.read_text(),'private')
    def test_desktop_rollback_only_rewrites_managed_launcher_prefix(self):
        self.m.apply();folder=self.m.home/'.local/share/applications';folder.mkdir(parents=True)
        managed=folder/'droidloom-example.desktop';prefix='Exec='+str(self.m.files['launch'])+' '
        managed.write_text('[Desktop Entry]\nX-Droidloom-Managed=true\n'+prefix+'app --flag\nComment=kept\n')
        custom=folder/'droidloom-custom.desktop';custom.write_text('[Desktop Entry]\n'+prefix+'custom\n')
        self.m.rollback();self.assertIn('Exec=/usr/bin/droidloomctl launch app --flag',managed.read_text());self.assertIn('Comment=kept',managed.read_text());self.assertIn(prefix,custom.read_text())

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
