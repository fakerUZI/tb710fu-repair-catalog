#!/usr/bin/python3
"""Local front-camera verification for the active GDM lock screen."""

import argparse
import base64
import fcntl
import json
import os
from pathlib import Path
import pwd
import select
import subprocess
import sys
import syslog
import tempfile
import time

from gi.repository import Gio, GLib

PROGRAM = Path(__file__).resolve().parent
STATE = Path("/var/lib/tb710fu-face-unlock")
LOGIN = "org.freedesktop.login1"
WORKER = "/usr/libexec/gdm-session-worker"
FRONT_CAMERA = "/base/soc@0/cci@ac15000/i2c-bus@0/camera@39"
POSES = (0, 90, 180, 270)
WIDTH, HEIGHT = 640, 480
SAMPLES = 6
MATCH_THRESHOLD = 0.55
PREVIEW_WIDTH, PREVIEW_HEIGHT = 320, 240
LOCK_TITLE = "◉ 人脸解锁"


def say(message):
    print(message, flush=True)


def lock_message(message):
    say(f"{LOCK_TITLE} · {message}")


def active_session():
    bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)

    def call(path, method, signature, arguments):
        return bus.call_sync(
            LOGIN, path, "org.freedesktop.DBus.Properties", method,
            GLib.Variant(signature, arguments), None, Gio.DBusCallFlags.NONE, 800, None,
        ).unpack()[0]

    _, path = call(
        "/org/freedesktop/login1/seat/seat0", "Get", "(ss)",
        (f"{LOGIN}.Seat", "ActiveSession"),
    )
    if path == "/":
        return {}
    return call(path, "GetAll", "(s)", (f"{LOGIN}.Session",))


def is_local_desktop(session, uid, name, locked):
    return (
        uid >= 1000
        and session.get("User", (None,))[0] == uid
        and session.get("Name") == name
        and session.get("Seat", (None,))[0] == "seat0"
        and session.get("Class") == "user"
        and session.get("Type") == "wayland"
        and session.get("Service") == "gdm-password"
        and session.get("Remote") is False
        and session.get("Active") is True
        and session.get("LockedHint") is locked
    )


def parent_pid(pid):
    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
        if line.startswith("PPid:"):
            return int(line.split()[1])
    return None


def reauth_worker(worker, leader):
    watchdog = os.getppid()
    if leader in (None, worker) or not os.path.samefile(f"/proc/{watchdog}/exe", "/usr/bin/timeout"):
        return False
    if any(os.readlink(f"/proc/{pid}/exe") != WORKER for pid in (worker, leader)):
        return False
    return parent_pid(watchdog) == worker and parent_pid(worker) == leader


def template_path(uid):
    return STATE / f"{uid}.json"


def load_template(uid, name):
    path = template_path(uid)
    if not path.exists():
        return None
    with path.open() as file:
        template = json.load(file)
    if template["user"] != name or template["rotation"] not in POSES:
        return None
    return template


def store_template(uid, name, rotation, features):
    descriptor, path = tempfile.mkstemp(dir=STATE)
    try:
        with os.fdopen(descriptor, "w") as file:
            json.dump({"user": name, "rotation": rotation, "features": features}, file)
            file.flush()
            os.fsync(file.fileno())
        os.replace(path, template_path(uid))
    finally:
        Path(path).unlink(missing_ok=True)


