import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import Meta from 'gi://Meta';
import St from 'gi://St';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import {switchPrimary} from './primary-monitor.js';
import {DesktopKeys} from './desktop-keys.js';
import {Touchpad} from './touchpad.js';

function displayPair() {
    const logical = global.backend.get_monitor_manager().get_logical_monitors();
    let tablet, external, tabletConnector, externalConnector;
    for (const group of logical) {
        const monitors = group.get_monitors();
        if (monitors.length !== 1)
            continue;
        const [monitor] = monitors;
        const geometry = Main.layoutManager.monitors[group.get_number()];
        const connector = monitor.get_connector();
        if (monitor.is_builtin()) {
            tablet = geometry;
            tabletConnector = connector;
        } else if (!external && /^(DP|HDMI)-/.test(connector)) {
            external = geometry;
            externalConnector = connector;
        }
    }
    return tablet && external ? {tablet, external, tabletConnector, externalConnector} : null;
}

function button(label, action) {
    const actor = new St.Button({label, can_focus: false, style_class: 'button'});
    actor.connect('clicked', action);
    return actor;
}

const clamp = (value, low, high) => Math.max(low, Math.min(value, high));

export default class ExternalDesktopInput extends Extension {
    enable() {
        this._indicator = new PanelMenu.Button(0, '外接桌面控制');
        this._indicator.add_child(new St.Icon({
            icon_name: 'input-touchpad-symbolic', style_class: 'system-status-icon',
        }));
        this._indicator.menu.addAction('打开全屏键盘与触控板', () => this._start());
        Main.panel.addToStatusArea(this.uuid, this._indicator);
        // When the external screen is primary, its top bar is out of reach of
        // the tablet touchscreen. Keep a local entry on the secondary panel.
        this._localEntry = button('外接桌面控制', () => this._start());
        this._localEntry.add_style_class_name('tb-external-entry');
        Main.layoutManager.addChrome(this._localEntry);
        this._monitors = Main.layoutManager.connect('monitors-changed', () => this._sync());
        this._displayChanges ??= Promise.resolve();
        this._session = Main.sessionMode.connect('updated', () => this._sync());
        this._sync();
    }

    _sync() {
        this._pair = !Main.sessionMode.isLocked &&
            (Main.sessionMode.currentMode === 'user' || Main.sessionMode.parentMode === 'user')
            ? displayPair() : null;
        this._indicator.visible = !!this._pair;
        this._syncEntry();
        if (!this._pair) {
            this._stop();
        } else if (this._keyboard) {
            this._layout();
            this._motion(0, 0);
        }
    }

    _syncEntry() {
        const m = this._pair?.tablet;
        this._localEntry.visible = !!m && m.index !== Main.layoutManager.primaryIndex && !this._restore;
        if (this._localEntry.visible) {
            const [, width] = this._localEntry.get_preferred_width(-1);
            this._localEntry.set_position(m.x + m.width - width - 12, m.y + 12);
        }
    }

    // Shell 50 keeps the OSK and its input-method controller together. Pin
    // this instance only for the explicit control session, preserving the
    // tablet's installed key, shortcut, candidate and clipboard handlers.
    _replace(object, key, replacement) {
        const own = Object.getOwnPropertyDescriptor(object, key);
        object[key] = replacement;
        this._restore.push(() => {
            if (object[key] !== replacement)
                return;
            if (own)
                Object.defineProperty(object, key, own);
            else
                delete object[key];
        });
    }

