#!/usr/bin/python3
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, Gtk


def show(app):
    settings = Gio.Settings.new("org.tb710fu.screen-gestures")
    window = Gtk.ApplicationWindow(application=app, title="屏幕手势", default_width=400)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16,
                  margin_top=20, margin_bottom=20, margin_start=20, margin_end=20)
    for key, title in (("double-tap-to-wake", "双击亮屏"),
                       ("desktop-click-to-lock", "双击桌面空白处锁屏")):
        row = Gtk.Box(spacing=16)
        row.append(Gtk.Label(label=title, hexpand=True, xalign=0))
        toggle = Gtk.Switch()
        row.append(toggle)
        settings.bind(key, toggle, "active", Gio.SettingsBindFlags.DEFAULT)
        box.append(row)
    window.set_child(box)
    window.present()


app = Gtk.Application(application_id="org.tb710fu.GestureSettings")
app.connect("activate", show)
app.run()
