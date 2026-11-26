"""Choose the current desktop user's system-password face verification mode."""
import subprocess

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk

dialog = Gtk.Dialog(title='系统密码人脸验证', modal=True)
dialog.add_button('取消', Gtk.ResponseType.CANCEL)
dialog.add_button('应用', Gtk.ResponseType.OK)
body = dialog.get_content_area()
body.set_border_width(20)
body.pack_start(Gtk.Label(label='先在「人脸解锁」录入本账号，再选择何时使用人脸验证。'), False, False, 8)
choice = Gtk.ComboBoxText()
for value, label in [('off', '关闭'), ('unlock', '仅解锁后'), ('boot', '仅开机'), ('always', '始终')]:
    choice.append(value, label)
choice.set_active(0)
body.pack_start(choice, False, False, 8)
dialog.show_all()
if dialog.run() == Gtk.ResponseType.OK:
    result = subprocess.run(['/usr/bin/pkexec', '/usr/local/lib/tb710fu-face-unlock/configure',
                             choice.get_active_id()], capture_output=True, text=True)
    if result.returncode and result.returncode != 126:
        message = Gtk.MessageDialog(transient_for=dialog, modal=True, buttons=Gtk.ButtonsType.CLOSE,
                                    text='未能更改验证方式')
        message.format_secondary_text(result.stderr.strip())
        message.run()
        message.destroy()
dialog.destroy()
