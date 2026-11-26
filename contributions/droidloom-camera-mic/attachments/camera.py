#!/usr/bin/python3
"""Droidloom camera endpoint backed by the tablet's existing libcamera ISP."""
import array
import contextlib
import math
import mmap
import os
from pathlib import Path
import select
import socket
import struct
import threading
import time

import gi
gi.require_version("Gst", "1.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib, Gst

# Expose modes producing frames on this tablet. Width and height describe
# the actual captured image used for both preview and JPEG.
WIDTH, HEIGHT = 1280, 960
FRAME_BYTES = WIDTH * HEIGHT * 4
SLOTS = 3
CAMERAS = {
    "rear": ("ov13b10", "/base/soc@0/cci@ac15000/i2c-bus@1/camera@36", 1),
    "front": ("gc13a2_raw", "/base/soc@0/cci@ac15000/i2c-bus@0/camera@39", 0),
    "macro": ("gc02m1_raw", "/base/soc@0/cci@ac16000/i2c-bus@0/camera@37", 1),
}
MAGIC = b"DLDEV001"
camera_lock = threading.Lock()


def send_fd(sock, payload, fd):
    sock.sendmsg([payload], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [fd]))])


def devices():
    names = {p.read_text().split()[0] for p in Path("/sys/class/video4linux").glob("v4l-subdev*/name")}
    available = [(key, model, facing) for key, (model, _, facing) in CAMERAS.items() if model in names]
    message = bytearray(MAGIC + struct.pack("<II", 17, len(available)))
    for key, model, facing in available:
        for label in (key, model):
            value = label.encode()
            message.extend(struct.pack("<H", len(value)) + value)
        message.extend(struct.pack("<BBIIf", facing, 0, WIDTH, HEIGHT, 1.0))
    return message


def pixbuf(raw):
    return GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(raw), GdkPixbuf.Colorspace.RGB,
                                           True, 8, WIDTH, HEIGHT, WIDTH * 4)


def crop(image, aspect, zoom):
    w, h = image.get_width(), image.get_height()
    cw = max(1, min(w, round(min(w, h * aspect) / zoom)))
    ch = max(1, min(h, round(cw / aspect)))
    return image.new_subpixbuf((w - cw) // 2, (h - ch) // 2, cw, ch)


def jpeg(data, fd, size, zoom, mirrored):
    w, h = size
    if not (0 < w <= WIDTH and 0 < h <= HEIGHT):
        raise ValueError("unsupported photo size")
    rgb = bytearray(WIDTH * HEIGHT * 3)
    rgb[0::3], rgb[1::3], rgb[2::3] = data[2::4], data[1::4], data[0::4]
    image = GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(bytes(rgb)), GdkPixbuf.Colorspace.RGB,
                                            False, 8, WIDTH, HEIGHT, WIDTH * 3)
    if mirrored:
        image = image.flip(True)
    image = crop(image, w / h, zoom).scale_simple(w, h, GdkPixbuf.InterpType.BILINEAR)
    ok, buffer = image.save_to_bufferv("jpeg", ["quality"], ["90"])
    if not ok:
        raise OSError("JPEG encoding failed")
    with os.fdopen(os.dup(fd), "wb") as output:
        output.seek(0)
        output.write(buffer)
        output.truncate()