class FrontCamera:
    def __init__(self, *, identity=True):
        self.identity = identity

    def __enter__(self):
        os.environ["LIBCAMERA_LOG_LEVELS"] = "*:FATAL"
        os.environ["OPENCV_LOG_LEVEL"] = "SILENT"
        import cv2
        import gi
        import numpy as np
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst

        self.cv = cv2
        self.np = np
        self.gst = Gst
        self.last_timestamp = None
        cv2.setNumThreads(2)
        self.detector = cv2.FaceDetectorYN.create(
            str(PROGRAM / "face_detection_yunet_2023mar.onnx"), "", (WIDTH, HEIGHT), 0.90,
        )
        if self.identity:
            self.recognizer = cv2.FaceRecognizerSF.create(
                str(PROGRAM / "face_recognition_sface_2021dec.onnx"), "",
            )
        Gst.init(None)
        self.pipeline = Gst.parse_launch(
            f"libcamerasrc camera-name={FRONT_CAMERA} "
            f"! video/x-raw,width={WIDTH},height={HEIGHT} "
            "! videoconvert ! video/x-raw,format=BGR "
            "! appsink name=frames sync=false max-buffers=1 drop=true"
        )
        self.sink = self.pipeline.get_by_name("frames")
        if self.pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            self.pipeline.set_state(Gst.State.NULL)
            raise RuntimeError("无法启动前置相机")
        return self

    def __exit__(self, *_):
        self.pipeline.set_state(self.gst.State.NULL)

    def read(self):
        sample = self.sink.emit("try-pull-sample", 250 * self.gst.MSECOND)
        if sample is None:
            error = self.pipeline.get_bus().pop_filtered(self.gst.MessageType.ERROR)
            if error is not None:
                raise RuntimeError("前置相机采集失败，请关闭正在使用相机的应用")
            return None
        buffer = sample.get_buffer()
        if buffer.pts == self.last_timestamp or buffer.get_size() != WIDTH * HEIGHT * 3:
            return None
        self.last_timestamp = buffer.pts
        return self.np.frombuffer(buffer.extract_dup(0, buffer.get_size()), dtype="uint8").reshape(
            HEIGHT, WIDTH, 3,
        )

    def detect(self, frame, rotation):
        frame = self.np.ascontiguousarray(self.np.rot90(frame, rotation // 90))
        self.detector.setInputSize((frame.shape[1], frame.shape[0]))
        _, faces = self.detector.detect(frame)
        if faces is None or len(faces) != 1:
            return None
        face = faces[0]
        if min(face[2:4]) < 75:
            return None
        eye_a, eye_b, nose = face[4], face[6], face[8]
        eye_span = abs(eye_a - eye_b)
        if eye_span < 18:
            return None
        yaw = (2 * nose - eye_a - eye_b) / eye_span
        return frame, face, yaw

    def observe(self, frame, rotation):
        detection = self.detect(frame, rotation)
        if detection is None:
            return None
        frame, face, yaw = detection
        aligned = self.recognizer.alignCrop(frame, face)
        features = self.recognizer.feature(aligned).flatten()
        magnitude = self.np.linalg.norm(features)
        if not self.np.isfinite(magnitude) or magnitude <= 0:
            return None
        return yaw, features / magnitude


def publish_preview(camera, frame, rotation):
    rotated = camera.np.rot90(frame, rotation // 90)
    height, width = rotated.shape[:2]
    scale = min(PREVIEW_WIDTH / width, PREVIEW_HEIGHT / height)
    width, height = round(width * scale), round(height * scale)
    resized = camera.cv.resize(rotated, (width, height), interpolation=camera.cv.INTER_AREA)
    preview = camera.np.zeros((PREVIEW_HEIGHT, PREVIEW_WIDTH, 3), dtype="uint8")
    left, top = (PREVIEW_WIDTH - width) // 2, (PREVIEW_HEIGHT - height) // 2
    preview[top:top + height, left:left + width] = resized[:, ::-1]
    say(f"PREVIEW {base64.b64encode(preview).decode('ascii')}")


def enroll(uid, *, require_tty=True, preview=False):
    name = pwd.getpwuid(uid).pw_name
    if (require_tty and not sys.stdin.isatty()) or not is_local_desktop(active_session(), uid, name, False):
        raise RuntimeError("请先解锁平板，再从本机桌面录入")
    say("请横放平板，在光线充足处正视前置相机。")
    previous = load_template(uid, name) if preview else None
    preview_rotation = previous["rotation"] if previous else 0
    with FrontCamera() as camera:
        deadline = time.monotonic() + (40 if preview else 24)
        capturing = not preview
        rotation = None
        features = []
        last_sample = 0
        last_preview = 0
        while time.monotonic() < deadline:
            if not capturing and select.select([sys.stdin], [], [], 0)[0]:
                if sys.stdin.readline().strip() != "START":
                    return False
                capturing = True
                deadline = time.monotonic() + 24
                say("正在录入，请正视前置相机并保持平板稳定。")
            frame = camera.read()
            if frame is None:
                continue
            now = time.monotonic()
            if preview and now - last_preview >= 0.10:
                publish_preview(camera, frame, preview_rotation)
                last_preview = now
            if now - last_sample < (0.4 if preview else 0.28):
                continue
            if preview:
                last_sample = now
            angles = (rotation,) if rotation is not None else (
                preview_rotation, *(angle for angle in POSES if angle != preview_rotation)
            )
            for angle in angles:
                observation = camera.observe(frame, angle)
                if observation is None or abs(observation[0]) > 0.20:
                    continue
                preview_rotation = angle
                if not capturing:
                    last_sample = now
                    break
                vector = observation[1]
                if features and float(vector @ features[0]) < 0.65:
                    raise RuntimeError("不同画面中的人脸不一致，录入已取消")
                rotation = angle
                features.append(vector)
                last_sample = now
                if preview:
                    say(f"PROGRESS {len(features)}")
                break
            if len(features) == SAMPLES:
                if not is_local_desktop(active_session(), uid, name, False):
                    raise RuntimeError("桌面会话已切换，录入已取消")
                store_template(uid, name, rotation, [vector.tolist() for vector in features])
                say("录入完成。人脸特征已保存在平板上。")
                return True
    if not capturing:
        raise RuntimeError("预览已超时，请重新打开录入窗口")
    raise RuntimeError("未采集到足够的清晰正脸。请在光线充足时重试")


def verify(template):
    consecutive = 0
    lock_message("正在识别，请正视前置相机")
    with FrontCamera() as camera:
        reference = camera.np.asarray(template["features"], dtype="float32")
        if reference.shape != (SAMPLES, 128) or not camera.np.isfinite(reference).all():
            return False
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            frame = camera.read()
            if frame is None:
                continue
            observation = camera.observe(frame, template["rotation"])
            if observation is None:
                consecutive = 0
                continue
            yaw, vector = observation
            matched = abs(yaw) <= 0.23 and float(camera.np.max(reference @ vector)) >= MATCH_THRESHOLD
            consecutive = consecutive + 1 if matched else 0
            if consecutive >= 2:
                return True
    return False


def authenticate(name, worker):
    uid = pwd.getpwnam(name).pw_uid
    session = active_session()
    leader = session.get("Leader")
    if not is_local_desktop(session, uid, name, True) or not reauth_worker(worker, leader):
        return False
    template = load_template(uid, name)
    if template is None:
        return False
    runtime = Path("/run/tb710fu-face-unlock")
    runtime.mkdir(mode=0o700, exist_ok=True)
    with (runtime / "attempt").open("a+") as attempt:
        fcntl.flock(attempt, fcntl.LOCK_EX | fcntl.LOCK_NB)
        attempt.seek(0)
        now = int(time.monotonic())
        if now - int(attempt.read() or "-60") < 35:
            return False
        attempt.seek(0)
        attempt.truncate()
        attempt.write(str(now))
        attempt.flush()
        if not verify(template):
            lock_message("识别未通过，请输入密码")
            return False
        current = active_session()
        verified = (
            current.get("Id") == session.get("Id")
            and is_local_desktop(current, uid, name, True)
            and reauth_worker(worker, current.get("Leader"))
        )
        if verified:
            attempt.seek(0)
            attempt.truncate()
            syslog.openlog("tb710fu-face-unlock")
            syslog.syslog(syslog.LOG_NOTICE, "Face verification succeeded for a local lock screen")
            lock_message("识别通过")
        return verified


def settings_action(action):
    uid = int(os.environ.get("PKEXEC_UID", "-1"))
    name = pwd.getpwuid(uid).pw_name
    if not is_local_desktop(active_session(), uid, name, False):
        raise RuntimeError("请在已解锁的平板桌面中操作")
    with (STATE / "settings.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if action in ("enroll", "enroll-preview"):
            if not enroll(uid, require_tty=False, preview=action == "enroll-preview"):
                return False
            action = "enable"
        if action == "enable" and load_template(uid, name) is None:
            raise RuntimeError("请先录入人脸")
        if action == "forget":
            template_path(uid).unlink(missing_ok=True)
            if not any(STATE.glob("*.json")):
                subprocess.run([PROGRAM / "manage", "disable"], check=True, stdout=subprocess.DEVNULL)
            say("已删除本账号的人脸资料")
        else:
            subprocess.run([PROGRAM / "manage", action], check=True)
    return True


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    auth = commands.add_parser("authenticate")
    auth.add_argument("user")
    auth.add_argument("worker", type=int)
    commands.add_parser("enroll")
    forget = commands.add_parser("forget")
    forget.add_argument("user")
    settings = commands.add_parser("settings")
    settings.add_argument("action", choices=("enroll", "enroll-preview", "enable", "disable", "forget"))
    commands.add_parser("probe")
    args = parser.parse_args()
    if args.command != "probe" and os.geteuid() != 0:
        parser.error("请使用 sudo")
    try:
        if args.command == "authenticate":
            return 0 if authenticate(args.user, args.worker) else 1
        if args.command == "enroll":
            return 0 if enroll(int(os.environ.get("SUDO_UID", "-1"))) else 1
        if args.command == "settings":
            return 0 if settings_action(args.action) else 1
        if args.command == "forget":
            template_path(pwd.getpwnam(args.user).pw_uid).unlink(missing_ok=True)
            return 0
        with FrontCamera() as camera:
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                frame = camera.read()
                if frame is not None:
                    camera.observe(frame, 0)
                    say("TB710FU 前摄取帧和识别模型自检通过")
                    return 0
        raise RuntimeError("未收到前摄帧")
    except BrokenPipeError:
        return 1
    except Exception as error:
        if args.command == "authenticate":
            if isinstance(error, RuntimeError):
                lock_message("前置相机暂不可用，请输入密码")
        else:
            say(f"人脸解锁：{error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
