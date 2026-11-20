import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const BOTTOM_HEIGHT = 48;
const BOTTOM_FRACTION = 0.62;
const PILL_WIDTH = 144;
const PILL_HEIGHT = 7;
const PILL_BOTTOM_MARGIN = 13;
const EDGE_WIDTH = 22;
const FOCUS_OUTSET = 10;
const BACK_HEIGHT = 60;
const MIN_BACK_SWIPE = 35;
const MIN_UP_SWIPE = 24;
const HOLD_MS = 260;
const HOLD_SLOP = 10;
const KEY_BACK = 158;  // Droidloom maps Linux KEY_BACK to Android KEYCODE_BACK.

export class AndroidNavigation {
    constructor(isAndroidWindow) {
        this._isAndroidWindow = isAndroidWindow;
        this._window = null;
        this._keyboard = Clutter.get_default_backend().get_default_seat()
            .create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
        this._bottom = this._addGestureTarget('bottom', '上滑最小化安卓窗口，上滑停住查看最近任务');
        this._left = this._addGestureTarget('left', '从屏幕左侧向内滑动返回');
        this._right = this._addGestureTarget('right', '从屏幕右侧向内滑动返回');
        this._windowLeft = this._addGestureTarget('left', '从安卓窗口左侧向内滑动返回');
        this._windowRight = this._addGestureTarget('right', '从安卓窗口右侧向内滑动返回');
        // Paint in its own chrome actor, independent of the transparent input targets.
        this._pill = new St.Widget({style_class: 'tb-android-nav-pill', reactive: false,
            visible: false, width: PILL_WIDTH, height: PILL_HEIGHT});
        this._pill.set_pivot_point(0.5, 0.5);
        Main.layoutManager.addTopChrome(this._pill);
        this._backPreview = new St.Bin({style_class: 'tb-android-back-preview', reactive: false,
            height: BACK_HEIGHT, visible: false,
            child: new St.Icon({icon_name: 'go-previous-symbolic', icon_size: 20,
                x_align: Clutter.ActorAlign.CENTER, y_align: Clutter.ActorAlign.CENTER})});
        Main.layoutManager.addTopChrome(this._backPreview);
        const keyboardBox = Main.layoutManager.keyboardBox;
        this._signals = [
            [Main.overview, Main.overview.connect('showing', () => this._hide())],
            [Main.overview, Main.overview.connect('hidden', () => this.sync(this._window))],
            [Main.sessionMode, Main.sessionMode.connect('updated', () => this.sync(this._window))],
            [Main.keyboard, Main.keyboard.connect('visibility-changed', () => this.sync(this._window))],
            // The box remains visible throughout GNOME's show and hide animations.
            [keyboardBox, keyboardBox.connect('notify::visible', () => this.sync(this._window))],
            [global.window_manager, global.window_manager.connect('map', () => this._syncFocusShadow())],
            [global.window_manager, global.window_manager.connect('destroy', (_, actor) =>
                this._syncFocusShadow(actor.meta_window))],
            [global.workspace_manager, global.workspace_manager.connect('active-workspace-changed', () =>
                this.sync(this._window))],
        ];
        const keyboardSwipe = Main.keyboard._bottomDragGesture;
        if (keyboardSwipe) {
            const id = keyboardSwipe.connect('should-handle-sequence', (_, event) =>
                !this._startsOnNavigation(event));
            this._signals.push([keyboardSwipe, id]);
        }
    }

    _startsOnNavigation(event) {
        if (!this._bottom.visible)
            return false;
        // GNOME still owns bottom-edge swipes everywhere outside the nav area.
        const [x, y] = event.get_coords();
        const [left, top] = this._bottom.get_position();
        return x >= left && x < left + this._bottom.width && y >= top && y < top + BOTTOM_HEIGHT;
    }

    _addGestureTarget(edge, name) {
        const target = new St.Widget({accessible_name: name, reactive: true, visible: false});
        target.connect('captured-event', (_, event) => this._onGesture(target, edge, event));
        Main.layoutManager.addTopChrome(target);
        return target;
    }

