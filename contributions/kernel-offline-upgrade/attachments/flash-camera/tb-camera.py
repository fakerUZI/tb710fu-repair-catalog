#!/usr/bin/python3
"""TB710FU front/rear camera UI, versioned profiles and safe JPEG capture."""
import datetime
import math
import os
from pathlib import Path
import sys
import tempfile
import time

CAMERA = '/base/soc@0/cci@ac15000/i2c-bus@1/camera@36'
FULL = (4200, 3120)
PROFILES = {
    'rear': {'name': '后摄', 'path': CAMERA, 'full': FULL,
             'previews': [(2560,1920), (1920,1440), FULL], 'focus': True},
    'front': {'name': '前摄', 'path': '/base/soc@0/cci@ac15000/i2c-bus@0/camera@39',
              'full': (4200,3120), 'previews': [(1280,720), (1920,1080), (4200,3120)], 'focus': False},
}


def profile(name):
    if name not in PROFILES:
        raise ValueError('Unknown camera profile')
    return PROFILES[name]


def preview_labels(name):
    return [f'{width}×{height}' for width, height in profile(name)['previews']]

PHOTO_WARMUP_FRAMES = 3
FOCUS = Path('/sys/module/tb_focus_manual/parameters/position')


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
            self.preview_timer = None
            self.frames = 0
            root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            self.set_child(root)
            header = Gtk.HeaderBar()
            self.set_titlebar(header)
            self.camera_selector = Gtk.DropDown.new_from_strings(['后摄', '前摄'])
            self.camera_selector.set_selected(0 if self.camera_key == 'rear' else 1)
            self.camera_selector.connect('notify::selected', self.camera_changed)
            header.pack_start(self.camera_selector)
            self.quality = Gtk.DropDown.new_from_strings(preview_labels(self.camera_key))
            header.pack_start(self.quality)
            self.quality.connect('notify::selected', self.quality_changed)
            self.view = View()
            root.append(self.view)
            toolbar = Gtk.Box(spacing=10, margin_start=16, margin_end=16)
            toolbar.append(Gtk.Label(label='放大'))
            self.zoom_slider = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 1, 4, .05)
            self.zoom_slider.set_hexpand(True)
            self.zoom_slider.set_draw_value(False)
            self.zoom_slider.connect('value-changed', self.zoom_changed)
            toolbar.append(self.zoom_slider)
            self.zoom_label = Gtk.Label(label='1.0×', width_chars=5)
            toolbar.append(self.zoom_label)
            for z in (1, 2, 4):
                button = Gtk.Button(label=str(z)+'×')
                button.connect('clicked', lambda button, z=z: self.zoom_slider.set_value(z))
                toolbar.append(button)
            root.append(toolbar)
            pinch = Gtk.GestureZoom.new()
            pinch.connect('begin', lambda gesture, seq: setattr(self, 'zoom_start', self.view.zoom))
            pinch.connect('scale-changed', lambda gesture, scale: self.zoom_slider.set_value(self.zoom_start*scale))
            self.view.add_controller(pinch)
            drag = Gtk.GestureDrag.new()
            drag.connect('drag-begin', lambda gesture, x, y: setattr(self, 'drag_start', list(self.view.center)))
            drag.connect('drag-update', self.pan)
            self.view.add_controller(drag)
            pinch.group(drag)
            scroll = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
            scroll.connect('scroll', self.scroll_zoom)
            self.view.add_controller(scroll)
            focus_row = Gtk.Box(spacing=10, margin_start=16, margin_end=16)
            focus_row.append(Gtk.Label(label='手动对焦'))
            self.focus = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1023, 1)
            self.focus.set_hexpand(True)
            self.focus.set_draw_value(True)
            try:
                self.focus.set_value(int(FOCUS.read_text()))
            except (OSError, ValueError):
                self.focus.set_value(700)
            self.focus.set_sensitive(os.access(FOCUS, os.W_OK))
            self.focus.connect('value-changed', self.focus_changed)
            focus_row.append(self.focus)
            self.focus_row = focus_row
            self.focus_row.set_visible(profile(self.camera_key)['focus'])
            root.append(focus_row)
            bottom = Gtk.Box(spacing=12, margin_start=16, margin_end=16, margin_bottom=12)
            self.status = Gtk.Label(label='正在启动相机…', xalign=0, hexpand=True, wrap=True)
            bottom.append(self.status)
            self.shutter = Gtk.Button(label='拍照 · 最高约1300万像素')
            self.shutter.add_css_class('suggested-action')
            self.shutter.connect('clicked', self.capture)
            bottom.append(self.shutter)
            root.append(bottom)
            self.connect('close-request', self.close_camera)
            GLib.idle_add(self.start_preview)

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
            self.view.queue_draw()

        def quality_changed(self, *_):
            if self.busy or self.changing_camera:
                return
            self.stop_preview()
            self.start_preview()

        def camera_changed(self, *_):
            if self.changing_camera:
                return
            requested = 'rear' if self.camera_selector.get_selected() == 0 else 'front'
            self.select_camera(requested)

        def select_camera(self, requested):
            profile(requested)
            if self.busy:
                self.changing_camera = True
                self.camera_selector.set_selected(0 if self.camera_key == 'rear' else 1)
                self.changing_camera = False
                self.status.set_text('拍照尚未完成，请稍后切换相机')
                return False
            if requested == self.camera_key:
                return True
            self.changing_camera = True
            self.stop_preview()
            if self.focus_timer:
                GLib.source_remove(self.focus_timer)
                self.focus_timer = None
            self.camera_key = requested
            self.camera_selector.set_selected(0 if requested == 'rear' else 1)
            self.quality.set_model(Gtk.StringList.new(preview_labels(requested)))
            self.quality.set_selected(0)
            self.focus_row.set_visible(profile(requested)['focus'])
            self.view.texture = None
            self.view.queue_draw()
            self.zoom_slider.set_value(1)
            self.view.center = [.5, .5]
            self.changing_camera = False
            self.start_preview()
            return True

        def focus_changed(self, scale):
            if not profile(self.camera_key)['focus']:
                return
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

        def start_preview(self):
            if self.closing or self.busy or self.preview:
                return False
            selected = profile(self.camera_key)
            width, height = selected['previews'][self.quality.get_selected()]
            self.set_title('相机 · '+selected['name'])
            self.preview = Gst.parse_launch(f'libcamerasrc name=camera ! video/x-raw,format=RGBA,width={width},height={height} ! appsink name=view sync=false max-buffers=1 drop=true')
            source = self.preview.get_by_name('camera')
            source.set_property('camera-name', selected['path'])
            self.sink = self.preview.get_by_name('view')
            self.preview.get_bus().add_signal_watch()
            self.preview.get_bus().connect('message::error', self.preview_error)
            self.preview.set_state(Gst.State.PLAYING)
            self.preview_timer = GLib.timeout_add(66, self.draw_frame)
            suffix = (' · 已保存：'+self.last_photo_name) if self.last_photo_name else ' · 双指缩放，放大后可拖动画面'
            self.status.set_text(f"{selected['name']}预览 {width}×{height}"+suffix)
            return False

        def stop_preview(self):
            if self.preview_timer:
                GLib.source_remove(self.preview_timer)
                self.preview_timer = None
            if self.preview:
                self.preview.set_state(Gst.State.NULL)
                self.preview.get_state(3*Gst.SECOND)
                self.preview.get_bus().remove_signal_watch()
                self.preview = None

        def draw_frame(self):
            if not self.preview:
                return False
            sample = self.sink.emit('try-pull-sample', 0)
            if sample:
                structure = sample.get_caps().get_structure(0)
                width, height = structure.get_value('width'), structure.get_value('height')
                buffer = sample.get_buffer()
                stride = buffer.get_size()//height
                data = GLib.Bytes.new(buffer.extract_dup(0, buffer.get_size()))
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

        def capture(self, *_):
            if self.busy:
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
            width, height = self.photo_dimensions
            self.finish_photo()
            self.status.set_text(f'已保存：{name}（{width}×{height}，数字缩放裁切）')

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
        if test:
            temporary_photos = tempfile.TemporaryDirectory(prefix='tb-camera-ui-check-')
            window.capture_folder = Path(temporary_photos.name)
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
                    photo_ok = jpeg_dimensions(data) == FULL and jpeg_has_image(data)
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
            stages = ['rear', 'front', 'rear', 'front']
            progress = {'index': 0, 'frames': 0, 'captured': False, 'deadline': time.monotonic()+90}
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
        parser.add_argument('--camera', choices=['rear','front'], default='rear')
        parser.add_argument('--ui-test', action='store_true')
        parser.add_argument('--switch-test', action='store_true')
        args = parser.parse_args()
        sys.exit(run_app(test=args.ui_test, camera=args.camera, switch_test=args.switch_test))
