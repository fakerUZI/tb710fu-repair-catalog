#!/usr/bin/env python3
"""Validate a TB710FU dated kernel release before building or installing it."""
import argparse
from datetime import date, datetime
import json
import platform
import re
from pathlib import Path

# Reviewed historical release; never infer this date from file mtimes or uname -v.
LEGACY_DATES = {'7.2.6-tb710fu-snap1': date(2026, 11, 16)}
PATTERN = re.compile(r'([0-9]+\.[0-9]+\.[0-9]+)-tb710fu-([0-9]{8})', re.ASCII)


def release_date(version):
    if version in LEGACY_DATES:
        return LEGACY_DATES[version]
    match = PATTERN.fullmatch(version)
    if not match:
        raise ValueError('未识别的内核版本：' + str(version))
    try:
        return datetime.strptime(match[2], '%Y%m%d').date()
    except ValueError as exc:
        raise ValueError('内核版本中的日期无效：' + version) from exc


def validate(plan, current=None, today=None):
    if not isinstance(plan, dict) or set(plan) != {'format', 'source_version', 'target_version'} or plan['format'] != 1:
        raise ValueError('不支持的内核发布计划。')
    source, target = plan['source_version'], plan['target_version']
    if not isinstance(source, str) or not isinstance(target, str):
        raise ValueError('内核版本必须是字符串。')
    if not PATTERN.fullmatch(target):
        raise ValueError('新内核必须使用 <Linux版本>-tb710fu-YYYYMMDD。')
    previous, following = release_date(source), release_date(target)
    if following <= previous:
        raise ValueError('目标内核日期必须严格晚于旧内核日期；禁止同日覆盖或日期倒退。')
    if following > (today or date.today()):
        raise ValueError('目标日期晚于本机当前日期，请检查时钟或等待实际发布日期。')
    source_linux = tuple(map(int, source.split('-')[0].split('.')))
    target_linux = tuple(map(int, target.split('-')[0].split('.')))
    if target_linux < source_linux:
        raise ValueError('禁止降低 Linux 基础版本。')
    if current is not None and current != source:
        raise ValueError('本次更新要求旧内核 ' + source + '，实际为 ' + current + '。')
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--current', help='明确指定待升级设备当前 uname -r；不省略版本检查')
    parser.add_argument('--running', action='store_true', help='读取本机 uname -r 检查升级资格')
    parser.add_argument('--target-only', action='store_true', help='构建时仅输出经过校验的目标版本')
    args = parser.parse_args()
    if args.running and args.current is not None:
        parser.error('--running 和 --current 不能同时使用')
    if not args.target_only and not args.running and args.current is None:
        parser.error('需要 --running 或 --current；构建校验使用 --target-only')
    current = platform.release() if args.running else args.current
    plan = json.loads(args.plan.read_text())
    target = validate(plan, current)
    print(target if args.target_only else 'PASS: ' + plan['source_version'] + ' -> ' + target)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, TypeError) as exc:
        raise SystemExit('停止：' + str(exc))