    sync(window) {
        if (window !== this._window) {
            this._clearGesture(false);
            this._closeChooser();
            this._clearFocusShadow();
        }
        this._window = window;
        if (!window || window.minimized || global.display.focus_window !== window ||
            Main.overview.visible || Main.sessionMode.isLocked || Main.keyboard.visible ||
            Main.layoutManager.keyboardBox.visible) {
            this._hide();
            return;
        }
        const monitorIndex = window.get_monitor();
        const monitor = Main.layoutManager.monitors[monitorIndex];
        if (!monitor) {
            this._hide();
            return;
        }
        const fullscreen = window.is_fullscreen();
        const frame = fullscreen ? monitor : window.get_frame_rect();
        const area = fullscreen ? monitor : window.get_work_area_for_monitor(monitorIndex);
        const x = Math.max(frame.x, area.x);
        const y = Math.max(frame.y, area.y);
        const right = Math.min(frame.x + frame.width, area.x + area.width);
        const bottom = Math.min(frame.y + frame.height, area.y + area.height);
        if (right - x < 160 || bottom - y < 140) {
            this._hide();
            return;
        }
        this._bounds = {x, y, right, bottom};
        const width = Math.round((right - x) * BOTTOM_FRACTION);
        const pillWidth = Math.min(PILL_WIDTH, width);
        this._bottom.set_position(x + Math.round((right - x - width) / 2),
            bottom - BOTTOM_HEIGHT);
        this._bottom.set_size(width, BOTTOM_HEIGHT);
        this._pill.set_width(pillWidth);
        this._pill.set_position(x + Math.round((right - x - pillWidth) / 2),
            bottom - PILL_HEIGHT - PILL_BOTTOM_MARGIN);
        const screenRight = monitor.x + monitor.width;
        this._placeEdge(this._left, monitor.x, area.y, area.y + area.height);
        this._placeEdge(this._right, screenRight - EDGE_WIDTH, area.y, area.y + area.height);
        this._placeEdge(this._windowLeft, x, y, bottom, x - monitor.x >= EDGE_WIDTH);
        this._placeEdge(this._windowRight, right - EDGE_WIDTH, y, bottom,
            screenRight - right >= EDGE_WIDTH);
        this._syncFocusShadow();
        for (const target of [this._bottom, this._left, this._right, this._pill])
            target.show();
        if (this._chooser)
            this._positionChooser();
    }

    _placeEdge(actor, x, top, bottom, visible = true) {
        actor.set_position(x, top + BOTTOM_HEIGHT);
        actor.set_size(EDGE_WIDTH, bottom - top - 2 * BOTTOM_HEIGHT);
        actor.visible = visible;
    }

    _syncFocusShadow(exclude = null) {
        const window = this._window;
        if (!this._bounds || !window || window === exclude ||
            window !== global.display.focus_window || window.is_fullscreen() || window.is_maximized()) {
            this._clearFocusShadow();
            return;
        }
        const workspace = global.workspace_manager.get_active_workspace();
        const multipleAndroidWindows = global.display.get_tab_list(Meta.TabList.NORMAL_ALL, workspace)
            .some(other => other !== window && other !== exclude &&
                other.get_window_type() === Meta.WindowType.NORMAL && !other.get_transient_for() &&
                this._isAndroidWindow(other));
        const actor = window.get_compositor_private();
        if (!multipleAndroidWindows || !actor) {
            this._clearFocusShadow();
            return;
        }
        if (!this._focusShadow) {
            this._focusShadow = new Clutter.Actor({
                layout_manager: new Clutter.FixedLayout(), reactive: false,
            });
            // Separate clipped strips leave the entire application interior unpainted.
            for (const side of ['top', 'bottom', 'left', 'right']) {
                this._focusShadow.add_child(new St.Widget({
                    style_class: `tb-android-focus-shadow ${side}`,
                    reactive: false, clip_to_allocation: true,
                }));
            }
            this._focusShadow.connect('destroy', () => this._focusShadow = null);
            actor.add_child(this._focusShadow);
        }
        // Attach to the window actor so the ring shares its stacking and animations.
        const rect = window.get_frame_rect();
        const buffer = window.get_buffer_rect();
        const transform = actor.get_child_transform();
        const sx = transform.get_x_scale() || 1;
        const sy = transform.get_y_scale() || 1;
        this._focusShadow.set_scale(1 / sx, 1 / sy);
        this._focusShadow.set_position((rect.x - buffer.x - FOCUS_OUTSET) / sx,
            (rect.y - buffer.y - FOCUS_OUTSET) / sy);
        const t = FOCUS_OUTSET;
        const width = rect.width + 2 * t;
        this._focusShadow.set_size(width, rect.height + 2 * t);
        const [top, bottom, left, right] = this._focusShadow.get_children();
        top.set_size(width, t);
        bottom.set_position(0, t + rect.height);
        bottom.set_size(width, t);
        left.set_position(0, t);
        left.set_size(t, rect.height);
        right.set_position(t + rect.width, t);
        right.set_size(t, rect.height);
    }

