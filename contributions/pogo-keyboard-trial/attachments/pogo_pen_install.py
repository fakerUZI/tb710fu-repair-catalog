#!/usr/bin/env python3
"""Run the combined Pogo and pen test package."""
import argparse
import os
import subprocess
import sys

import pen_install
import pogo_install


def check():
    pogo_install.check_install()
    pen_install.preflight(pen_install.saved_state())


def apply():
    check()
    keyboard_preexisting = pogo_install.installed_payload()
    # Stage the pen first. A failed keyboard update leaves the previous
    # keyboard payload and the previous touch module in place.
    changed = pen_install.stage(keyboard_preexisting)
    try:
        pogo_install.apply()
    except (OSError, RuntimeError, subprocess.SubprocessError):
        if changed:
            pen_install.rollback()
        raise
    print("手写笔模块已写入；重启后使用新的触控驱动。", flush=True)


def rollback():
    pen_install.require_kernel()
    state = pen_install.saved_state()
    if state:
        pen_install.preflight(state)
    if not state or not state["keyboard_preexisting"]:
        pogo_install.rollback()
    pen_install.rollback()
    if state:
        print("已恢复原触控模块，重启后生效。", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "apply", "rollback"))
    args = parser.parse_args()
    if args.action == "check":
        check()
        print("磁吸键盘与手写笔的 20261118 安装条件检查通过。", flush=True)
        return
    if os.geteuid() != 0:
        raise PermissionError("安装或卸载需要管理员权限")
    with pogo_install.lock("tb710fu-pogo-install"):
        (apply if args.action == "apply" else rollback)()


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        sys.exit(str(error))
