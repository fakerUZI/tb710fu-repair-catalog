import GLib from 'gi://GLib';
import Clutter from 'gi://Clutter';
import St from 'gi://St';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';

export default class OskToggle extends Extension {
    enable() {
        this._button = new PanelMenu.Button(0.0, '屏幕键盘', true);
        this._button.add_child(new St.Icon({icon_name: 'input-keyboard-symbolic',
            style_class: 'system-status-icon'}));
        const toggle = () => {
            if (Main.keyboard.visible) {
                Main.keyboard.close();
            } else {
                GLib.idle_add_once(GLib.PRIORITY_DEFAULT_IDLE, () =>
                    Main.keyboard.open(Main.layoutManager.focusIndex));
            }
        };
        this._button.connect('button-press-event', () => {
            toggle();
            return Clutter.EVENT_STOP;
        });
        this._button.connect('touch-event', (_actor, event) => {
            if (event.type() === Clutter.EventType.TOUCH_END)
                toggle();
            return Clutter.EVENT_STOP;
        });
        Main.panel.addToStatusArea('tb-osk-toggle', this._button);
    }
    disable() {
        this._button?.destroy();
        this._button = null;
    }
}