    _clearFocusShadow() {
        this._focusShadow?.destroy();
        this._focusShadow = null;
    }

    _hide() {
        this._bounds = null;
        this._clearGesture(false);
        this._clearFocusShadow();
        for (const target of [this._bottom, this._left, this._right,
            this._windowLeft, this._windowRight, this._pill])
            target.hide();
        this._closeChooser();
    }

    _clearGesture(animate = true) {
        const gesture = this._gesture;
        this._gesture = null;
        gesture?.grab.dismiss();
        this._pill.remove_all_transitions();
        if (animate && gesture?.edge === 'bottom') {
            this._pill.ease({translation_y: 0, scale_x: 1, duration: 140,
                mode: Clutter.AnimationMode.EASE_OUT_QUAD});
        } else {
            this._pill.translation_y = 0;
            this._pill.scale_x = 1;
        }
        if (!this._backPreview.visible)
            return;
        this._backPreview.remove_all_transitions();
        if (animate) {
            this._backPreview.ease({opacity: 0, duration: 130,
                mode: Clutter.AnimationMode.EASE_OUT_QUAD, onComplete: () => this._backPreview.hide()});
        } else {
            this._backPreview.hide();
        }
    }

    _updateFeedback(gesture, travel) {
        if (gesture.edge === 'bottom') {
            const progress = Math.min(1, travel / 75);
            this._pill.translation_y = -17 * progress;
            this._pill.scale_x = 1 + 0.25 * progress;
            return;
        }
        if (travel < 4) {
            this._backPreview.hide();
            return;
        }
        const [edgeX, edgeY] = gesture.target.get_position();
        const edgeBottom = edgeY + gesture.target.height;
        const fromLeft = gesture.edge === 'left';
        const progress = Math.min(1, travel / MIN_BACK_SWIPE);
        const width = Math.round(20 + 36 * progress);
        this._backPreview.set_width(width);
        this._backPreview.set_position(fromLeft ? edgeX : edgeX + EDGE_WIDTH - width,
            Math.max(edgeY - BOTTOM_HEIGHT, Math.min(edgeBottom + BOTTOM_HEIGHT - BACK_HEIGHT,
                Math.round(gesture.y - BACK_HEIGHT / 2))));
        this._backPreview.set_style_class_name(`tb-android-back-preview ${gesture.edge}`);
        if (progress === 1)
            this._backPreview.add_style_pseudo_class('active');
        else
            this._backPreview.remove_style_pseudo_class('active');
        this._backPreview.opacity = Math.round(130 + 125 * progress);
        this._backPreview.show();
    }

    _onGesture(target, edge, event) {
        const E = Clutter.EventType;
        const type = event.type();
        const slot = event.get_event_sequence()?.get_slot() ?? null;
        const [x, y] = event.get_coords();
        if (type === E.TOUCH_BEGIN || (type === E.BUTTON_PRESS && event.get_button() === 1)) {
            if (!this._gesture && this._bottom.visible && global.display.focus_window === this._window) {
                this._clearGesture(false);
                this._gesture = {target, window: this._window, edge, slot, x, y, lastX: x, lastY: y,
                    lastMove: GLib.get_monotonic_time(), travel: 0, maxDistance: 0,
                    grab: global.stage.grab(target)};
                this._gesture.started = this._gesture.lastMove;
            }
            return Clutter.EVENT_STOP;
        }
        const gesture = this._gesture;
        if (!gesture || gesture.target !== target || gesture.slot !== slot)
            return Clutter.EVENT_PROPAGATE;
        if (type === E.TOUCH_CANCEL) {
            this._clearGesture();
            return Clutter.EVENT_STOP;
        }
        const release = type === E.TOUCH_END ||
            (type === E.BUTTON_RELEASE && event.get_button() === 1);
        if (!release && type !== E.TOUCH_UPDATE && type !== E.MOTION)
            return Clutter.EVENT_PROPAGATE;
        const now = GLib.get_monotonic_time();
        // A touchscreen can send a release without reliable coordinates.
        // Use the last motion sample to decide whether to commit.
        if (!release && Math.hypot(x - gesture.lastX, y - gesture.lastY) >= HOLD_SLOP) {
            gesture.lastX = x;
            gesture.lastY = y;
            gesture.lastMove = now;
        }
        if (!release) {
            const dx = x - gesture.x;
            const up = gesture.y - y;
            const inward = edge === 'left' ? dx : -dx;
            gesture.travel = edge === 'bottom' ?
                (up >= Math.abs(dx) * 0.7 ? up : 0) :
                (inward >= Math.abs(up) * 0.8 ? inward : 0);
            gesture.maxDistance = Math.max(gesture.maxDistance, Math.hypot(dx, up));
            this._updateFeedback(gesture, gesture.travel);
            return Clutter.EVENT_STOP;
        }
        this._clearGesture();
        if (edge === 'bottom') {
            const [pillX, pillY] = this._pill.get_position();
            const onPill = gesture.x >= pillX && gesture.x < pillX + this._pill.width &&
                gesture.y >= pillY - 6 && gesture.y < pillY + PILL_HEIGHT + 6;
            if (gesture.travel >= MIN_UP_SWIPE && now - gesture.lastMove >= HOLD_MS * 1000)
                this._toggleRecents();
            else if (gesture.travel >= MIN_UP_SWIPE ||
                (onPill && gesture.maxDistance < HOLD_SLOP && now - gesture.started < 300000))
                this._minimize();
        } else if (gesture.travel >= MIN_BACK_SWIPE) {
            this._back(gesture.window);
        }
        return Clutter.EVENT_STOP;
    }

