#!/usr/bin/python3
"""Enroll with a live preview and manage GDM face unlock from the desktop."""
import base64
import binascii

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk

ADMIN = "/usr/local/lib/tb710fu-face-unlock/settings-admin"
WIDTH, HEIGHT = 320, 240


class FaceSettings(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="人脸解锁", default_width=440)
        self.process = None
        self.capture = None
        self.preview = Gtk.Picture(width_request=WIDTH, height_request=HEIGHT, visible=False)
        self.status = Gtk.Label(label="请在光线充足处录入。锁屏后打开密码页，正视前置相机。",
                                wrap=True, xalign=0, selectable=True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                      margin_top=20, margin_bottom=20, margin_start=20, margin_end=20)
        box.append(self.preview)
        box.append(self.status)
        self.buttons = []
        for label, action in (("预览并录入", "enroll-preview"), ("启用", "enable"),
                              ("停用", "disable"), ("删除本账号的人脸资料", "forget")):
            button = Gtk.Button(label=label)
            button.connect("clicked", self.start, action)
            self.buttons.append(button)
            box.append(button)
        self.record = Gtk.Button(label="开始录入", visible=False, sensitive=False)
        self.record.connect("clicked", self.record_face)
        box.append(self.record)
        self.cancel = Gtk.Button(label="结束预览", visible=False)
        self.cancel.connect("clicked", self.cancel_preview)
        box.append(self.cancel)
        self.connect("close-request", self.close_window)
        self.set_child(box)

    def start(self, _button, action):
        if self.process is not None:
            return
        preview = action == "enroll-preview"
        flags = Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE
        if preview:
            flags |= Gio.SubprocessFlags.STDIN_PIPE
        try:
            self.process = Gio.Subprocess.new(("/usr/bin/pkexec", ADMIN, action), flags)
        except GLib.Error as error:
            self.status.set_label(str(error))
            return
        self.capture = self.process.get_stdin_pipe() if preview else None
        for button in self.buttons:
            button.set_sensitive(False)
        self.status.set_label("请在系统授权窗口中确认。")
        self.record.set_visible(preview)
        self.cancel.set_visible(preview)
        self.preview.set_visible(preview)
        output = Gio.DataInputStream.new(self.process.get_stdout_pipe())
        output.read_line_async(GLib.PRIORITY_DEFAULT, None, self.read_output)

    def read_output(self, stream, result):
        try:
            line, _size = stream.read_line_finish_utf8(result)
        except GLib.Error as error:
            line = None
            self.status.set_label(str(error))
            self.cancel_preview()
        if line is None:
            self.process.wait_async(None, self.finished)
            return
        if line.startswith("PREVIEW "):
            if self.preview.get_visible():
                try:
                    pixels = base64.b64decode(line[8:], validate=True)
                    if len(pixels) == WIDTH * HEIGHT * 3:
                        texture = Gdk.MemoryTexture.new(
                            WIDTH, HEIGHT, Gdk.MemoryFormat.R8G8B8, GLib.Bytes.new(pixels), WIDTH * 3,
                        )
                        self.preview.set_paintable(texture)
                        self.record.set_sensitive(self.capture is not None)
                except (binascii.Error, ValueError):
                    pass
        elif line.startswith("PROGRESS "):
            self.status.set_label(f"正在录入（{line[9:]}/6）")
        elif line:
            self.status.set_label(line)
        stream.read_line_async(GLib.PRIORITY_DEFAULT, None, self.read_output)

    def record_face(self, _button):
        if self.capture is None:
            return
        try:
            self.capture.write_all(b"START\n", None)
        except GLib.Error as error:
            self.status.set_label(str(error))
        self.cancel_preview()
        self.record.set_visible(True)

    def cancel_preview(self, *_args):
        if self.capture is not None:
            try:
                self.capture.close(None)
            except GLib.Error:
                pass
            self.capture = None
        self.record.set_sensitive(False)
        self.cancel.set_visible(False)

    def close_window(self, _window):
        self.cancel_preview()
        return False

    def finished(self, process, result):
        process.wait_finish(result)
        if not process.get_successful() and self.status.get_label() == "请在系统授权窗口中确认。":
            self.status.set_label("未能完成系统授权。")
        self.cancel_preview()
        self.process = None
        self.preview.set_visible(False)
        self.preview.set_paintable(None)
        self.record.set_visible(False)
        for button in self.buttons:
            button.set_sensitive(True)


def show(app):
    window = app.get_active_window() or FaceSettings(app)
    window.present()


app = Gtk.Application(application_id="org.tb710fu.FaceSettings")
app.connect("activate", show)
app.run()
