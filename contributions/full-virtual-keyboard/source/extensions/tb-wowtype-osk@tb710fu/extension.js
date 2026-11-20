import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

export default class WowTypeOsk extends Extension {
    enable() {
        this._disabled = false;
        this._deleteQueue = Promise.resolve();
        this._deleteTimer = 0;
        this._engineDeleteActive = false;
        this._fcitxAvailable = false;
        this._watch = Gio.bus_watch_name(Gio.BusType.SESSION, 'org.fcitx.Fcitx5',
            Gio.BusNameWatcherFlags.NONE,
            () => { this._fcitxAvailable = true; },
            () => { this._fcitxAvailable = false; });
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
        this._originalCommit = controller.commit;
        this._originalToggleDelete = controller.toggleDelete;
        const extension = this;
        controller.toggleDelete = function (enabled) {
            if (extension._engineDeleteActive &&
                !extension._deleteFocusValid(extension._engineDeleteRequest))
                extension._stopEngineDelete();
            if (extension._engineDeleteActive ||
                (enabled && extension._canDeletePreedit())) {
                extension._toggleEngineDelete(enabled);
                return;
            }
            return extension._originalToggleDelete.call(this, enabled);
        };
        controller.commit = async function (text, modifiers) {
            const method = Main.inputMethod;
            const purpose = method.content_purpose;
            if (!extension._fcitxAvailable || !method.currentFocus || !method._context ||
                Main.actionMode === Shell.ActionMode.LOCK_SCREEN ||
                purpose === Clutter.InputContentPurpose.PASSWORD ||
                purpose === Clutter.InputContentPurpose.PIN || modifiers?.size > 0 ||
                !/^[\x20-\x7e]+$/.test(text))
                return extension._originalCommit.call(this, text, modifiers);
            // XKB layouts normally commit text directly, bypassing the engine.
            // The IBus compatibility frontend is already the Shell's IM context.
            for (const char of text) {
                const handled = await method.handleVirtualKey(char.codePointAt(0));
                if (!handled)
                    await extension._originalCommit.call(this, char, modifiers);
            }
        };
    }
    _canDeletePreedit() {
        const method = Main.inputMethod;
        const purpose = method.content_purpose;
        const shortcuts = Main.extensionManager.lookup('tb-osk-shortcuts@tb710fu')?.stateObj;
        return !this._disabled && this._fcitxAvailable && !!method.currentFocus &&
            !!method._context && Main.actionMode !== Shell.ActionMode.LOCK_SCREEN &&
            purpose !== Clutter.InputContentPurpose.PASSWORD &&
            purpose !== Clutter.InputContentPurpose.PIN &&
            !Main.keyboard.keyboardActor?._modifiers?.size &&
            (!!shortcuts?._compositionText || method.hasPreedit());
    }
    _deleteFocusValid(request) {
        return !this._disabled && this._fcitxAvailable &&
            this._controller === request.controller && Main.keyboard.visible &&
            Main.inputMethod.currentFocus === request.focus &&
            Main.inputMethod._context === request.context &&
            Main.inputMethod.content_purpose === request.purpose &&
            Main.actionMode !== Shell.ActionMode.LOCK_SCREEN &&
            global.display.focus_window === request.window;
    }
    _toggleEngineDelete(enabled) {
        if (!enabled) {
            this._stopEngineDelete();
            return;
        }
        if (this._engineDeleteActive)
            return;
        const request = {controller: this._controller,
            focus: Main.inputMethod.currentFocus, context: Main.inputMethod._context,
            purpose: Main.inputMethod.content_purpose,
            window: global.display.focus_window};
        this._engineDeleteRequest = request;
        this._engineDeleteActive = true;
        // A short tap enables and disables synchronously. Its first delete must
        // finish even after release; only subsequent repeats are cancelled.
        this._queueDelete(request, false).then(() => this._scheduleDelete(request, 300));
    }
    _queueDelete(request, repeating) {
        this._deleteQueue = this._deleteQueue.then(async () => {
            if (!this._deleteFocusValid(request) ||
                (repeating && (!this._engineDeleteActive || this._engineDeleteRequest !== request)))
                return;
            const handled = await Main.inputMethod.handleVirtualKey(Clutter.KEY_BackSpace);
            if (!handled && this._deleteFocusValid(request)) {
                // Once composition is empty, a held key may continue deleting
                // application text. Keep a balanced native press/release pair.
                try {
                    request.controller.keyvalPress(Clutter.KEY_BackSpace);
                } finally {
                    request.controller.keyvalRelease(Clutter.KEY_BackSpace);
                }
            }
        }).catch(error => {
            this._stopEngineDelete();
            console.error(error);
        });
        return this._deleteQueue;
    }
    _scheduleDelete(request, delay) {
        if (!this._engineDeleteActive || this._engineDeleteRequest !== request)
            return;
        if (!this._deleteFocusValid(request)) {
            this._stopEngineDelete();
            return;
        }
        this._deleteTimer = GLib.timeout_add_once(GLib.PRIORITY_DEFAULT, delay, () => {
            this._deleteTimer = 0;
            this._queueDelete(request, true).then(() => this._scheduleDelete(request, 65));
        });
    }
    _stopEngineDelete() {
        this._engineDeleteActive = false;
        if (this._deleteTimer)
            GLib.source_remove(this._deleteTimer);
        this._deleteTimer = 0;
    }
    _restore() {
        this._stopEngineDelete();
        if (this._controller && this._originalCommit)
            this._controller.commit = this._originalCommit;
        if (this._controller && this._originalToggleDelete)
            this._controller.toggleDelete = this._originalToggleDelete;
        this._controller = null;
        this._originalCommit = null;
        this._originalToggleDelete = null;
    }
    disable() {
        this._disabled = true;
        if (this._poll)
            GLib.source_remove(this._poll);
        if (this._watch)
            Gio.bus_unwatch_name(this._watch);
        this._restore();
    }
}