    _back(window) {
        if (!this._bottom.visible || this._window !== window || global.display.focus_window !== window)
            return;
        if (this._chooser) {
            this._closeChooser();
            return;
        }
        const time = GLib.get_monotonic_time();
        this._keyboard.notify_key(time, KEY_BACK, Clutter.KeyState.PRESSED);
        this._keyboard.notify_key(time, KEY_BACK, Clutter.KeyState.RELEASED);
    }

    _minimize() {
        const window = this._window;
        if (window && global.display.focus_window === window) {
            this._hide();
            window.minimize();
        }
    }

    _toggleRecents() {
        if (!this._bounds)
            return;
        if (this._chooser) {
            this._closeChooser();
            return;
        }
        const window = this._window;
        this._chooser = new St.BoxLayout({vertical: true, style_class: 'tb-android-nav-chooser',
            reactive: true});
        const list = new St.BoxLayout({vertical: true});
        const scroll = new St.ScrollView({overlay_scrollbars: true, y_expand: true,
            hscrollbar_policy: St.PolicyType.NEVER, vscrollbar_policy: St.PolicyType.AUTOMATIC});
        scroll.set_child(list);
        this._chooser.add_child(scroll);
        const add = (parent, label, action) => {
            const button = new St.Button({label, style_class: 'tb-android-nav-choice', x_expand: true});
            button.connect('clicked', () => {
                this._closeChooser();
                action();
            });
            parent.add_child(button);
        };
        const windows = global.display.get_tab_list(Meta.TabList.NORMAL_ALL, null);
        for (const recent of windows.filter(this._isAndroidWindow)) {
            const app = Shell.WindowTracker.get_default().get_window_app(recent);
            add(list, app?.get_name() ?? recent.get_title() ?? '安卓应用',
                () => recent.activate(global.get_current_time()));
        }
        if (window.is_fullscreen())
            add(this._chooser, '退出全屏', () => window.unmake_fullscreen());
        Main.layoutManager.addTopChrome(this._chooser);
        this._positionChooser();
    }

    _positionChooser() {
        const {x, y, right, bottom} = this._bounds;
        const width = Math.min(320, right - x - 24);
        const height = Math.min(268, bottom - y - BOTTOM_HEIGHT - 24);
        this._chooser.set_size(width, height);
        this._chooser.set_position(x + Math.round((right - x - width) / 2),
            bottom - BOTTOM_HEIGHT - height - 4);
    }

    _closeChooser() {
        if (this._chooser) {
            Main.layoutManager.removeChrome(this._chooser);
            this._chooser.destroy();
            this._chooser = null;
        }
    }

    destroy() {
        for (const [target, id] of this._signals)
            target.disconnect(id);
        this._hide();
        for (const target of [this._bottom, this._left, this._right, this._windowLeft,
            this._windowRight, this._pill, this._backPreview]) {
            Main.layoutManager.removeChrome(target);
            target.destroy();
        }
        this._keyboard = null;
    }
}
