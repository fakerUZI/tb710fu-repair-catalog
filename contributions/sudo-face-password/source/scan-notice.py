"""Show a temporary GNOME banner for the lifetime of one camera check."""
import os
import sys

from gi.repository import Gio, GLib


def main():
    if os.geteuid() == 0:
        return
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    interface = "org.freedesktop.Notifications"
    endpoint = (interface, "/org/freedesktop/Notifications", interface)
    hints = {key: GLib.Variant("b", True) for key in ("transient", "suppress-sound")}
    # Expiry is also a backstop if the desktop or authentication process exits.
    arguments = GLib.Variant("(susssasa{sv}i)", (
        "系统授权", 0, "camera-web-symbolic", "正在识别人脸", "请正视前置摄像头。", [], hints, 10000,
    ))
    result = bus.call_sync(*endpoint, "Notify", arguments, GLib.VariantType("(u)"),
                           Gio.DBusCallFlags.NONE, 600, None)
    sys.stdin.buffer.read(1)
    bus.call_sync(*endpoint, "CloseNotification", result, None, Gio.DBusCallFlags.NONE, 500, None)


if __name__ == "__main__":
    try:
        main()
    except GLib.Error:
        pass
