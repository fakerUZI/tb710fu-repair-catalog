"""Refresh the repair catalog and apply a reviewed batch from the desktop."""

import fcntl
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from urllib.parse import quote


ASSISTANT = Path("/usr/share/tb710fu-repair-assistant")
BATCH_ID = "one-click-upgrade"
RETIRED_ID = "kernel-offline-upgrade"


def prepare(catalog, *, clean):
    state = catalog.refresh()
    revision = state["revision"]
    files = catalog.repository_files(revision)
    missing = catalog.missing_items(state, files)
    if clean:
        removed, errors = catalog.cleanup_stale(state, missing)
        print(f"仓库已更新至 {revision[:8]}，已清理 {removed} 份失效缓存。")
        if errors:
            raise RuntimeError("清理缓存失败，批量安装未开始。\n" + "\n".join(errors))

    records = catalog.load_applied()["issues"]
    pending = []
    for item in state["catalog"]["issues"]:
        ident = item["id"]
        if ident in (BATCH_ID, RETIRED_ID) or item.get("status") != "community-review":
            continue
        if ident in missing:
            print(f"跳过（仓库文件缺失）：{item['title']}")
            continue
        if not isinstance(item.get("risk"), str) or not item["risk"].strip():
            print(f"跳过（缺少风险说明）：{item['title']}")
            continue
        record = records.get(ident)
        if record and record["fingerprint"] == catalog.fingerprint(item):
            print(f"跳过（已安装且无更新）：{item['title']}")
            continue
        pending.append((item, "更新" if record else "安装"))
    return revision, files, pending


def confirm(pending, revision):
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk

    ready, _argv = Gtk.init_check()
    if not ready:
        raise RuntimeError("请在 Ubuntu 图形桌面运行一键升级。")
    dialog = Gtk.Dialog(title="TB710FU 一键升级", flags=Gtk.DialogFlags.MODAL)
    dialog.set_default_size(620, 520)
    dialog.set_position(Gtk.WindowPosition.CENTER)
    dialog.add_button("取消", Gtk.ResponseType.CANCEL)
    run_button = dialog.add_button("下载并应用", Gtk.ResponseType.OK)
    run_button.get_style_context().add_class("suggested-action")
    dialog.set_default_response(Gtk.ResponseType.CANCEL)
    content = dialog.get_content_area()
    content.set_spacing(10)
    content.set_border_width(12)
    heading = Gtk.Label(label=f"仓库版本 {revision[:8]} · 选择要安装或更新的修复")
    heading.set_xalign(0)
    content.pack_start(heading, False, False, 0)
    scroller = Gtk.ScrolledWindow()
    scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    content.pack_start(scroller, True, True, 0)
    rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
    scroller.add(rows)
    selected = []
    for item, action in pending:
        privilege = " · 需要管理员授权" if item["script"]["requires_admin"] else ""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        checkbox = Gtk.CheckButton(label=f"{action} · {item['title']}{privilege}")
        checkbox.set_active(True)
        checkbox.get_child().set_line_wrap(True)
        row.pack_start(checkbox, True, True, 0)
        path = quote(item["script"]["path"], safe="/")
        link = Gtk.LinkButton.new_with_label(
            f"https://github.com/fakerUZI/tb710fu-repair-catalog/blob/{revision}/{path}", "查看脚本")
        row.pack_start(link, False, False, 0)
        rows.pack_start(row, False, False, 0)
        risk = Gtk.Label(label=item["risk"], wrap=True, xalign=0)
        risk.set_margin_start(25)
        risk.set_max_width_chars(62)
        risk.set_selectable(True)
        rows.pack_start(risk, False, False, 0)
        selected.append((item, checkbox))
    try:
        dialog.show_all()
        dialog.present()
        if dialog.run() == Gtk.ResponseType.OK:
            return [item for item, checkbox in selected if checkbox.get_active()]
        return []
    finally:
        dialog.destroy()


def apply(catalog, item, revision, files):
    if not catalog.downloaded(item):
        print("正在下载并校验脚本和附件。")
        catalog.download(item, revision)
    if catalog.repository_changed(item, files):
        raise ValueError("脚本或附件与当前仓库版本不符。")
    with tempfile.TemporaryDirectory(prefix="tb710fu-batch-item-") as directory:
        workspace = Path(directory)
        attachments = workspace / "attachments"
        attachments.mkdir()
        script = catalog.stage_execution(item, workspace)
        command = ["/usr/bin/env", f"TB710FU_ATTACHMENTS_DIR={attachments}",
                   "TB710FU_FIX_ACTION=apply", "/bin/bash", str(script)]
        if item["script"]["requires_admin"]:
            command.insert(0, "/usr/bin/pkexec")
        result = subprocess.run(command, stdin=subprocess.DEVNULL, check=False)
    if result.returncode == 0:
        catalog.mark_applied(item, revision)
    return result.returncode


def upgrade(catalog, action):
    revision, files, pending = prepare(catalog, clean=action == "apply")
    if not pending:
        print("没有可安装或更新的修复。")
        return 0
    if action == "check":
        for item, change in pending:
            print(f"{change}：{item['title']}")
        return 0
    selected = confirm(pending, revision)
    if not selected:
        print("已取消安装。")
        return 0
    failed = 0
    for item in selected:
        status = catalog.applied_status(item)
        if status == "record-error":
            raise RuntimeError("安装记录无法读取，批量安装已暂停。")
        if status == "installed":
            print(f"跳过（已安装且无更新）：{item['title']}")
            continue
        print(f"\n开始应用：{item['title']}")
        try:
            code = apply(catalog, item, revision, files)
        except (OSError, ValueError) as error:
            print(f"未完成：{item['title']} · {catalog.error_message(error)}")
            failed += 1
            continue
        if code == 0:
            print(f"已完成：{item['title']}")
        elif item["script"]["requires_admin"] and code in (126, 127):
            print("管理员授权已取消或不可用，剩余条目已暂停。")
            return 1
        else:
            print(f"失败：{item['title']} · 退出码 {code}")
            failed += 1
    print(f"批量升级结束，{failed} 项失败。")
    return int(bool(failed))


def main(action):
    if os.geteuid() == 0:
        raise RuntimeError("请以当前桌面用户运行一键升级。")
    if action not in ("apply", "check"):
        raise RuntimeError("一键升级支持 apply 和 check。可从各修复条目回退。")
    if not (ASSISTANT / "catalog.py").is_file():
        raise RuntimeError("请先安装 TB710FU 修复助手 0.10.11 或更新版本。")
    sys.path.insert(0, str(ASSISTANT))
    import catalog

    if not hasattr(catalog, "cleanup_stale") or not hasattr(catalog, "load_applied"):
        raise RuntimeError("请将 TB710FU 修复助手更新至 0.10.11 或更新版本。")
    catalog.DATA.mkdir(parents=True, exist_ok=True)
    with (catalog.DATA / ".one-click-upgrade.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("一键升级已在运行。") from None
        return upgrade(catalog, action)


if __name__ == "__main__":
    try:
        if len(sys.argv) > 2:
            raise ValueError("用法：upgrade.py [apply|check]")
        sys.exit(main(sys.argv[1] if len(sys.argv) == 2 else "apply"))
    except (OSError, RuntimeError, ValueError) as error:
        sys.exit(f"一键升级：{error}")