    _start() {
        this._sync();
        if (!this._pair || this._restore)
            return;
        Main.overview.hide();
        this._restore = [];
        this._syncEntry();
        try {
            const manager = Main.keyboard;
            const settings = manager._a11yApplicationsSettings;
            const sessionSettings = new Gio.Settings({settings_schema: settings.settings_schema,
                backend: Gio.memory_settings_backend_new()});
            sessionSettings.set_boolean('screen-keyboard-enabled', true);
            this._replace(manager, '_a11yApplicationsSettings', sessionSettings);
            manager._syncEnabled();
            const keyboard = this._keyboard = manager.keyboardActor;
            // Attach current customizations now if the touch-mode keyboard was
            // created above, before wrapping its session-specific geometry.
            this._integrations = [];
            for (const [uuid, attach, detach] of [
                ['tb-osk-input-policy@tb710fu', '_wrapKeyboard', '_unwrapKeyboard'],
                ['tb-osk-shortcuts@tb710fu', '_attach', '_detach'],
                ['tb-backspace-fallback@tb710fu', '_attach', '_restore'],
                ['tb-wowtype-osk@tb710fu', '_attach', '_restore'],
            ]) {
                const installed = Main.extensionManager.lookup(uuid);
                if (installed?.state !== 1)
                    continue;
                const ext = installed.stateObj;
                ext[attach]();
                this._integrations.push(() => ext[detach]());
            }
            keyboard._clearShowIdle();
            keyboard._clearKeyboardRestTimer();
            keyboard.close(true);
            // The ordinary OSK stays closed so Kimpanel follows its normal
            // hardware-keyboard path and anchors candidates at the app caret.
            this._replace(keyboard, 'open', () => {});
            this._replace(manager, 'open', () => {});
            const seat = global.stage.context.get_backend().get_default_seat();
            const shortcuts = Main.extensionManager.lookup('tb-osk-shortcuts@tb710fu');
            this._keys = new DesktopKeys(keyboard._keyboardController._virtualDevice,
                seat.get_keymap(), () => {
                    if (shortcuts?.state === 1)
                        shortcuts.stateObj._toggleLanguage();
                    else
                        Gio.DBus.session.call('org.fcitx.Fcitx5', '/controller',
                            'org.fcitx.Fcitx.Controller1', 'Toggle', null, null,
                            Gio.DBusCallFlags.NONE, 1000, null, null);
                });
            this._pointer = seat.create_virtual_device(
                Clutter.InputDeviceType.POINTER_DEVICE);
            this._touchpad = new Touchpad((dx, dy) => this._motion(dx * 1.6, dy * 1.6),
                () => this._click(1), (dx, dy) => this._scroll(dx, dy, false),
                () => this._scroll(0, 0, true));
            this._buildControls();
            this._layout();
            const target = this._pair.external;
            this._x = target.x + target.width / 2;
            this._y = target.y + target.height / 2;
            seat.warp_pointer(Math.floor(this._x), Math.floor(this._y));
            this._watchCursor();
            this._useExternalPrimary();
            const windows = global.display.get_tab_list(Meta.TabList.NORMAL,
                global.workspace_manager.get_active_workspace());
            const existing = windows.find(w => !w.minimized && w.get_monitor() === target.index);
            if (existing)
                Main.activateWindow(existing);
            this._moveFocusedWindow();
            this._focus = global.display.connect('notify::focus-window', () => this._moveFocusedWindow());
        } catch (error) {
            this._stop();
            console.error(error);
            Main.notify('未能打开外接桌面控制', error.message);
        }
    }

    _useExternalPrimary() {
        const {tabletConnector, externalConnector} = this._pair;
        const lease = {changed: false};
        this._primaryLease = lease;
        this._displayChanges = this._displayChanges.then(async () => {
            lease.changed = await switchPrimary(tabletConnector, externalConnector,
                () => this._primaryLease === lease);
        }).catch(error => {
            if (this._primaryLease === lease)
                Main.notify('无法将应用菜单切换到外屏', error.message);
        });
        lease.release = () => {
            this._displayChanges = this._displayChanges.then(async () => {
                if (lease.changed)
                    await switchPrimary(externalConnector, tabletConnector, () => true);
            }).catch(error => console.warn('恢复主屏时显示配置已变化', error));
        };
    }

    _watchCursor() {
        // Mutter hides the hardware pointer on touchscreen input. Keep a
        // non-interactive arrow at the touchpad's last external position for
        // that interval. Native cursors, including text and resize shapes,
        // take over again as soon as Mutter shows them.
        const tracker = global.backend.get_cursor_tracker();
        const size = new Gio.Settings({schema_id: 'org.gnome.desktop.interface'}).get_int('cursor-size');
        const arrow = new St.DrawingArea({width: size, height: size, reactive: false,
            can_focus: false, visible: false});
        this._cursor = arrow;
        arrow.connect('repaint', () => {
            const cr = arrow.get_context();
            const [w, h] = arrow.get_surface_size();
            cr.scale(w / 24, h / 24);
            cr.moveTo(1, 1);
            for (const [x, y] of [[1, 19], [6, 15], [10, 23], [14, 21], [10, 14], [17, 14]])
                cr.lineTo(x, y);
            cr.closePath();
            cr.setSourceRGBA(0.08, 0.08, 0.08, 1);
            cr.fillPreserve();
            cr.setLineWidth(1.6);
            cr.setSourceRGBA(1, 1, 1, 1);
            cr.stroke();
            cr.$dispose();
        });
        Main.uiGroup.add_child(arrow);
        const update = () => {
            arrow.set_position(Math.floor(this._x), Math.floor(this._y));
            arrow.visible = !tracker.get_pointer_visible();
        };
        tracker.connectObject('visibility-changed', update, arrow);
        update();
    }

    _moveFocusedWindow() {
        const w = global.display.focus_window;
        const target = this._pair?.external;
        if (target && w && !w.is_override_redirect() &&
            [Meta.WindowType.NORMAL, Meta.WindowType.DIALOG, Meta.WindowType.MODAL_DIALOG]
                .includes(w.get_window_type()) && w.get_monitor() !== target.index)
            w.move_to_monitor(target.index);
    }

