#!/usr/bin/python3
"""Root-owned PM8550 torch broker; only active local desktop sessions may write."""
import math
from pathlib import Path
import signal
import time

BUS = 'org.tb710fu.Torch1'
OBJECT = '/org/tb710fu/Torch1'
INTERFACE = BUS
LED = Path('/sys/class/leds/tb710fu:flash:rear')
XML = '''<node><interface name="org.tb710fu.Torch1">
<method name="SetEnabled"><arg type="b" direction="in"/></method>
<method name="GetState"><arg type="b" direction="out"/><arg type="u" direction="out"/></method>
<signal name="Changed"><arg type="b"/><arg type="u"/></signal>
</interface></node>'''


class Torch:
    def __init__(self, root=LED, timeout=60, clock=time.monotonic):
        self.root, self.timeout, self.clock = Path(root), timeout, clock
        self.deadline = None
        self.off()

    def off(self):
        (self.root/'brightness').write_text('0')
        self.deadline = None

    def set(self, enabled):
        if not enabled:
            self.off()
            return
        if self.deadline is not None:
            return  # Repeated requests never extend the 60-second lease.
        if (self.root/'flash_fault').read_text().strip():
            raise RuntimeError('闪光灯硬件报告故障，已保持关闭')
        # OEM torch maximum is 2 x 100mA. This requests ~100mA total, not flash.
        (self.root/'brightness').write_text('128')
        self.deadline = self.clock() + self.timeout

    def state(self):
        if self.deadline is not None and self.clock() >= self.deadline:
            self.off()
        enabled = (self.root/'brightness').read_text().strip() != '0'
        if enabled and self.deadline is None:
            self.off()
            enabled = False
        remaining = math.ceil(max(0, self.deadline-self.clock())) if self.deadline is not None else 0
        return enabled, remaining


def main():
    import gi
    gi.require_version('Gio','2.0')
    from gi.repository import Gio, GLib
    torch = Torch()
    loop = GLib.MainLoop()
    connection = None
    last = False
    owner_session = None

    def call(bus, path, interface, method, args, result):
        return connection.call_sync(bus,path,interface,method,args,GLib.VariantType.new(result),
                                    Gio.DBusCallFlags.NONE,1200,None).unpack()

    def authorize(sender):
        uid = call('org.freedesktop.DBus','/org/freedesktop/DBus','org.freedesktop.DBus',
                   'GetConnectionUnixUser',GLib.Variant('(s)',(sender,)),'(u)')[0]
        if uid == 0:
            return None
        pid = call('org.freedesktop.DBus','/org/freedesktop/DBus','org.freedesktop.DBus',
                   'GetConnectionUnixProcessID',GLib.Variant('(s)',(sender,)),'(u)')[0]
        try:
            session = call('org.freedesktop.login1','/org/freedesktop/login1','org.freedesktop.login1.Manager',
                           'GetSessionByPID',GLib.Variant('(u)',(pid,)),'(o)')[0]
        except GLib.Error:
            # GNOME Shell may run in user@UID.service, outside the login scope.
            user = call('org.freedesktop.login1','/org/freedesktop/login1','org.freedesktop.login1.Manager',
                        'GetUser',GLib.Variant('(u)',(uid,)),'(o)')[0]
            display = call('org.freedesktop.login1',user,'org.freedesktop.DBus.Properties','Get',
                           GLib.Variant('(ss)',('org.freedesktop.login1.User','Display')),'(v)')[0]
            session = display[1]
        def prop(name):
            return call('org.freedesktop.login1',session,'org.freedesktop.DBus.Properties','Get',
                        GLib.Variant('(ss)',('org.freedesktop.login1.Session',name)),'(v)')[0]
        if not (prop('Active') and not prop('Remote') and prop('Class') == 'user'
                and prop('Type') in ('x11','wayland') and prop('User')[0] == uid):
            raise PermissionError('仅活动的本机桌面会话可操作手电筒')
        return session

    def changed():
        nonlocal last
        enabled, remaining = torch.state()
        if connection is not None and enabled != last:
            connection.emit_signal(None,OBJECT,INTERFACE,'Changed',GLib.Variant('(bu)',(enabled,remaining)))
        last = enabled

    def method(conn, sender, path, interface, name, parameters, invocation):
        nonlocal owner_session
        try:
            if name == 'SetEnabled':
                owner = authorize(sender)
                enabled = parameters.unpack()[0]
                torch.set(enabled)
                owner_session = owner if enabled else None
                changed()
                invocation.return_value(None)
            elif name == 'GetState':
                invocation.return_value(GLib.Variant('(bu)',torch.state()))
            else:
                invocation.return_dbus_error(INTERFACE+'.UnknownMethod','Unknown method')
        except (GLib.Error,PermissionError) as exc:
            invocation.return_dbus_error(INTERFACE+'.NotAuthorized','仅活动的本机桌面会话或管理员可操作')
        except (OSError,RuntimeError) as exc:
            try:torch.off()
            except OSError:pass
            invocation.return_dbus_error(INTERFACE+'.HardwareError',str(exc))

    def acquired(conn, name):
        nonlocal connection
        connection = conn
        info = Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0]
        conn.register_object(OBJECT,info,method,None,None)
        def sleeping(conn,sender,path,iface,name,parameters,*unused):
            if parameters.unpack()[0]:
                torch.off();changed()
        conn.signal_subscribe('org.freedesktop.login1','org.freedesktop.login1.Manager','PrepareForSleep',
                              '/org/freedesktop/login1',None,Gio.DBusSignalFlags.NONE,sleeping)
        def session_changed(conn,sender,path,iface,name,parameters,*unused):
            if path != owner_session:
                return
            _, props, _ = parameters.unpack()
            if props.get('LockedHint') or props.get('Active') is False:
                torch.off();changed()
        conn.signal_subscribe('org.freedesktop.login1','org.freedesktop.DBus.Properties','PropertiesChanged',
                              None,None,Gio.DBusSignalFlags.NONE,session_changed)

    def stop(*unused):
        torch.off();loop.quit();return False
    for sig in (signal.SIGTERM,signal.SIGINT):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT,sig,stop)
    def tick():
        changed();return True
    GLib.timeout_add(250,tick)
    name_id = Gio.bus_own_name(Gio.BusType.SYSTEM,BUS,Gio.BusNameOwnerFlags.NONE,acquired,None,lambda *args:stop())
    try:loop.run()
    finally:
        torch.off();Gio.bus_unown_name(name_id)

if __name__ == '__main__':main()
