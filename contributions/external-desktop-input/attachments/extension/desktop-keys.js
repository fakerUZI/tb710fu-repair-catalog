import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import St from 'gi://St';

// Linux evdev positions, using the same US QWERTY layout as the tablet.
// Widths are quarter-key units. Shift uses the system keymap like a USB keyboard.
const key = (label, code, width = 4, upper = '') => ({label, code, width, upper});
const letters = (text, first) => [...text].map((label, i) => key(label, first + i));
const punctuation = (label, upper, code, width = 4) => key(label, code, width, upper);
const functionKeys = Array.from({length: 12}, (_, i) => key('F' + (i + 1), i < 10 ? 59 + i : 87 + i - 10));
const rows = [
    [key('Esc', 1, 6), ...functionKeys, key('Del', 111, 6)],
    [punctuation('`', '~', 41), ...[...'1234567890'].map((n, i) =>
        punctuation(n, '!@#$%^&*()'[i], 2 + i)), punctuation('-', '_', 12),
        punctuation('=', '+', 13), key('Backspace', 14, 8)],
    [key('Tab', 15, 6), ...letters('qwertyuiop', 16), punctuation('[', '{', 26),
        punctuation(']', '}', 27), punctuation('\\', '|', 43, 6)],
    [key('Caps', 58, 7), ...letters('asdfghjkl', 30), punctuation(';', ':', 39),
        punctuation("'", '"', 40), key('Enter', 28, 9)],
    [key('Shift', 42, 9), ...letters('zxcvbnm', 44), punctuation(',', '<', 51),
        punctuation('.', '>', 52), punctuation('/', '?', 53), key('↑', 103, 5), key('Shift', 42, 6)],
    [key('Ctrl', 29, 5), key('Super', 125, 5), key('Alt', 56, 5), key('中 / 英', 0, 6),
        key('空格', 57, 23), key('←', 105, 5), key('↓', 108, 5), key('→', 106, 6)],
];
const modifiers = new Set([29, 42, 56, 125]);

export class DesktopKeys {
    constructor(device, keymap, switchLanguage) {
        this._device = device;
        this._keymap = keymap;
        this._switchLanguage = switchLanguage;
        this._latched = new Set();
        this._buttons = [];
        const grid = new Clutter.GridLayout({row_homogeneous: true, column_homogeneous: true});
        this.actor = new St.Widget({style_class: 'tb-desktop-keys', layout_manager: grid,
            x_expand: true, y_expand: true});
        for (const [row, keys] of rows.entries()) {
            let column = 0;
            for (const spec of keys) {
                const {label, code, width, upper} = spec;
                const letter = /^[a-z]$/.test(label);
                const style = row === 0 ? 'function' : letter || upper ? 'character' :
                    code === 28 ? 'enter' : 'control';
                const button = new St.Button({accessible_name: label, reactive: true,
                    can_focus: false, track_hover: true, x_expand: true, y_expand: true,
                    style_class: 'tb-desktop-key tb-key-' + style});
                const face = new St.BoxLayout({orientation: Clutter.Orientation.VERTICAL,
                    x_align: Clutter.ActorAlign.CENTER, y_align: Clutter.ActorAlign.CENTER});
                const main = new St.Label({text: letter ? label.toUpperCase() : label,
                    x_align: Clutter.ActorAlign.CENTER});
                if (upper)
                    face.add_child(new St.Label({text: upper, style_class: 'tb-key-symbol',
                        x_align: Clutter.ActorAlign.CENTER}));
                face.add_child(main);
                button.set_child(face);
                // Keys are activated on release so dragging off cancels a tap.
                button.connect('clicked', () => this._activate(spec));
                grid.attach(button, column, row, width, 1);
                column += width;
                this._buttons.push([button, code]);
            }
        }
        keymap.connectObject('notify::caps-lock-state', () => this._syncModifiers(), this.actor);
        this._syncModifiers();
    }

    _send(code, pressed) {
        this._device.notify_key(GLib.get_monotonic_time(), code,
            pressed ? Clutter.KeyState.PRESSED : Clutter.KeyState.RELEASED);
    }

    _activate({code}) {
        if (modifiers.has(code)) {
            if (!this._latched.delete(code))
                this._latched.add(code);
        } else {
            const held = [...this._latched];
            this._latched.clear();
            if (code === 0) {
                this._switchLanguage();
            } else {
                for (const mod of held)
                    this._send(mod, true);
                try {
                    this._send(code, true);
                    this._send(code, false);
                } finally {
                    for (const mod of held.reverse())
                        this._send(mod, false);
                }
            }
        }
        this._syncModifiers();
    }

    _syncModifiers() {
        for (const [button, code] of this._buttons) {
            if (this._latched.has(code) || (code === 58 && this._keymap.get_caps_lock_state()))
                button.add_style_pseudo_class('latched');
            else
                button.remove_style_pseudo_class('latched');
        }
    }
}
