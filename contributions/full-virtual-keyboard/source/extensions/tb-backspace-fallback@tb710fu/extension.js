import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

export default class BackspaceFallback extends Extension {
    enable() {
        this._attach();
        this._poll = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 500, () => {
            this._attach();
            return GLib.SOURCE_CONTINUE;
        });
    }
    _attach() {
        const controller = Main.keyboard.keyboardActor?._keyboardController;
        if (!controller || controller === this._controller)
            return;
        this._restore();
        this._controller = controller;
        this._original = controller.toggleDelete;
        const ext = this;
        controller.toggleDelete = function (enabled) {
            if (ext._pressed) {
                if (!enabled) {
                    this.keyvalRelease(Clutter.KEY_BackSpace);
                    ext._pressed = false;
                }
                return;
            }
            const method = Main.inputMethod;
            const shortcuts = Main.extensionManager.lookup('tb-osk-shortcuts@tb710fu')?.stateObj;
            const purpose = method.content_purpose;
            if (enabled && method.currentFocus && !method.hasPreedit() &&
                !shortcuts?._compositionText &&
                Main.actionMode !== Shell.ActionMode.LOCK_SCREEN &&
                purpose !== Clutter.InputContentPurpose.PASSWORD &&
                purpose !== Clutter.InputContentPurpose.PIN) {
                this.keyvalPress(Clutter.KEY_BackSpace);
                ext._pressed = true;
                return;
            }
            return ext._original.call(this, enabled);
        };
    }
    _restore() {
        if (this._pressed && this._controller)
            this._controller.keyvalRelease(Clutter.KEY_BackSpace);
        this._pressed = false;
        if (this._controller && this._original && this._controller.toggleDelete)
            this._controller.toggleDelete = this._original;
        this._controller = null;
        this._original = null;
    }
    disable() {
        if (this._poll)
            GLib.source_remove(this._poll);
        this._restore();
    }
}
