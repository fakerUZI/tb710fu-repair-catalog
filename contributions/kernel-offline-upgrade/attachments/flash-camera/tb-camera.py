#!/usr/bin/python3
"""TB710FU front/rear camera UI, versioned profiles and safe JPEG capture."""
import datetime
import math
import os
import statistics
from pathlib import Path
import sys
import tempfile
import time

CAMERA = '/base/soc@0/cci@ac15000/i2c-bus@1/camera@36'
FULL = (4200, 3120)
PROFILES = {
    'rear': {'name': '后摄', 'path': CAMERA, 'full': FULL,
             'previews': [(2560,1920), (1920,1440), FULL], 'focus': True},
    'macro': {'name': '后摄微距', 'path': '/base/soc@0/cci@ac16000/i2c-bus@0/camera@37',
              'full': (1592,1200), 'previews': [(1280,960),(1592,1200)], 'focus': False},
    'front': {'name': '前摄', 'path': '/base/soc@0/cci@ac15000/i2c-bus@0/camera@39',
              'full': (4200,3120), 'previews': [(1280,720), (1920,1080), (4200,3120)], 'focus': False},
}


def profile(name):
    if name not in PROFILES:
        raise ValueError('Unknown camera profile')
    return PROFILES[name]


def available_cameras():
    keys = ['rear','front']
    if Path('/sys/firmware/devicetree/base/soc@0/cci@ac16000/i2c-bus@0/camera@37').exists():
        keys.append('macro')
    return keys


def preview_labels(name):
    return [f'{width}×{height}' for width, height in profile(name)['previews']]

PHOTO_WARMUP_FRAMES = 12
FOCUS = Path('/sys/module/tb_focus_manual/parameters/position')



