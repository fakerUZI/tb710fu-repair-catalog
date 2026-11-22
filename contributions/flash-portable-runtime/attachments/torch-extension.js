import Gio from 'gi://Gio';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {QuickToggle, SystemIndicator} from 'resource:///org/gnome/shell/ui/quickSettings.js';

const Torch = Gio.DBusProxy.makeProxyWrapper(`<node><interface name="org.tb710fu.Torch1">
<method name="SetEnabled"><arg type="b" direction="in"/></method>
<method name="GetState"><arg type="b" direction="out"/><arg type="u" direction="out"/></method>
<signal name="Changed"><arg type="b"/><arg type="u"/></signal>
</interface></node>`);

export default class TorchExtension extends Extension {
    enable() {
        this._enabled = true;
        this._cancel = new Gio.Cancellable();
        this._indicator = new SystemIndicator();
        this._toggle = new QuickToggle({title: '手电筒', toggleMode: true,
            iconName: 'display-brightness-symbolic'});
        this._toggle.reactive = false;
        this._indicator.quickSettingsItems.push(this._toggle);
        Main.panel.statusArea.quickSettings.addExternalIndicator(this._indicator);
        this._toggle.connect('clicked', () => this._set(this._toggle.checked));
        this._proxy = new Torch(Gio.DBus.system, 'org.tb710fu.Torch1', '/org/tb710fu/Torch1',
            (proxy, error) => {
                if (!this._enabled) return;
                if (error) {console.error(`tb-torch: ${error.message}`); return;}
                this._proxy = proxy;
                this._signal = proxy.connectSignal('Changed', (_proxy, _sender, [enabled]) => {
                    if (this._enabled) this._toggle.checked = enabled;
                });
                this._owner = proxy.connect('notify::g-name-owner', () => this._sync());
                this._sync();
            }, this._cancel);
    }
    _sync() {
        if (!this._enabled) return;
        this._toggle.reactive = !!this._proxy?.g_name_owner;
        if (!this._toggle.reactive) {this._toggle.checked = false; return;}
        this._proxy.GetStateRemote((result, error) => {
            if (!this._enabled) return;
            if (!error) this._toggle.checked = result[0];
        });
    }
    _set(enabled) {
        if (!this._enabled || !this._proxy?.g_name_owner) return;
        this._proxy.SetEnabledRemote(enabled, (_result, error) => {
            if (!this._enabled) return;
            if (error) Main.notify('手电筒', error.message);
            this._sync();
        });
    }
    disable() {
        this._enabled = false;
        this._cancel?.cancel();
        if (this._signal) this._proxy?.disconnectSignal(this._signal);
        if (this._owner) this._proxy?.disconnect(this._owner);
        this._toggle?.destroy();
        this._indicator?.destroy();
        this._toggle = this._indicator = this._proxy = null;
        this._signal = this._owner = 0;
    }
}