def capture(peer, key):
    _, path, facing = CAMERAS[key]
    native = (1592, 1200) if key == "macro" else (WIDTH, HEIGHT)
    flip = "videoflip method=horizontal-flip ! " if facing == 0 else ""
    pipeline = Gst.parse_launch(
        f'libcamerasrc camera-name="{path}" ! video/x-raw,format=RGBA,width={native[0]},height={native[1]} '
        f'! videoscale ! videoconvert ! {flip}video/x-raw,format=BGRA,width={WIDTH},height={HEIGHT} '
        '! appsink name=frames max-buffers=1 drop=true sync=false')
    sink = pipeline.get_by_name("frames")
    with contextlib.ExitStack() as stack:
        stack.callback(pipeline.set_state, Gst.State.NULL)
        if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            raise OSError("libcamera failed to start")
        fd = os.memfd_create("tb710fu-preview", os.MFD_CLOEXEC)
        stack.callback(os.close, fd)
        os.ftruncate(fd, SLOTS * FRAME_BYTES)
        shared = stack.enter_context(mmap.mmap(fd, SLOTS * FRAME_BYTES))
        free = set(range(SLOTS))
        seq, zoom, raw = 0, 1.0, None
        retried = False
        last = time.monotonic()
        while True:
            if select.select([peer], [], [], 0)[0]:
                packet, fds, flags, _ = socket.recv_fds(peer, 128, 2)
                try:
                    if flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC):
                        raise ValueError("truncated request")
                    if not packet or packet == b"\x02":
                        return
                    if packet[0] == 6 and len(packet) == 5:
                        slot, = struct.unpack("<I", packet[1:])
                        if slot < SLOTS:
                            free.add(slot)
                    if packet[0] == 4 and len(packet) == 5:
                        value, = struct.unpack("<f", packet[1:])
                        if math.isfinite(value):
                            zoom = max(1.0, min(4.0, value))
                    if packet[0] == 5 and len(packet) in (5, 13) and len(fds) == 1 and raw is not None:
                        shot = packet[1:5]
                        size = struct.unpack("<II", packet[5:]) if len(packet) == 13 else (WIDTH, HEIGHT)
                        try:
                            print(f"{key} photo requested {size}", flush=True)
                            peer.send(b"\x03" + shot)
                            jpeg(raw, fds[0], size, zoom, facing == 0)
                            print(f"{key} photo encoded {os.fstat(fds[0]).st_size} bytes", flush=True)
                            peer.send(b"\x04" + shot)
                        except (ValueError, OSError, GLib.Error) as error:
                            print(f"{key} photo failed: {error}", flush=True)
                            peer.send(b"\x05" + shot + str(error).encode()[:250])
                finally:
                    for received in fds:
                        os.close(received)
                continue
            sample = sink.emit("try-pull-sample", 40 * Gst.MSECOND)
            if sample is None:
                elapsed = time.monotonic() - last
                if not seq and not retried and elapsed > 1.25:
                    # CAMSS sometimes misses stream-on when switching sensors.
                    # Release the pipeline before repeating the start once.
                    retried = True
                    pipeline.set_state(Gst.State.NULL)
                    pipeline.get_state(300 * Gst.MSECOND)
                    if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
                        raise OSError("libcamera restart failed")
                if elapsed > 3.8:
                    raise OSError("libcamera returned no frames")
                continue
            last = time.monotonic()
            if not free:
                continue
            buffer = sample.get_buffer()
            if buffer.get_size() != FRAME_BYTES:
                raise OSError("unexpected libcamera frame layout")
            raw = buffer.extract_dup(0, FRAME_BYTES)
            if not seq:
                send_fd(peer, struct.pack("<BIIBII", 1, WIDTH, HEIGHT, 0, SLOTS, FRAME_BYTES), fd)
            preview = raw
            if zoom != 1.0:
                # Cropping and interpolation work identically on RGBA and BGRA.
                image = crop(pixbuf(raw), WIDTH / HEIGHT, zoom)
                preview = image.scale_simple(WIDTH, HEIGHT, GdkPixbuf.InterpType.BILINEAR).get_pixels()
            slot = free.pop()
            shared[slot * FRAME_BYTES:(slot + 1) * FRAME_BYTES] = preview
            seq += 1
            peer.send(struct.pack("<BIIIQ", 2, slot, WIDTH, HEIGHT, seq))


def session(peer):
    with peer:
        try:
            peer.settimeout(6)
            packet = peer.recv(64)
            if packet[:1] != b"\x01":
                return
            key = packet[1:].decode()
            if key not in CAMERAS:
                raise ValueError("unknown camera")
            if not camera_lock.acquire(timeout=1):
                raise OSError("camera busy")
            try:
                capture(peer, key)
            finally:
                camera_lock.release()
        except (BrokenPipeError, ConnectionResetError):
            return
        except (ValueError, OSError, GLib.Error) as error:
            print(f"camera session: {error}", flush=True)
            with contextlib.suppress(OSError):
                peer.send(b"\x05\0\0\0\0" + str(error).encode()[:250])


def main():
    Gst.init(None)
    listener = socket.socket(fileno=3)
    while True:
        peer, _ = listener.accept()
        with peer:
            _, uid, _ = struct.unpack("3i", peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid != 1047:  # Android cameraserver, as in the upstream broker.
                continue
            peer.settimeout(2)
            try:
                request = peer.recv(128)
                if request == MAGIC + struct.pack("<I", 16):
                    peer.send(devices())
                elif request == MAGIC + struct.pack("<I", 18):
                    server, client = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
                    with client:
                        threading.Thread(target=session, args=(server,), daemon=True).start()
                        send_fd(peer, MAGIC + struct.pack("<I", 19), client.fileno())
            except OSError as error:
                print(f"camera broker: {error}", flush=True)


if __name__ == "__main__":
    main()
