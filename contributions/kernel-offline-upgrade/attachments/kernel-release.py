#!/usr/bin/env python3
"""One version policy for the initial undated migration and future dated updates."""
import argparse
from datetime import date, datetime
import json
import platform
import re
from pathlib import Path

PATTERN = re.compile(r'([0-9]+\.[0-9]+\.[0-9]+)-tb710fu-([0-9]{8})', re.ASCII)
KERNEL = re.compile(r'[0-9]+\.[0-9]+(?:\.[0-9]+)?(?:[-+._][A-Za-z0-9+._-]+)?', re.ASCII)
DATE_TOKEN = re.compile(r'(?<![0-9])(?:[0-9]{8}|[0-9]{4}[-_.][0-9]{2}[-_.][0-9]{2})(?![0-9])', re.ASCII)
POLICIES = ('undated', 'dated-before-target', 'undated-or-dated-before-target')
BRIDGE_TARGET = '7.2.6-tb710fu-20261118'
UNDATED_MIGRATION_TARGET = '7.2.6-tb710fu-20261117'


def release_date(version):
    match = PATTERN.fullmatch(version)
    if not match:
        raise ValueError('日期内核必须使用 <Linux版本>-tb710fu-YYYYMMDD：' + version)
    try:
        return datetime.strptime(match[2], '%Y%m%d').date()
    except ValueError as exc:
        raise ValueError('内核版本中的日期无效：' + version) from exc


def validate(plan, current=None, today=None):
    if not isinstance(plan, dict) or set(plan) != {'format', 'source_policy', 'target_version'} or plan['format'] != 2:
        raise ValueError('不支持的内核发布计划。')
    policy, target = plan['source_policy'], plan['target_version']
    if policy not in POLICIES or not isinstance(target, str):
        raise ValueError('不支持的旧版本策略或目标版本。')
    following = release_date(target)
    if policy == 'undated' and target != UNDATED_MIGRATION_TARGET:
        raise ValueError('无日期迁移仅适用于首次目标 ' + UNDATED_MIGRATION_TARGET + '；后续发布必须使用 dated-before-target。')
    if policy == 'undated-or-dated-before-target' and target != BRIDGE_TARGET:
        raise ValueError('新旧兼容迁移策略仅适用于 ' + BRIDGE_TARGET + '。')
    if following > (today or date.today()):
        raise ValueError('目标日期晚于本机当前日期，请检查时钟或等待实际发布日期。')
    if current is None:
        return target
    if not isinstance(current, str) or len(current) > 128 or not KERNEL.fullmatch(current):
        raise ValueError('运行内核版本格式无效。')
    if policy == 'undated':
        if DATE_TOKEN.search(current):
            raise ValueError('本次迁移仅接受不带日期的旧内核；已带日期的版本应使用后续日期更新包。')
    elif policy == 'undated-or-dated-before-target' and not DATE_TOKEN.search(current):
        return target
    elif release_date(current) >= following:
        raise ValueError('目标内核日期必须严格晚于当前内核日期；禁止同日覆盖或日期倒退。')
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--current', help='明确指定待升级设备的 uname -r')
    parser.add_argument('--running', action='store_true', help='读取本机 uname -r 检查版本资格')
    parser.add_argument('--target-only', action='store_true', help='构建时仅输出经过校验的目标版本')
    args = parser.parse_args()
    if args.running and args.current is not None:
        parser.error('--running 和 --current 不能同时使用')
    if not args.target_only and not args.running and args.current is None:
        parser.error('需要 --running 或 --current；构建校验使用 --target-only')
    current = platform.release() if args.running else args.current
    plan = json.loads(args.plan.read_text())
    target = validate(plan, current)
    print(target if args.target_only else 'PASS: ' + str(current) + ' -> ' + target)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, TypeError) as exc:
        raise SystemExit('停止：' + str(exc))
