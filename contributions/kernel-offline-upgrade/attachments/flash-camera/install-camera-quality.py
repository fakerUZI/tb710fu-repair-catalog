#!/usr/bin/python3
"""Coordinate the camera add-on and matching software ISP package fix."""
import argparse,importlib.util,os
from pathlib import Path
ROOT=Path(__file__).resolve().parent
def module(name):
 spec=importlib.util.spec_from_file_location(name,ROOT/(name+'.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['check','apply','rollback'],default='check',nargs='?');p.add_argument('--user',default='ubuntu');a=p.parse_args()
 addon=module('install-flash-camera');isp=module('install-camera-isp')
 addon.require(os.geteuid()==0,'需要管理员权限');os.umask(0o077)
 if a.action=='rollback':
  addon.rollback()
  if (isp.BASE/'state.json').exists():isp.rollback()
  return
 m=addon.package(ROOT);isp.check()
 if a.action=='check':print('相机软件包、配套与现场硬件校验通过；未写入设备。');return
 old=all(v==isp.OLD for v in isp.versions().values())
 isp.apply()
 try:addon.apply(ROOT,m,a.user)
 except BaseException:
  if old:isp.rollback()
  raise
 print('主摄自动对焦、前后摄/微距切换与增益适配完成。')
if __name__=='__main__':main()