    _buildControls() {
        this._deck = new St.BoxLayout({orientation: Clutter.Orientation.VERTICAL,
            reactive: true, style_class: 'tb-external-deck'});
        Main.layoutManager.addTopChrome(this._deck);
        this._bar = new St.BoxLayout({style_class: 'tb-external-controls'});
        this._deck.add_child(this._bar);
        this._deck.add_child(this._keys.actor);
        this._bar.add_child(new St.Label({text: '外接桌面', x_expand: true,
            y_align: Clutter.ActorAlign.CENTER}));
        this._toggle = button('全屏键盘', () => {
            this._touchpad.reset();
            this._pad.visible = !this._pad.visible;
            this._toggle.label = this._pad.visible ? '全屏键盘' : '显示触控板';
            this._layout();
        });
        this._bar.add_child(this._toggle);
        this._bar.add_child(button('退出', () => this._stop()));
        this._pad = new St.BoxLayout({style_class: 'tb-external-pad',
            orientation: Clutter.Orientation.VERTICAL});
        this._deck.add_child(this._pad);
        const area = new St.Bin({reactive: true, y_expand: true, x_expand: true,
            style_class: 'tb-external-surface', accessible_name: '外接桌面触控板'});
        area.set_child(new St.Label({text: '单指移动或轻点　·　双指滚动',
            x_align: Clutter.ActorAlign.CENTER, y_align: Clutter.ActorAlign.CENTER}));
        area.connect('touch-event', (_actor, event) => {
            const id = event.get_event_sequence().get_slot();
            const [x, y] = event.get_coords();
            const type = event.type();
            if (type === Clutter.EventType.TOUCH_BEGIN)
                this._touchpad.begin(id, x, y, event.get_time());
            else if (type === Clutter.EventType.TOUCH_UPDATE)
                this._touchpad.update(id, x, y);
            else if (type === Clutter.EventType.TOUCH_END || type === Clutter.EventType.TOUCH_CANCEL)
                this._touchpad.end(id, x, y, event.get_time(), type === Clutter.EventType.TOUCH_CANCEL);
            return Clutter.EVENT_STOP;
        });
        this._pad.add_child(area);
        const clicks = new St.BoxLayout({style_class: 'tb-external-clicks'});
        for (const [label, number] of [['左键', 1], ['右键', 3]]) {
            const key = button(label, () => this._click(number));
            key.x_expand = true;
            clicks.add_child(key);
        }
        this._pad.add_child(clicks);
    }

    _layout() {
        if (!this._pair || !this._keyboard || !this._pad)
            return;
        const m = this._pair.tablet;
        this._deck.set_position(m.x, m.y);
        this._deck.set_size(m.width, m.height);
        this._pad.height = Math.round(m.height * 0.29);
    }

    _motion(dx, dy) {
        const m = this._pair.external;
        this._x = clamp(this._x + dx, m.x, m.x + m.width - 1);
        this._y = clamp(this._y + dy, m.y, m.y + m.height - 1);
        this._cursor?.set_position(Math.floor(this._x), Math.floor(this._y));
        this._pointer.notify_absolute_motion(GLib.get_monotonic_time(), this._x, this._y);
    }

    _click(number) {
        this._motion(0, 0);
        for (const state of [Clutter.ButtonState.PRESSED, Clutter.ButtonState.RELEASED])
            this._pointer.notify_button(GLib.get_monotonic_time(), number, state);
    }

    _scroll(dx, dy, finish) {
        this._motion(0, 0);
        this._pointer.notify_scroll_continuous(GLib.get_monotonic_time(), dx, dy,
            Clutter.ScrollSource.FINGER, finish
                ? Clutter.ScrollFinishFlags.HORIZONTAL | Clutter.ScrollFinishFlags.VERTICAL
                : Clutter.ScrollFinishFlags.NONE);
    }

    _stop() {
        if (!this._restore)
            return;
        this._primaryLease?.release();
        this._primaryLease = null;
        this._cursor?.destroy();
        this._cursor = null;
        if (this._focus)
            global.display.disconnect(this._focus);
        this._focus = 0;
        // A cable removal invalidates the old desktop coordinates.
        if (this._pair)
            this._touchpad?.reset();
        this._touchpad = this._pointer = null;
        for (const restore of this._restore.reverse())
            restore();
        this._restore = null;
        this._deck?.destroy();
        this._deck = this._bar = this._pad = this._toggle = this._keys = null;
        this._keyboard = null;
        const manager = Main.keyboard;
        if (!manager._a11yApplicationsSettings.get_boolean('screen-keyboard-enabled') &&
            !(manager._seat.get_touch_mode() && manager._lastDeviceIsTouchscreen())) {
            for (const detach of (this._integrations ?? []).reverse())
                detach();
        }
        this._integrations = null;
        manager._syncEnabled();
        this._syncEntry();
    }

    disable() {
        this._stop();
        Main.layoutManager.disconnect(this._monitors);
        Main.sessionMode.disconnect(this._session);
        this._localEntry.destroy();
        this._indicator.destroy();
        this._indicator = this._pair = null;
    }
}