def focus_metric(data, width, height, stride, center=(.5, .5)):
    """Sparse native-pixel gradients in a central/tapped ROI; no image storage.

    Normalize for brightness, reject clipped/dark/textureless frames. Sampling
    neighboring native pixels (rather than resizing) keeps defocus measurable.
    """
    cx = max(.15, min(.85, center[0])) * width
    cy = max(.15, min(.85, center[1])) * height
    rw, rh = max(8, int(width*.28)), max(8, int(height*.28))
    x0, y0 = max(2, int(cx-rw/2)), max(2, int(cy-rh/2))
    x1, y1 = min(width-3, x0+rw), min(height-3, y0+rh)
    step = max(1, (x1-x0)//80)
    count = total = squares = edges = clipped = 0
    def luma(x, y):
        i = y*stride+x*4
        return (int(data[i])+2*int(data[i+1])+int(data[i+2]))/4
    for y in range(y0, y1, step):
        for x in range(x0, x1, step):
            v = luma(x,y)
            gx = luma(x+2,y)-luma(x-2,y)
            gy = luma(x,y+2)-luma(x,y-2)
            total += v; squares += v*v; edges += gx*gx+gy*gy
            clipped += v < 3 or v > 252
            count += 1
    if not count:
        return None
    mean = total/count
    variance = max(0., squares/count-mean*mean)
    if mean < 5 or mean > 245 or variance < 4 or clipped/count > .7:
        return None
    return edges/count/max(16.,mean*mean)


class ContrastFocus:
    """Bounded single AF sweep using the existing ramped GT9772B interface.

    Ignore queued frames after every move, wait 200 ms, median three frames,
    coarse/fine search, then confirm the peak. Flat/unstable scenes restore the
    original position. No continuous sweep and no writes outside 0..1023.
    """
    def __init__(self, initial, write, done, now=None):
        self.initial = int(initial)
        if not 0 <= self.initial <= 1023:
            raise ValueError('Invalid initial lens position')
        self.write, self.done = write, done
        self.records = []
        self.phase = 'baseline'
        self.pending = []
        self.active = True
        self.deadline = (time.monotonic() if now is None else now)+24
        self.position = self.initial
        self._wait(time.monotonic() if now is None else now)

    def _wait(self, now):
        self.ready_at = now+.2
        self.skip = 3
        self.samples = []

    def move(self, position, now):
        self.position = int(position)
        self.write(self.position)
        self._wait(now)

    def cancel(self, reason='对焦已取消'):
        if self.active:
            try:
                self.write(self.initial)
                position = self.initial
            except OSError:
                position = self.position
                reason = '对焦接口故障，恢复焦点未确认'
            self.active = False
            self.done(False, position, reason, self.records)

    def frame(self, score, now=None):
        if not self.active:
            return
        now = time.monotonic() if now is None else now
        if now > self.deadline:
            self.cancel('对焦超时，已恢复原焦点'); return
        if self.skip:
            self.skip -= 1; return
        if now < self.ready_at:
            return
        self.samples.append(score)
        if len(self.samples) < 3:
            return
        good = [v for v in self.samples if v is not None and math.isfinite(v)]
        if len(good) != 3:
            self.cancel('光线或纹理不足，已恢复原焦点'); return
        score = statistics.median(good)
        if self.phase == 'verify':
            # A moving scene or changing exposure must not select a false peak.
            if score < self.peak*.72 or score > self.peak*1.4:
                self.cancel('画面变化较大，已恢复原焦点'); return
            self.active = False
            self.done(True, self.position, '自动对焦完成', self.records)
            return
        self.records.append((self.position,score))
        if self.phase == 'baseline':
            self.baseline = score
            self.phase = 'coarse'
            self.pending = [v for v in list(range(0,1024,64))+[1023] if v != self.initial]
        elif not self.pending and self.phase == 'coarse':
            best, _ = max(self.records, key=lambda v:v[1])
            self.phase = 'fine'
            self.pending = [v for v in range(max(0,best-64),min(1023,best+64)+1,16)
                            if v not in {p for p,_ in self.records}]
        if not self.pending and self.phase == 'fine':
            best,self.peak = max(self.records,key=lambda v:v[1])
            floor = min(v for _,v in self.records)
            if self.peak < .0005 or self.peak < max(.00001,floor)*1.12:
                self.cancel('焦点变化不明显，已保留原焦点'); return
            self.phase = 'verify'
            self.move(best,now)
        elif self.pending:
            self.move(self.pending.pop(0),now)

def crop_bounds(width, height, zoom, center=(.5, .5)):
    zoom = max(1., min(4., float(zoom)))
    w = max(2, int(width / zoom) // 2 * 2)
    h = max(2, int(height / zoom) // 2 * 2)
    x = int(max(0, min(width-w, center[0]*width-w/2))) // 2 * 2
    y = int(max(0, min(height-h, center[1]*height-h/2))) // 2 * 2
    return x, y, w, h


def jpeg_dimensions(data):
    position = 2
    while position + 8 < len(data) and data[position] == 255:
        marker = data[position + 1]
        length = int.from_bytes(data[position+2:position+4], 'big')
        if marker in (192, 193, 194):
            return (int.from_bytes(data[position+7:position+9], 'big'),
                    int.from_bytes(data[position+5:position+7], 'big'))
        position += 2 + length
    raise ValueError('JPEG dimensions unavailable')


def jpeg_has_image(data):
    """Reject the all-zero startup frame, not merely malformed JPEG files."""
    import gi
    gi.require_version('GdkPixbuf', '2.0')
    from gi.repository import GdkPixbuf
    loader = GdkPixbuf.PixbufLoader.new_with_type('jpeg')
    loader.set_size(96, 96)
    loader.write(data)
    loader.close()
    image = loader.get_pixbuf()
    if image is None:
        return False
    pixels = image.get_pixels()
    stride = image.get_rowstride()
    row_bytes = image.get_width() * image.get_n_channels()
    return any(any(pixels[y*stride:y*stride+row_bytes]) for y in range(image.get_height()))


def pull_ready_photo(Gst, pipeline, timeout_seconds=12):
    sink = pipeline.get_by_name('jpeg')
    deadline = time.monotonic() + timeout_seconds
    frames = 0
    while time.monotonic() < deadline:
        sample = sink.emit('try-pull-sample', min(Gst.SECOND, int(max(0, deadline-time.monotonic())*Gst.SECOND)))
        if sample is None:
            continue
        frames += 1
        buffer = sample.get_buffer()
        data = buffer.extract_dup(0, buffer.get_size())
        sample = None
        if frames >= PHOTO_WARMUP_FRAMES and jpeg_has_image(data):
            return data
    raise RuntimeError('相机画面尚未就绪，未保存空白照片')


def save_photo(destination, data, dimensions):
    if data[:2] != b'\xff\xd8' or data[-2:] != b'\xff\xd9' or jpeg_dimensions(data) != dimensions:
        raise ValueError('拍照数据不完整，未保存照片')
    fd, temporary = tempfile.mkstemp(prefix='.tb-photo-', suffix='.tmp', dir=destination.parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(data)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def make_photo(Gst, destination, zoom=1., center=(.5, .5), camera='rear'):
    selected = profile(camera)
    full = selected['full']
    x, y, w, h = crop_bounds(*full, zoom, center)
    pipeline = Gst.parse_launch(
        f'libcamerasrc name=camera ! video/x-raw,format=RGBA,width={full[0]},height={full[1]} '
        '! videocrop name=crop ! videoconvert '
        '! jpegenc quality=98 ! appsink name=jpeg sync=false max-buffers=1 drop=true')
    source = pipeline.get_by_name('camera')
    source.set_property('camera-name', selected['path'])
    pipeline.get_by_name('crop').set_properties(left=x, right=full[0]-x-w,
                                               top=y, bottom=full[1]-y-h)
    return pipeline, (w, h)


def load_gi():
    import gi
    gi.require_version('Gtk', '4.0')
    gi.require_version('Gdk', '4.0')
    gi.require_version('Graphene', '1.0')
    gi.require_version('Gst', '1.0')
    from gi.repository import Gtk, Gdk, Graphene, Gst, GLib, Gio
    Gst.init(None)
    return Gtk, Gdk, Graphene, Gst, GLib, Gio


def smoke_test():
    _, _, _, Gst, _, _ = load_gi()
    preview = Gst.parse_launch('libcamerasrc name=camera ! video/x-raw,format=RGBA,width=2560,height=1920 ! appsink name=view sync=false max-buffers=1 drop=true')
    preview.get_by_name('camera').set_property('camera-name', CAMERA)
    try:
        preview.set_state(Gst.State.PLAYING)
        sample = preview.get_by_name('view').emit('try-pull-sample', 8*Gst.SECOND)
        if not sample:
            raise RuntimeError('No high-resolution preview frame')
        caps = sample.get_caps().get_structure(0)
        print('PREVIEW', caps.get_value('width'), caps.get_value('height'), caps.get_value('format'), flush=True)
    finally:
        preview.set_state(Gst.State.NULL)
        preview.get_state(3*Gst.SECOND)
        sample = None
        preview = None
    with tempfile.TemporaryDirectory(prefix='tb-camera-test-') as folder:
        for zoom in (1., 2.):
            photo, dimensions = make_photo(Gst, Path(folder)/'test.jpg', zoom)
            try:
                photo.set_state(Gst.State.PLAYING)
                data = pull_ready_photo(Gst, photo)
                save_photo(Path(folder)/'test.jpg', data, dimensions)
            finally:
                photo.set_state(Gst.State.NULL)
                photo.get_state(3*Gst.SECOND)
            data = (Path(folder)/'test.jpg').read_bytes()
            assert data[:2] == b'\xff\xd8' and data[-2:] == b'\xff\xd9'
            assert jpeg_dimensions(data) == dimensions
            assert jpeg_has_image(data), 'Captured JPEG contains only startup zeros'
            print('PHOTO', zoom, dimensions, 'JPEG_BYTES', len(data), flush=True)
    print('CAMERA_SMOKE_TEST_PASSED', flush=True)


def run_app(test=False, camera='rear', switch_test=False):
    Gtk, Gdk, Graphene, Gst, GLib, Gio = load_gi()

    import gi
    gi.require_version('GdkPixbuf','2.0')
    from gi.repository import GdkPixbuf
    def camera_icon(name,size=28,color='#ffffff'):
        paths={
            'close':'<path d="M15 5l-7 7 7 7"/>',
            'flash':'<path d="M13 2L5 14h6l-1 8 9-13h-6z"/>',
            'timer':'<circle cx="12" cy="14" r="8"/><path d="M9 2h6m-3 4v-4m0 8v5l3 2M18 6l2-2"/>',
            'grid':'<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 3v18M15 3v18M3 9h18M3 15h18"/>',
            'flip':'<path d="M5 7H3V3m0 4a10 10 0 0 1 17-1M19 17h2v4m0-4a10 10 0 0 1-17 1"/><rect x="7" y="8" width="10" height="8" rx="2"/><circle cx="12" cy="12" r="2"/>',
            'gallery':'<rect x="3" y="3" width="18" height="18" rx="5"/><circle cx="9" cy="9" r="1.5"/><path d="M4 17l5-5 4 4 3-3 5 5"/>',
            'settings':'<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
        }
        svg=('<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24"><g fill="none" stroke="'+color+'" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'+paths[name]+'</g></svg>').encode()
        loader=GdkPixbuf.PixbufLoader.new_with_type('svg');loader.write(svg);loader.close()
        image=Gtk.Image.new_from_paintable(Gdk.Texture.new_for_pixbuf(loader.get_pixbuf()));image.set_pixel_size(size);return image
    css=Gtk.CssProvider()
    css.load_from_data(b"""
    window.camera-window { background:#000; color:#fff; }
    .camera-window button { background-image:none; box-shadow:none; text-shadow:none; background:transparent; border:none; color:#fff; }
    .camera-window .icon-button { min-width:48px; min-height:48px; padding:4px; border-radius:28px; }
    .camera-window .icon-button:hover { background:rgba(255,255,255,.10); }
    .camera-window .selected { color:#e8bc05; }
    .tool-rail { padding:10px 6px; background:#000; }
    .mode-rail { padding:8px 0; background:#000; }
    .camera-window .mode-button { font-size:16px; font-weight:500; padding:14px 4px; min-height:24px; }
    .camera-window .mode-button.selected { color:#e8bc05; font-weight:700; }
    .capture-rail { margin:0 8px 0 2px; background:#000; }
    .camera-window .shutter { min-width:84px; min-height:84px; padding:3px; border:2px solid #fff; border-radius:999px; }
    .shutter-inner { min-width:68px; min-height:68px; border-radius:999px; background:#fff; }
    .camera-window .shutter:active .shutter-inner { background:#ddd; }
    .capture-spinner { color:#e8bc05; }
    .camera-window .gallery-button { min-width:56px; min-height:56px; border:2px solid #666; border-radius:999px; padding:0; }
    .gallery-button picture { border-radius:999px; }
    .viewfinder { background:#000; }
    .camera-window .zoom-chip { background:rgba(20,20,20,.75); color:#e8bc05; border-radius:999px; min-width:44px; min-height:44px; padding:0; font-size:14px; font-weight:600; }
    .camera-name { color:rgba(255,255,255,.65); font-size:12px; }
    .camera-toast { color:#fff; background:rgba(0,0,0,.50); border-radius:16px; padding:6px 12px; font-size:12px; }
    .countdown { color:#fff; font-size:88px; font-weight:300; text-shadow:0 2px 4px #000; }
    .focus-ring { border:2px solid #e8bc05; border-radius:5px; }
    .grid-right { border-right:1px solid rgba(255,255,255,.35); }
    .grid-bottom { border-bottom:1px solid rgba(255,255,255,.35); }
    .camera-settings contents { background:#191919; color:#eee; border:1px solid #333; border-radius:20px; }
    .camera-settings button { color:#eee; }
    .camera-settings dropdown { color:#eee; }
    .camera-settings scale highlight { background:#e8bc05; }
    .camera-settings checkbutton check:checked { background:#e8bc05; border-color:#e8bc05; color:#111; }
    .settings-title { font-size:20px; font-weight:600; }
    .muted { color:#aaa; font-size:12px; }
    """)
    Gtk.init()
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(),css,Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    class View(Gtk.Widget):
        def __init__(self):
            super().__init__(hexpand=True, vexpand=True)
            self.texture = None
            self.zoom = 1.
            self.center = [.5, .5]

        def do_measure(self, orientation, size):
            return (160, 800 if orientation == Gtk.Orientation.HORIZONTAL else 540, -1, -1)

        def do_snapshot(self, snapshot):
            if not self.texture:
                return
            ww, wh = self.get_width(), self.get_height()
            tw, th = self.texture.get_width(), self.texture.get_height()
            scale = min(ww/tw, wh/th)
            dw, dh = tw*scale, th*scale
            rect = Graphene.Rect().init((ww-dw)/2, (wh-dh)/2, dw, dh)
            snapshot.push_clip(rect)
            z = self.zoom
            cx, cy = self.center
            cx = max(.5/z, min(1-.5/z, cx))
            cy = max(.5/z, min(1-.5/z, cy))
            point = Graphene.Point().init(ww/2-cx*dw*z, wh/2-cy*dh*z)
            snapshot.translate(point)
            snapshot.scale(scale*z, scale*z)
            snapshot.append_texture(self.texture, Graphene.Rect().init(0, 0, tw, th))
            snapshot.pop()

    class Window(Gtk.ApplicationWindow):
        def __init__(self, app):
            super().__init__(application=app, title='相机', default_width=1000, default_height=850)
            self.camera_key = app.initial_camera
            self.changing_camera = False
            self.preview = None
            self.photo = None
            self.photo_path = None
            self.photo_timeout = None
            self.photo_poll = None
            self.photo_frames = 0
            self.last_photo_name = None
            self.capture_folder = None
            self.busy = False
            self.closing = False
            self.zoom_start = 1.
            self.drag_start = [.5, .5]
            self.focus_timer = None
            self.af = None
            self.af_pending_photo = False
            self.af_roi = (.5,.5)
            self.af_auto_started = False
            self.af_watchdog = None
            self.af_sync_slider = False
            self.preview_frames = 0
            self.preview_retries = 0
            self.preview_last_frame = time.monotonic()
            self.preview_timer = None
            self.frames = 0
            self.timer_seconds=0
            self.countdown_source=None
            self.countdown_deadline=0
            self.focus_ring_source=None
            self.last_rear='rear'
            self.gallery_path=None
            self.torch_owned=False
            self.torch_enabled=False
            self.torch_proxy=None
            self.portrait=None
            self.set_decorated(False)
            self.set_default_size(1280,900)
            self.add_css_class('camera-window')
            self.camera_keys=available_cameras()
            self.camera_selector=Gtk.DropDown.new_from_strings([profile(k)['name'] for k in self.camera_keys])
            self.camera_selector.set_selected(self.camera_keys.index(self.camera_key))
            self.camera_selector.connect('notify::selected',self.camera_changed)
            self.shell=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            self.set_child(self.shell)
            self.left_rail=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=8)
            self.left_rail.set_hexpand(False);self.left_rail.set_vexpand(False)
            self.left_rail.add_css_class('tool-rail')
            self.close_button=self.icon_button('close','关闭相机',lambda *_:self.close())
            self.left_rail.append(self.close_button)
            self.tools=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=14,vexpand=True,hexpand=True,halign=Gtk.Align.CENTER,valign=Gtk.Align.CENTER)
            self.torch_button=self.icon_button('flash','常亮补光',self.toggle_torch)
            self.timer_button=self.icon_button('timer','定时拍摄：关闭',self.cycle_timer)
            self.grid_button=self.icon_button('grid','九宫格',self.toggle_grid)
            self.tools.append(self.torch_button);self.tools.append(self.timer_button);self.tools.append(self.grid_button)
            self.settings_button=Gtk.MenuButton()
            self.settings_button.add_css_class('icon-button')
            self.settings_button.set_child(camera_icon('settings'))
            self.settings_button.set_tooltip_text('相机设置')
            self.tools.append(self.settings_button)
            self.left_rail.append(self.tools)
            self.balance=Gtk.Box();self.balance.set_size_request(56,56);self.left_rail.append(self.balance)
            self.shell.append(self.left_rail)
            self.preview_page=Gtk.Overlay(hexpand=True,vexpand=True)
            self.preview_page.add_css_class('viewfinder')
            self.view=View();self.preview_page.set_child(self.view)
            self.shell.append(self.preview_page)
            self.grid=Gtk.Grid(row_homogeneous=True,column_homogeneous=True,can_target=False)
            for row in range(3):
                for col in range(3):
                    cell=Gtk.Box(hexpand=True,vexpand=True,can_target=False)
                    if col<2:cell.add_css_class('grid-right')
                    if row<2:cell.add_css_class('grid-bottom')
                    self.grid.attach(cell,col,row,1,1)
            self.grid.set_visible(False);self.preview_page.add_overlay(self.grid)
            self.focus_layer=Gtk.Fixed(can_target=False)
            self.focus_ring=Gtk.Box(can_target=False);self.focus_ring.set_size_request(62,62);self.focus_ring.add_css_class('focus-ring');self.focus_ring.set_visible(False)
            self.focus_layer.put(self.focus_ring,0,0);self.preview_page.add_overlay(self.focus_layer)
            self.zoom_slider=Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL,1,4,.05)
            self.zoom_slider.connect('value-changed',self.zoom_changed)
            self.zoom_label=Gtk.Label(label='1×')
            self.zoom_button=Gtk.Button(child=self.zoom_label)
            self.zoom_button.add_css_class('zoom-chip');self.zoom_button.set_tooltip_text('切换数字变焦；也可双指缩放')
            self.zoom_button.connect('clicked',lambda *_:self.zoom_slider.set_value(2 if self.view.zoom<1.5 else 4 if self.view.zoom<3 else 1))
            self.zoom_group=Gtk.Box(halign=Gtk.Align.END,valign=Gtk.Align.CENTER,margin_end=18)
            self.zoom_group.append(self.zoom_button);self.preview_page.add_overlay(self.zoom_group)
            self.camera_label=Gtk.Label(label=profile(self.camera_key)['name'],halign=Gtk.Align.END,valign=Gtk.Align.START,margin_top=20,margin_end=20,can_target=False)
            self.camera_label.add_css_class('camera-name');self.preview_page.add_overlay(self.camera_label)
            self.status=Gtk.Label(label='正在打开相机',halign=Gtk.Align.CENTER,valign=Gtk.Align.END,margin_bottom=20,wrap=True,can_target=False)
            self.status.add_css_class('camera-toast');self.preview_page.add_overlay(self.status)
            self.countdown_label=Gtk.Label(label='',halign=Gtk.Align.CENTER,valign=Gtk.Align.CENTER,can_target=False)
            self.countdown_label.add_css_class('countdown');self.countdown_label.set_visible(False);self.preview_page.add_overlay(self.countdown_label)
            self.modes=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=20,halign=Gtk.Align.CENTER,valign=Gtk.Align.CENTER)
            self.modes.set_hexpand(False);self.modes.set_vexpand(False)
            self.modes.add_css_class('mode-rail')
            self.photo_mode=Gtk.Button(label='拍照');self.photo_mode.add_css_class('mode-button');self.photo_mode.connect('clicked',lambda *_:self.select_camera('front' if self.camera_key=='front' else 'rear'))
            self.modes.append(self.photo_mode)
            self.macro_mode=Gtk.Button(label='微距');self.macro_mode.add_css_class('mode-button');self.macro_mode.set_visible('macro' in self.camera_keys);self.macro_mode.connect('clicked',lambda *_:self.select_camera('macro'))
            self.modes.append(self.macro_mode);self.shell.append(self.modes)
            self.capture_controls=Gtk.CenterBox(orientation=Gtk.Orientation.VERTICAL,valign=Gtk.Align.CENTER,halign=Gtk.Align.CENTER)
            self.capture_controls.set_hexpand(False);self.capture_controls.set_vexpand(False)
            self.capture_controls.add_css_class('capture-rail')
            self.flip_button=self.icon_button('flip','切换前后摄像头',self.flip_camera)
            self.capture_controls.set_start_widget(self.flip_button)
            self.shutter=Gtk.Button(halign=Gtk.Align.CENTER,valign=Gtk.Align.CENTER)
            self.shutter.add_css_class('shutter');self.shutter.set_tooltip_text('拍照')
            self.shutter_inner=Gtk.Box(halign=Gtk.Align.CENTER,valign=Gtk.Align.CENTER)
            self.shutter_inner.add_css_class('shutter-inner');self.shutter.set_child(self.shutter_inner)
            self.shutter.connect('clicked',self.request_capture)
            self.shutter_overlay=Gtk.Overlay();self.shutter_overlay.set_child(self.shutter)
            self.capture_spinner=Gtk.Spinner(halign=Gtk.Align.CENTER,valign=Gtk.Align.CENTER,can_target=False)
            self.capture_spinner.set_size_request(42,42);self.capture_spinner.add_css_class('capture-spinner');self.capture_spinner.set_visible(False);self.shutter_overlay.add_overlay(self.capture_spinner)
            self.capture_controls.set_center_widget(self.shutter_overlay)
            self.thumbnail=Gtk.Picture(can_shrink=True,content_fit=Gtk.ContentFit.COVER)
            self.thumbnail.set_size_request(56,56)
            self.gallery_button=Gtk.Button(halign=Gtk.Align.CENTER,valign=Gtk.Align.CENTER)
            self.gallery_button.add_css_class('gallery-button');self.gallery_button.set_overflow(Gtk.Overflow.HIDDEN);self.gallery_button.set_child(camera_icon('gallery',30));self.gallery_button.set_tooltip_text('查看照片');self.gallery_button.connect('clicked',self.open_gallery)
            self.capture_controls.set_end_widget(self.gallery_button)
            self.shell.append(self.capture_controls)
            panel=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=14,margin_top=20,margin_bottom=20,margin_start=20,margin_end=20)
            panel.set_size_request(310,-1)
            title=Gtk.Label(label='相机设置',xalign=0);title.add_css_class('settings-title');panel.append(title)
            panel.append(Gtk.Label(label='预览清晰度',xalign=0))
            self.quality=Gtk.DropDown.new_from_strings(preview_labels(self.camera_key));self.quality.connect('notify::selected',self.quality_changed);panel.append(self.quality)
            self.resolution_label=Gtk.Label(label='保存尺寸 %d×%d'%profile(self.camera_key)['full'],xalign=0);self.resolution_label.add_css_class('muted');panel.append(self.resolution_label)
            self.focus_row=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=12)
            self.auto_focus=Gtk.CheckButton(label='拍照前自动对焦',active=True);self.focus_row.append(self.auto_focus)
            self.focus_row.append(Gtk.Label(label='手动对焦',xalign=0))
            self.focus=Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL,0,1023,1)
            self.focus.set_hexpand(True);self.focus.set_draw_value(False)
            try:self.focus.set_value(int(FOCUS.read_text()))
            except (OSError,ValueError):self.focus.set_value(700)
            self.focus.set_sensitive(os.access(FOCUS,os.W_OK));self.focus.connect('value-changed',self.focus_changed);self.focus_row.append(self.focus)
            self.af_button=Gtk.Button(label='自动对焦');self.af_button.connect('clicked',lambda *_:self.start_af());self.af_button.set_sensitive(os.access(FOCUS,os.W_OK));self.focus_row.append(self.af_button)
            self.focus_row.set_visible(profile(self.camera_key)['focus']);panel.append(self.focus_row)
            self.settings_popover=Gtk.Popover(child=panel);self.settings_popover.add_css_class('camera-settings');self.settings_button.set_popover(self.settings_popover)
            pinch=Gtk.GestureZoom.new();pinch.connect('begin',lambda gesture,seq:setattr(self,'zoom_start',self.view.zoom));pinch.connect('scale-changed',lambda gesture,scale:self.zoom_slider.set_value(self.zoom_start*scale));self.view.add_controller(pinch)
            drag=Gtk.GestureDrag.new();drag.connect('drag-begin',lambda gesture,x,y:setattr(self,'drag_start',list(self.view.center)));drag.connect('drag-update',self.pan);self.view.add_controller(drag);pinch.group(drag)
            tap=Gtk.GestureClick.new();tap.connect('released',self.tap_focus);self.view.add_controller(tap)
            scroll=Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL);scroll.connect('scroll',self.scroll_zoom);self.view.add_controller(scroll)
            keys=Gtk.EventControllerKey.new();keys.connect('key-pressed',self.key_pressed);self.add_controller(keys)
            self.connect('close-request',self.close_camera)
            self.add_tick_callback(self.relayout)
            self.setup_torch()
            GLib.idle_add(self.start_preview)
            GLib.timeout_add_seconds(1,self.preview_health)
            GLib.timeout_add(200,self.sync_chrome)

        def icon_button(self,name,tooltip,callback):
            button=Gtk.Button(child=camera_icon(name));button.add_css_class('icon-button');button.set_tooltip_text(tooltip);button.connect('clicked',callback);return button

        def relayout(self,*_):
            if self.closing:return False
            width,height=self.get_width(),self.get_height()
            portrait=height>width*1.08
            if width>0 and height>0 and portrait!=self.portrait:
                self.portrait=portrait
                self.shell.set_orientation(Gtk.Orientation.VERTICAL if portrait else Gtk.Orientation.HORIZONTAL)
                orientation=Gtk.Orientation.HORIZONTAL if portrait else Gtk.Orientation.VERTICAL
                for box in (self.left_rail,self.tools,self.modes,self.capture_controls):box.set_orientation(orientation)
                self.left_rail.set_size_request(-1,68) if portrait else self.left_rail.set_size_request(76,-1)
                self.modes.set_size_request(-1,52) if portrait else self.modes.set_size_request(62,-1)
                self.capture_controls.set_size_request(max(320,width-60),126) if portrait else self.capture_controls.set_size_request(132,min(560,max(350,height-120)))
                self.capture_controls.set_start_widget(None);self.capture_controls.set_end_widget(None)
                self.capture_controls.set_start_widget(self.gallery_button if portrait else self.flip_button)
                self.capture_controls.set_end_widget(self.flip_button if portrait else self.gallery_button)
                self.zoom_group.set_halign(Gtk.Align.CENTER if portrait else Gtk.Align.END);self.zoom_group.set_valign(Gtk.Align.END if portrait else Gtk.Align.CENTER)
                self.zoom_group.set_margin_bottom(70 if portrait else 0);self.zoom_group.set_margin_end(0 if portrait else 18)
                self.settings_popover.set_position(Gtk.PositionType.BOTTOM if portrait else Gtk.PositionType.RIGHT)
                self.balance.set_visible(not portrait)
                print('CAMERA_LAYOUT','portrait' if portrait else 'landscape',width,height,flush=True)
            texture=self.view.texture
            if texture:
                ww,wh=self.view.get_width(),self.view.get_height();scale=min(ww/texture.get_width(),wh/texture.get_height());x=max(0,round((ww-texture.get_width()*scale)/2));y=max(0,round((wh-texture.get_height()*scale)/2))
                if self.portrait:self.zoom_group.set_margin_bottom(y+18)
                self.grid.set_margin_start(x);self.grid.set_margin_end(x);self.grid.set_margin_top(y);self.grid.set_margin_bottom(y)
            return True

        def sync_chrome(self):
            if self.closing:return False
            waiting=self.busy or self.af_pending_photo or self.countdown_source is not None
            self.capture_spinner.set_visible(self.busy or self.af_pending_photo);self.capture_spinner.set_spinning(self.busy or self.af_pending_photo)
            self.shutter_inner.set_opacity(.3 if self.busy or self.af_pending_photo else 1)
            self.shutter.set_sensitive(not waiting)
            for button in (self.flip_button,self.photo_mode,self.macro_mode):button.set_sensitive(not self.busy)
            self.torch_button.set_sensitive(self.camera_key!='front' and not self.busy and self.torch_proxy is not None)
            self.camera_label.set_text(profile(self.camera_key)['name'])
            for button,active in ((self.photo_mode,self.camera_key!='macro'),(self.macro_mode,self.camera_key=='macro')):
                if active:button.add_css_class('selected')
                else:button.remove_css_class('selected')
            return True

        def key_pressed(self,controller,keyval,keycode,state):
            if keyval==Gdk.KEY_Escape:
                if self.settings_popover.get_visible():self.settings_popover.popdown()
                else:self.close()
                return True
            if keyval==Gdk.KEY_space and not self.settings_popover.get_visible():self.request_capture();return True
            return False

        def toggle_grid(self,*_):
            visible=not self.grid.get_visible();self.grid.set_visible(visible)
            if visible:self.grid_button.add_css_class('selected')
            else:self.grid_button.remove_css_class('selected')

        def cycle_timer(self,*_):
            self.timer_seconds={0:3,3:10,10:0}[self.timer_seconds]
            self.timer_button.set_child(Gtk.Label(label=str(self.timer_seconds)+'s') if self.timer_seconds else camera_icon('timer'))
            self.timer_button.set_tooltip_text('定时拍摄：'+(str(self.timer_seconds)+'秒' if self.timer_seconds else '关闭'))
            if self.timer_seconds:self.timer_button.add_css_class('selected')
            else:self.timer_button.remove_css_class('selected')

        def request_capture(self,*_):
            if self.busy or self.af_pending_photo or self.countdown_source:return
            if not self.timer_seconds:self.capture();return
            self.countdown_deadline=time.monotonic()+self.timer_seconds
            self.countdown_label.set_visible(True);self.countdown_source=GLib.timeout_add(80,self.countdown_tick)
            self.countdown_tick()

        def countdown_tick(self):
            left=math.ceil(self.countdown_deadline-time.monotonic())
            if self.closing:return False
            if left<=0:
                self.countdown_source=None;self.countdown_label.set_visible(False);self.capture();return False
            self.countdown_label.set_text(str(left));return True

        def cancel_countdown(self):
            if self.countdown_source:GLib.source_remove(self.countdown_source);self.countdown_source=None
            self.countdown_label.set_visible(False)

        def flip_camera(self,*_):
            if self.camera_key=='front':self.select_camera(self.last_rear)
            else:self.last_rear=self.camera_key;self.select_camera('front')

        def show_focus_ring(self,x,y):
            if self.focus_ring_source:GLib.source_remove(self.focus_ring_source)
            self.focus_layer.move(self.focus_ring,max(0,x-31),max(0,y-31));self.focus_ring.set_visible(True)
            self.focus_ring_source=GLib.timeout_add(1500,self.hide_focus_ring)

        def hide_focus_ring(self):
            self.focus_ring.set_visible(False);self.focus_ring_source=None;return False

        def setup_torch(self):
            try:
                self.torch_proxy=Gio.DBusProxy.new_for_bus_sync(Gio.BusType.SYSTEM,Gio.DBusProxyFlags.DO_NOT_AUTO_START,None,'org.tb710fu.Torch1','/org/tb710fu/Torch1','org.tb710fu.Torch1',None)
                if not self.torch_proxy.get_name_owner():self.torch_proxy=None;return
                self.torch_proxy.connect('g-signal',self.torch_signal)
                state=self.torch_proxy.call_sync('GetState',None,Gio.DBusCallFlags.NONE,3000,None).unpack();self.update_torch_icon(state[0])
            except GLib.Error:self.torch_proxy=None

        def torch_signal(self,proxy,sender,signal,params):
            if signal=='Changed':self.update_torch_icon(params.unpack()[0])

        def update_torch_icon(self,enabled):
            self.torch_enabled=bool(enabled);self.torch_button.set_child(camera_icon('flash',color='#e8bc05' if enabled else '#ffffff'))
            if enabled:self.torch_button.add_css_class('selected')
            else:self.torch_button.remove_css_class('selected')

        def toggle_torch(self,*_):
            if self.camera_key=='front' or not self.torch_proxy:return
            try:
                enabled=not self.torch_enabled
                self.torch_proxy.call_sync('SetEnabled',GLib.Variant('(b)',(enabled,)),Gio.DBusCallFlags.NONE,3000,None)
                self.torch_owned=enabled;self.update_torch_icon(enabled)
            except GLib.Error as exc:print('TORCH_UI_ERROR',str(exc),flush=True);self.status.set_text('补光灯暂时不可用')

        def release_torch(self):
            if self.torch_owned and self.torch_proxy:
                try:self.torch_proxy.call_sync('SetEnabled',GLib.Variant('(b)',(False,)),Gio.DBusCallFlags.NONE,3000,None)
                except GLib.Error:pass
            self.torch_owned=False

        def open_gallery(self,*_):
            target=self.gallery_path
            if not target or not target.exists():
                target=Path(GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES) or Path.home()/'Pictures')/'Camera';target.mkdir(parents=True,exist_ok=True)
            try:Gio.AppInfo.launch_default_for_uri(target.as_uri(),None)
            except GLib.Error:self.status.set_text('暂时无法打开相册')

        def pan(self, gesture, dx, dy):
            if self.view.zoom <= 1:
                return
            z = self.view.zoom
            self.view.center = [max(.5/z, min(1-.5/z, self.drag_start[0]-dx/max(1,self.view.get_width())/z)),
                                max(.5/z, min(1-.5/z, self.drag_start[1]-dy/max(1,self.view.get_height())/z))]
            self.view.queue_draw()

        def scroll_zoom(self, controller, dx, dy):
            self.zoom_slider.set_value(self.view.zoom-dy*.15)
            return True

        def zoom_changed(self, scale):
            self.view.zoom = scale.get_value()
            if self.view.zoom == 1:
                self.view.center = [.5,.5]
            self.zoom_label.set_text('%.1f×' % self.view.zoom)
            w,h = crop_bounds(*profile(self.camera_key)['full'], self.view.zoom)[2:]
            self.resolution_label.set_text(f'保存尺寸 {w}×{h}'+(' · 数字裁切' if self.view.zoom>1 else ''))
            self.view.queue_draw()

        def quality_changed(self, *_):
            if self.busy or self.changing_camera:
                return
            self.preview_retries = 0
            self.stop_preview()
            self.start_preview()

        def camera_changed(self, *_):
            if self.changing_camera:
                return
            requested = self.camera_keys[self.camera_selector.get_selected()]
            self.select_camera(requested)

        def select_camera(self, requested):
            profile(requested)
            if requested not in self.camera_keys:
                self.status.set_text('当前设备没有接入所选相机')
                return False
            if self.busy:
                self.changing_camera = True
                self.camera_selector.set_selected(self.camera_keys.index(self.camera_key))
                self.changing_camera = False
                self.status.set_text('拍照尚未完成，请稍后切换相机')
                return False
            if requested == self.camera_key:
                return True
            self.cancel_countdown()
            if requested=='front':self.release_torch()
            self.changing_camera = True
            self.stop_preview()
            if self.focus_timer:
                GLib.source_remove(self.focus_timer)
                self.focus_timer = None
            self.camera_key = requested
            self.af_auto_started = False
            self.preview_retries = 0
            self.af_roi = (.5,.5)
            self.camera_selector.set_selected(self.camera_keys.index(requested))
            self.quality.set_model(Gtk.StringList.new(preview_labels(requested)))
            self.quality.set_selected(0)
            self.focus_row.set_visible(profile(requested)['focus'])
            self.resolution_label.set_text('保存尺寸 %d×%d'%profile(requested)['full'])
            self.view.texture = None
            self.view.queue_draw()
            self.zoom_slider.set_value(1)
            self.view.center = [.5, .5]
            self.changing_camera = False
            self.start_preview()
            return True

        def focus_changed(self, scale):
            if self.af_sync_slider or not profile(self.camera_key)['focus']:
                return
            self.cancel_af()
            self.auto_focus.set_active(False)
            if self.focus_timer:
                GLib.source_remove(self.focus_timer)
            self.focus_timer = GLib.timeout_add(180, self.write_focus)

        def write_focus(self):
            self.focus_timer = None
            if not profile(self.camera_key)['focus']:
                return False
            try:
                FOCUS.write_text(str(round(self.focus.get_value())))
            except OSError as exc:
                self.status.set_text('对焦调整失败：'+str(exc))
            return False

        def cancel_af(self):
            self.af_pending_photo = False
            if self.af and self.af.active:
                self.af.cancel()
            self.af = None

        def tap_focus(self, gesture, presses, x, y):
            if self.busy or not profile(self.camera_key)['focus'] or not self.view.texture:
                return
            tw, th = self.view.texture.get_width(), self.view.texture.get_height()
            ww, wh = self.view.get_width(), self.view.get_height()
            scale = min(ww/tw, wh/th)
            dw,dh = tw*scale,th*scale
            if not (abs(x-ww/2)<=dw/2 and abs(y-wh/2)<=dh/2):
                return
            self.show_focus_ring(x,y)
            self.af_roi = (self.view.center[0]+(x-ww/2)/(dw*self.view.zoom),
                           self.view.center[1]+(y-wh/2)/(dh*self.view.zoom))
            self.start_af()

        def start_af(self, photo=False):
            if self.busy or not self.preview or not profile(self.camera_key)['focus']:
                return False
            if self.af and self.af.active:
                self.af_pending_photo |= photo
                return True
            if not os.access(FOCUS,os.W_OK):
                self.status.set_text('对焦接口不可写，请检查相机对焦服务')
                return False
            if self.focus_timer:
                GLib.source_remove(self.focus_timer); self.focus_timer = None
                self.write_focus()
            try:
                self.af_auto_started = True
                initial = int(FOCUS.read_text())
                self.af_pending_photo = photo
                self.af = ContrastFocus(initial,lambda v: FOCUS.write_text(str(v)),self.af_done)
                self.af_watchdog = GLib.timeout_add(250,self.af_check_deadline)
                self.focus.set_sensitive(False)
                self.af_button.set_sensitive(False)
                self.status.set_text('正在自动对焦，请保持画面稳定…')
                return True
            except (OSError,ValueError) as exc:
                self.af_pending_photo = False
                self.status.set_text('自动对焦无法启动：'+str(exc))
                return False

        def af_check_deadline(self):
            if not self.af or not self.af.active:
                self.af_watchdog = None
                return False
            if time.monotonic() > self.af.deadline:
                self.af_watchdog = None
                self.af.cancel('对焦超时，已恢复原焦点')
                return False
            return True

        def af_done(self, ok, position, message, records):
            if self.af_watchdog:
                GLib.source_remove(self.af_watchdog)
                self.af_watchdog = None
            self.af_sync_slider = True
            self.focus.set_value(position)
            self.af_sync_slider = False
            self.focus.set_sensitive(os.access(FOCUS,os.W_OK))
            self.af_button.set_sensitive(os.access(FOCUS,os.W_OK))
            self.status.set_text('对焦完成' if ok else '未找到清晰焦点，可轻触画面重试')
            print('AUTOFOCUS', 'PASS' if ok else 'RESTORED', position, message,
                  'scores', [(p,round(v,6)) for p,v in records],flush=True)
            pending = self.af_pending_photo
            self.af_pending_photo = False
            if pending and not self.closing:
                GLib.idle_add(lambda: (self.capture(focused=True),False)[1])

        def start_preview(self):
            if self.closing or self.busy or self.preview:
                return False
            selected = profile(self.camera_key)
            width, height = selected['previews'][self.quality.get_selected()]
            self.set_title('相机 · '+selected['name'])
            self.preview_frames = 0
            self.preview_last_frame = time.monotonic()
            self.preview = Gst.parse_launch(f'libcamerasrc name=camera ! video/x-raw,format=RGBA,width={width},height={height} ! appsink name=view sync=false max-buffers=1 drop=true')
            source = self.preview.get_by_name('camera')
            source.set_property('camera-name', selected['path'])
            self.sink = self.preview.get_by_name('view')
            self.preview.get_bus().add_signal_watch()
            self.preview.get_bus().connect('message::error', self.preview_error)
            self.preview.set_state(Gst.State.PLAYING)
            self.preview_timer = GLib.timeout_add(66, self.draw_frame)
            self.status.set_text('靠近被摄物拍摄' if self.camera_key=='macro' else '轻触画面对焦' if self.camera_key=='rear' else '前置相机')
            return False

        def stop_preview(self):
            self.cancel_af()
            if self.preview_timer:
                GLib.source_remove(self.preview_timer)
                self.preview_timer = None
            if self.preview:
                self.preview.set_state(Gst.State.NULL)
                self.preview.get_state(3*Gst.SECOND)
                self.preview.get_bus().remove_signal_watch()
                self.preview = None

        def preview_health(self):
            if self.closing:
                return False
            if self.preview and not self.busy and time.monotonic()-self.preview_last_frame > 6:
                self.preview_retries += 1
                print('PREVIEW_RECOVERY',self.camera_key,self.preview_retries,
                      'frames',self.preview_frames,'state',self.preview.get_state(0),flush=True)
                self.stop_preview()
                if self.preview_retries <= 2:
                    self.start_preview()
                    self.status.set_text('预览暂时无画面，正在重新连接相机…')
                else:
                    self.status.set_text('相机没有返回画面，请切换相机或重新打开应用')
            return True

        def draw_frame(self):
            if not self.preview:
                return False
            sample = self.sink.emit('try-pull-sample', 0)
            if sample:
                structure = sample.get_caps().get_structure(0)
                width, height = structure.get_value('width'), structure.get_value('height')
                buffer = sample.get_buffer()
                stride = buffer.get_size()//height
                raw = buffer.extract_dup(0, buffer.get_size())
                self.preview_frames += 1
                self.preview_last_frame = time.monotonic()
                if self.af and self.af.active:
                    try:
                        self.af.frame(focus_metric(raw,width,height,stride,self.af_roi))
                    except OSError as exc:
                        self.cancel_af()
                        self.status.set_text('对焦接口写入失败：'+str(exc))
                elif (not self.af_auto_started and self.preview_frames >= 10
                      and profile(self.camera_key)['focus'] and self.auto_focus.get_active()):
                    self.af_auto_started = True
                    self.start_af()
                data = GLib.Bytes.new(raw)
                self.view.texture = Gdk.MemoryTexture.new(width, height, Gdk.MemoryFormat.R8G8B8A8, data, stride)
                self.frames += 1
                self.view.queue_draw()
            return True

        def preview_error(self, bus, message):
            if not self.preview or bus != self.preview.get_bus():
                return
            error, details = message.parse_error()
            self.status.set_text('相机预览失败：'+error.message)
            print('Preview error:', error.message, details, flush=True)
            GLib.idle_add(self.stop_preview)

        def capture(self, *_, focused=False):
            if self.busy:
                return
            if not focused and profile(self.camera_key)['focus'] and self.auto_focus.get_active():
                if self.start_af(photo=True):
                    return
            self.busy = True
            self.shutter.set_sensitive(False)
            self.quality.set_sensitive(False)
            self.camera_selector.set_sensitive(False)
            self.stop_preview()
            folder = self.capture_folder or Path(GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES) or Path.home()/'Pictures')/'Camera'
            folder.mkdir(parents=True, exist_ok=True)
            self.photo_path = folder/('IMG_'+datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.jpg')
            self.photo, self.photo_dimensions = make_photo(Gst, self.photo_path, self.view.zoom, self.view.center, camera=self.camera_key)
            self.photo_frames = 0
            bus = self.photo.get_bus()
            bus.add_signal_watch()
            bus.connect('message::error', self.photo_error)
            self.photo_timeout = GLib.timeout_add_seconds(15, self.photo_timed_out)
            self.status.set_text('正在拍照，请稍候…')
            self.photo.set_state(Gst.State.PLAYING)
            self.photo_poll = GLib.timeout_add(100, self.poll_photo)

        def poll_photo(self):
            if not self.photo:
                return False
            sample = self.photo.get_by_name('jpeg').emit('try-pull-sample', 0)
            if not sample:
                return True
            buffer = sample.get_buffer()
            data = buffer.extract_dup(0, buffer.get_size())
            sample = None
            self.photo_frames += 1
            if self.photo_frames < PHOTO_WARMUP_FRAMES:
                return True
            try:
                if not jpeg_has_image(data):
                    return True
                save_photo(self.photo_path, data, self.photo_dimensions)
            except (OSError, ValueError, GLib.Error) as exc:
                self.photo_poll = None
                self.finish_photo(failed=True)
                self.status.set_text('保存照片失败：'+str(exc))
                return False
            self.photo_poll = None
            self.photo_done()
            return False

        def photo_done(self, *_):
            name = self.photo_path.name
            self.last_photo_name = name
            self.gallery_path=self.photo_path
            pixbuf=GdkPixbuf.Pixbuf.new_from_file_at_scale(str(self.photo_path),56,56,True)
            self.thumbnail.set_paintable(Gdk.Texture.new_for_pixbuf(pixbuf));self.gallery_button.set_child(self.thumbnail)
            width, height = self.photo_dimensions
            self.finish_photo()
            self.status.set_text('照片已保存')

        def photo_error(self, bus, message):
            error, details = message.parse_error()
            print('Photo error:', error.message, details, flush=True)
            self.finish_photo(failed=True)
            self.status.set_text('拍照失败：'+error.message)

        def photo_timed_out(self):
            self.photo_timeout = None
            self.finish_photo(failed=True)
            self.status.set_text('拍照超时，请重试')
            return False

        def finish_photo(self, failed=False):
            if self.photo_poll:
                GLib.source_remove(self.photo_poll)
                self.photo_poll = None
            if self.photo_timeout:
                GLib.source_remove(self.photo_timeout)
                self.photo_timeout = None
            if self.photo:
                self.photo.set_state(Gst.State.NULL)
                self.photo.get_state(3*Gst.SECOND)
                self.photo.get_bus().remove_signal_watch()
                self.photo = None
            if failed and self.photo_path:
                self.photo_path.unlink(missing_ok=True)
            self.busy = False
            self.shutter.set_sensitive(True)
            self.quality.set_sensitive(True)
            self.camera_selector.set_sensitive(True)
            GLib.timeout_add(250, self.start_preview)

        def close_camera(self, *_):
            self.closing = True
            self.cancel_countdown();self.release_torch()
            if self.focus_ring_source:GLib.source_remove(self.focus_ring_source);self.focus_ring_source=None
            self.stop_preview()
            if self.photo:
                self.finish_photo(failed=True)
            if self.focus_timer:
                GLib.source_remove(self.focus_timer)
            return False

    app = Gtk.Application(application_id='org.tb710fu.RearCamera', flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
    app.add_main_option('camera', 0, GLib.OptionFlags.NONE, GLib.OptionArg.STRING, 'rear or front', 'CAMERA')
    app.initial_camera = camera
    app.test_ok = not (test or switch_test)
    def activate(app):
        window = app.get_active_window()
        if not window:
            window = Window(app)
        window.present()
        if not (test or switch_test):window.fullscreen()
        if test:
            temporary_photos = tempfile.TemporaryDirectory(prefix='tb-camera-ui-check-')
            window.capture_folder = Path(temporary_photos.name)
            window.auto_focus.set_active(False)
            actions = [(1, lambda: window.zoom_slider.set_value(2)),
                       (2, lambda: window.zoom_slider.set_value(4)),
                       (3, lambda: window.zoom_slider.set_value(1)),
                       (4, lambda: window.quality.set_selected(1)),
                       (5, lambda: window.quality.set_selected(0)),
                       (6, lambda: window.capture())]
            for seconds, action in actions:
                GLib.timeout_add_seconds(seconds, lambda action=action: (action(), False)[1])
            def finish_test():
                texture = window.view.texture
                photo_ok = bool(window.last_photo_name) and not window.busy
                if photo_ok:
                    data = (window.capture_folder/window.last_photo_name).read_bytes()
                    photo_ok = jpeg_dimensions(data) == profile(window.camera_key)['full'] and jpeg_has_image(data)
                ok = texture is not None and window.frames >= 4 and window.view.zoom == 1 and photo_ok
                app.test_ok = ok
                print('UI_TEST', 'PASS' if ok else 'FAIL', 'frames', window.frames,
                      'texture', (texture.get_width(), texture.get_height()) if texture else None,
                      'full_resolution_photo', photo_ok, flush=True)
                window.close()
                temporary_photos.cleanup()
                app.quit()
                return False
            GLib.timeout_add_seconds(11, finish_test)
        if switch_test:
            temporary_photos = tempfile.TemporaryDirectory(prefix='tb-camera-switch-ui-')
            window.capture_folder = Path(temporary_photos.name)
            stages = ['rear', 'front', 'macro', 'rear', 'front', 'macro'] if 'macro' in available_cameras() else ['rear','front','rear','front']
            progress = {'index': 0, 'frames': 0, 'captured': False, 'deadline': time.monotonic()+240}
            def advance():
                if time.monotonic() > progress['deadline']:
                    print('SWITCH_UI_TEST FAIL timeout', progress, flush=True)
                    window.close(); temporary_photos.cleanup(); app.quit(); return False
                current = stages[progress['index']]
                if window.camera_key != current:
                    window.select_camera(current)
                    progress['frames'] = window.frames
                    progress['captured'] = False
                    return True
                if not progress['captured']:
                    if window.frames - progress['frames'] < 4:
                        return True
                    progress['captured'] = True
                    window.last_photo_name = None
                    window.capture()
                    return True
                if window.busy or not window.last_photo_name:
                    return True
                data = (window.capture_folder/window.last_photo_name).read_bytes()
                ok = jpeg_dimensions(data) == profile(current)['full'] and jpeg_has_image(data)
                print('SWITCH_UI_STAGE', current, 'PASS' if ok else 'FAIL', jpeg_dimensions(data), flush=True)
                if not ok:
                    window.close(); temporary_photos.cleanup(); app.quit(); return False
                progress['index'] += 1
                if progress['index'] == len(stages):
                    app.test_ok = True
                    print('SWITCH_UI_TEST PASS', flush=True)
                    window.close(); temporary_photos.cleanup(); app.quit(); return False
                progress['frames'] = window.frames
                progress['captured'] = False
                return True
            GLib.timeout_add(200, advance)
    def command_line(app, command):
        value = command.get_options_dict().lookup_value('camera', None)
        requested = value.unpack() if value else 'rear'
        profile(requested)
        app.initial_camera = requested
        app.activate()
        window = app.get_active_window()
        if window:
            window.select_camera(requested)
        return 0
    app.connect('command-line', command_line)
    app.connect('activate', activate)
    result = app.run([sys.argv[0], '--camera', camera])
    return result or (0 if app.test_ok else 1)


if __name__ == '__main__':
    if '--smoke-test' in sys.argv:
        smoke_test()
    else:
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument('--camera', choices=['rear','front','macro'], default='rear')
        parser.add_argument('--ui-test', action='store_true')
        parser.add_argument('--switch-test', action='store_true')
        args = parser.parse_args()
        sys.exit(run_app(test=args.ui_test, camera=args.camera, switch_test=args.switch_test))
