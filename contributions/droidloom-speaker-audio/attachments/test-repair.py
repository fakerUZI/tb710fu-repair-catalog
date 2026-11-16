#!/usr/bin/env python3
"""Isolated filesystem and mocked session tests; never changes real audio."""
import base64
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('audio_repair',Path(__file__).with_name('repair.py'))
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)

class Audio:
    def __init__(self):
        self.active=True;self.sink=True;self.source=True;self.volumes=[32768,49152];self.mute='no';self.calls=[];self.fail={};self.restarts=0;self.disappear=False;self.version='wireplumber\nCompiled with libwireplumber 0.5.13\n'
    def __call__(self,args,check=True):
        self.calls.append(list(args));key=' '.join(args[:2]);out='';rc=0
        if args[:2]==['systemctl','--user']:
            key=' '.join(args[:3])
            if args[2]=='is-active':rc=0 if self.active else 3
            elif args[2]=='restart':
                self.restarts+=1;self.active=True;self.sink=not(self.disappear and self.restarts==1);self.volumes=[65536,65536];self.mute='no'
        elif args==['wireplumber','--version']:out=self.version
        elif args[:3]==['pactl','list','short']:
            if args[3]=='sinks' and self.sink:out='1\t'+r.SINK+'\tPipeWire\n'
            if args[3]=='sources' and self.source:out='2\t'+r.SOURCE+'\tPipeWire\n'
        elif args[1]=='get-sink-volume':out='Volume: front-left: %d / 50%% / -6.0 dB, front-right: %d / 75%% / -2.0 dB\n'%tuple(self.volumes)
        elif args[1]=='get-sink-mute':out='Mute: '+self.mute+'\n'
        elif args[1]=='set-sink-volume':self.volumes=list(map(int,args[3:]))
        elif args[1]=='set-sink-mute':self.mute=args[3]
        if self.fail.get(key,0):
            rc=1
            if self.fail[key]>0:self.fail[key]-=1
        result=subprocess.CompletedProcess(args,rc,out,'simulated failure' if rc else '')
        if rc and check:raise r.Stop('simulated '+key+' failure')
        return result

class RepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.cfg=self.root/'config/rule.conf';self.state=self.root/'state';self.model=self.root/'compatible';self.model.write_bytes(b'lenovo,tb710fu\0qcom,sm8650\0');self.release=self.root/'os-release';self.release.write_text('ID=ubuntu\nVERSION_ID="26.04"\n')
        self.x=r.Repair(config=self.cfg,state=self.state,model=self.model,release=self.release);self.audio=Audio();self.x.run=self.audio;self.x.dependencies=lambda:None
        self.patches=[patch.object(r.platform,'system',return_value='Linux'),patch.object(r.platform,'machine',return_value='aarch64'),patch.object(r.platform,'release',return_value='7.2.6-tb710fu-snap1')]
        for p in self.patches:p.start()
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()
    def seed(self,data=None,mode=0o640):
        self.cfg.parent.mkdir(parents=True);self.cfg.write_bytes(r.RULE if data is None else data);self.cfg.chmod(mode)
    def test_check_is_readonly_and_does_not_change_volume(self):
        self.x.check();self.assertFalse(self.cfg.exists());self.assertFalse(self.state.exists());self.assertEqual(self.audio.volumes,[32768,49152]);self.assertEqual(self.audio.mute,'no');self.assertFalse(any('restart' in a or (a[0]=='pactl' and a[1].startswith('set-')) for a in self.audio.calls))
    def test_apply_and_rollback_preserve_channels_and_mute(self):
        self.audio.mute='yes';self.x.apply();self.assertEqual(self.cfg.read_bytes(),r.RULE);self.assertEqual(self.audio.volumes,[32768,49152]);self.assertEqual(self.audio.mute,'yes');self.x.rollback();self.assertFalse(self.cfg.exists());self.assertFalse(self.x.record.exists());self.assertEqual(self.audio.volumes,[32768,49152]);self.assertEqual(self.audio.mute,'yes')
    def test_existing_identical_rule_and_mode_survive_rollback(self):
        self.seed();self.x.apply();self.x.rollback();self.assertEqual(self.cfg.read_bytes(),r.RULE);self.assertEqual(self.cfg.stat().st_mode&0o777,0o640)
    def test_repeat_apply_does_not_restart_or_overwrite_first_snapshot(self):
        self.seed();self.x.apply();before=self.x.record.read_bytes();restarts=self.audio.restarts;self.x.apply();self.assertEqual(self.x.record.read_bytes(),before);self.assertEqual(self.audio.restarts,restarts)
    def test_different_preexisting_rule_is_not_overwritten(self):
        self.seed(b'custom');
        with self.assertRaises(r.Stop):self.x.apply()
        self.assertEqual(self.cfg.read_bytes(),b'custom');self.assertFalse(self.state.exists());self.assertEqual(self.audio.restarts,0)
    def test_modified_installed_rule_blocks_apply_and_rollback(self):
        self.x.apply();self.cfg.write_bytes(b'user edit');calls=len(self.audio.calls)
        for op in [self.x.apply,self.x.rollback]:
            with self.assertRaises(r.Stop):op()
        self.assertEqual(self.cfg.read_bytes(),b'user edit');self.assertFalse(any('restart' in a for a in self.audio.calls[calls:]))
    def test_rule_without_receipt_is_not_deleted(self):
        self.seed()
        with self.assertRaises(r.Stop):self.x.rollback()
        self.assertTrue(self.cfg.exists());self.assertEqual(self.audio.restarts,0)
    def test_symlink_destination_is_not_followed(self):
        self.cfg.parent.mkdir(parents=True);outside=self.root/'outside';outside.write_bytes(b'private');self.cfg.symlink_to(outside)
        with self.assertRaises(r.Stop):self.x.apply()
        self.assertEqual(outside.read_bytes(),b'private')
    def test_restart_failure_restores_old_file_and_volume(self):
        self.audio.fail['systemctl --user restart']=1
        with self.assertRaises(r.Stop):self.x.apply()
        self.assertFalse(self.cfg.exists());self.assertFalse(self.x.record.exists());self.assertEqual(self.audio.volumes,[32768,49152]);self.assertEqual(self.audio.mute,'no')
    def test_volume_failure_rolls_back_configuration(self):
        self.audio.fail['pactl set-sink-volume']=1
        with self.assertRaises(r.Stop):self.x.apply()
        self.assertFalse(self.cfg.exists());self.assertFalse(self.x.record.exists());self.assertEqual(self.audio.volumes,[32768,49152]);self.assertEqual(self.audio.mute,'no')
    def test_mute_failure_restores_file_and_original_mute(self):
        self.audio.fail['pactl set-sink-mute']=1
        with self.assertRaises(r.Stop):self.x.apply()
        self.assertFalse(self.cfg.exists());self.assertEqual(self.audio.mute,'no')
    def test_failed_recovery_keeps_snapshot(self):
        self.audio.fail['pactl set-sink-volume']=-1
        with self.assertRaises(r.Stop):self.x.apply()
        self.assertTrue(self.x.record.exists());self.assertIsNone(self.x.load()['before'])
    def test_missing_mic_does_not_prevent_rollback(self):
        self.x.apply();self.audio.source=False;self.x.rollback();self.assertFalse(self.cfg.exists())
    def test_stopped_session_and_missing_nodes_do_not_block_config_rollback(self):
        self.x.apply();self.audio.active=False;self.audio.sink=False;self.audio.source=False;self.model.unlink();self.release.unlink();restarts=self.audio.restarts;self.x.rollback();self.assertFalse(self.cfg.exists());self.assertEqual(self.audio.restarts,restarts)
    def test_sink_loss_is_bounded_and_recovered(self):
        self.audio.disappear=True;now=[0]
        def clock():now[0]+=.5;return now[0]
        with patch.object(r.time,'monotonic',side_effect=clock),patch.object(r.time,'sleep'):
            with self.assertRaises(r.Stop):self.x.apply()
        self.assertLess(now[0],23);self.assertFalse(self.cfg.exists());self.assertEqual(self.audio.volumes,[32768,49152])
    def test_unknown_version_is_rejected_before_writes(self):
        self.audio.version='wireplumber 0.4.17\n'
        with self.assertRaises(r.Stop):self.x.apply()
        self.assertFalse(self.cfg.exists());self.assertFalse(self.state.exists())
    def test_wrong_kernel_is_rejected_before_writes(self):
        with patch.object(r.platform,'release',return_value='other'):
            with self.assertRaises(r.Stop):self.x.apply()
        self.assertFalse(self.state.exists())
    def test_wrong_model_is_rejected_before_writes(self):
        self.model.write_bytes(b'other\0')
        with self.assertRaises(r.Stop):self.x.apply()
        self.assertFalse(self.state.exists())
    def test_wrong_os_is_rejected_before_writes(self):
        self.release.write_text('ID=ubuntu\nVERSION_ID="24.04"\n')
        with self.assertRaises(r.Stop):self.x.apply()
        self.assertFalse(self.state.exists())
    def test_check_does_not_write_existing_rule(self):
        self.seed();before=self.cfg.stat().st_mtime_ns;self.x.check();self.assertEqual(self.cfg.stat().st_mtime_ns,before);self.assertFalse(self.state.exists())
    def test_corrupt_snapshot_blocks_rollback_before_audio_changes(self):
        self.seed();self.x.apply();data=json.loads(self.x.record.read_text());data['before']['sha256']='0'*64;self.x.record.write_text(json.dumps(data));calls=len(self.audio.calls)
        with self.assertRaises(r.Stop):self.x.rollback()
        self.assertEqual(len(self.audio.calls),calls);self.assertEqual(self.cfg.read_bytes(),r.RULE)
    def test_missing_original_snapshot_field_is_rejected(self):
        self.x.apply();data=json.loads(self.x.record.read_text());del data['before'];self.x.record.write_text(json.dumps(data))
        with self.assertRaises(r.Stop):self.x.rollback()
        self.assertTrue(self.cfg.exists())
    def test_final_receipt_write_failure_recovers_file_and_volume(self):
        real=self.x.save
        def fail(data):
            if data and data['phase']=='installed':raise OSError('failed final receipt')
            real(data)
        with patch.object(self.x,'save',side_effect=fail):
            with self.assertRaises(r.Stop):self.x.apply()
        self.assertFalse(self.cfg.exists());self.assertFalse(self.x.record.exists());self.assertEqual(self.audio.volumes,[32768,49152])
    def test_raw_pactl_calls_force_c_locale(self):
        real=r.Repair(config=self.cfg,state=self.state)
        with patch.object(r.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'Mute: no\n','')) as mocked:
            real.run(['pactl','get-sink-mute',r.SINK])
            self.assertEqual(mocked.call_args.kwargs['env']['LC_ALL'],'C')
    def test_private_record_permissions(self):
        self.x.apply();self.assertEqual(self.state.stat().st_mode&0o777,0o700);self.assertEqual(self.x.record.stat().st_mode&0o777,0o600)
    def test_insecure_record_directory_refused(self):
        self.state.mkdir();self.state.chmod(0o777)
        with self.assertRaises(r.Stop):self.x.apply()
        self.assertFalse(self.cfg.exists())
    def test_pending_install_can_be_rolled_back(self):
        self.x.apply();data=json.loads(self.x.record.read_text());data.update(phase='pending',previous=None);self.x.record.write_text(json.dumps(data));self.x.rollback();self.assertFalse(self.cfg.exists())
    def test_already_deleted_created_rule_can_clear_receipt(self):
        self.x.apply();self.cfg.unlink();self.x.rollback();self.assertFalse(self.x.record.exists())

if __name__=='__main__':unittest.main()
