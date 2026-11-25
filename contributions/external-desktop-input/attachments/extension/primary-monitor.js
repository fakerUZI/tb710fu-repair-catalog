import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

const SERVICE = 'org.gnome.Mutter.DisplayConfig';
const PATH = '/org/gnome/Mutter/DisplayConfig';

function displayCall(method, parameters) {
    return new Promise((resolve, reject) => {
        Gio.DBus.session.call(SERVICE, PATH, SERVICE, method, parameters, null,
            Gio.DBusCallFlags.NO_AUTO_START, 5000, null, (bus, res) => {
                try {
                    resolve(bus.call_finish(res).deepUnpack());
                } catch (error) {
                    reject(error);
                }
            });
    });
}

// Use the current serial and layout for both directions. Hotplug, a change
// to mirroring, or a newly selected primary must supersede our old request.
export async function switchPrimary(from, to, stillWanted) {
    const [serial, physical, logical, properties] = await displayCall('GetCurrentState', null);
    const singleOutput = (group, name) => group[5].length === 1 && group[5][0][0] === name;
    if (!stillWanted() || !logical.some(g => g[4] && singleOutput(g, from)) ||
        !logical.some(g => singleOutput(g, to)))
        return false;

    const outputs = new Map();
    const leased = [];
    for (const [spec, modes, props] of physical) {
        if (props['is-for-lease']?.get_boolean())
            leased.push(spec);
        const current = modes.find(mode => mode.at(-1)['is-current']?.get_boolean());
        if (!current)
            continue;
        const options = {};
        for (const [read, write] of [['is-underscanning', 'underscanning'],
            ['color-mode', 'color-mode'], ['rgb-range', 'rgb-range']]) {
            if (props[read])
                options[write] = props[read];
        }
        outputs.set(spec[0], [spec[0], current[0], options]);
    }
    const config = logical.map(([x, y, scale, transform, , specs]) =>
        [x, y, scale, transform, specs[0][0] === to, specs.map(([name]) => outputs.get(name))]);
    const options = {};
    if (properties['supports-changing-layout-mode']?.get_boolean())
        options['layout-mode'] = properties['layout-mode'];
    if (leased.length)
        options['monitors-for-lease'] = new GLib.Variant('a(ssss)', leased);
    await displayCall('ApplyMonitorsConfig',
        new GLib.Variant('(uua(iiduba(ssa{sv}))a{sv})', [serial, 1, config, options]));
    return true;
}
