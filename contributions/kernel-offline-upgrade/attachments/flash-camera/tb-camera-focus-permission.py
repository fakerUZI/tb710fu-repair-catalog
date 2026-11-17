#!/usr/bin/python3
"""Restrict the bounded lens DAC to the existing active local-seat policy."""
import os,sys
from pathlib import Path
VERSION='7.2.6-tb710fu-20261117'
FOCUS=Path('/sys/module/tb_focus_manual/parameters/position')
def active_uid():
 sys.path.insert(0,'/usr/local/lib')
 try:
  from tb710fu_session import active_user
 except ImportError:
  return 0
 user=active_user()
 return user[0] if user else 0
def main():
 if os.uname().release!=VERSION:return
 if os.geteuid()!=0:raise RuntimeError('需要管理员权限')
 if FOCUS.exists():
  os.chown(FOCUS,active_uid(),-1);os.chmod(FOCUS,0o600)
if __name__=='__main__':main()
