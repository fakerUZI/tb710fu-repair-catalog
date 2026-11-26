"""Install the verified TB710FU face PAM package from a repair-assistant job."""
import fcntl
import hashlib
import os
from pathlib import Path
import pwd
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
NAME = "tb710fu-face-password"
VERSION = "1.0.3"
DEB = f"{NAME}_{VERSION}_arm64.deb"
DIGEST = "92fc222f47af8af6d90e18ba9ea6f088d0c839cf876c6b9dae3f4fb37d0762c6"
BACKEND = Path("/usr/local/lib/tb710fu-face-unlock/face-unlock.py")
FACE_HASH = "7644a4d27a808768335d7feb925df2399e39f1b0197432d4d9f5000a7113edfa"


def require(ok, message):
    if not ok:
        raise ValueError(message)


def output(*cmd):
    return subprocess.check_output(cmd, text=True, timeout=10).strip()


def pkg(name):
    p = subprocess.run(["dpkg-query", "-W", "-f=${db:Status-Status} ${Version}", name],
                       text=True, capture_output=True, timeout=10)
    return p.stdout.strip()


def account():
    uid = int(os.environ.get("PKEXEC_UID") or os.environ.get("SUDO_UID") or 0)
    require(uid > 0, "请从桌面账号运行修复助手。")
    return pwd.getpwuid(uid)


def user_settings(user, action):
    runtime = f"/run/user/{user.pw_uid}"
    subprocess.run(["/usr/sbin/runuser", "-u", user.pw_name, "--", "/usr/bin/env", "-i",
                    f"HOME={user.pw_dir}", "PATH=/usr/bin:/bin", "LANG=C.UTF-8",
                    f"XDG_RUNTIME_DIR={runtime}", f"DBUS_SESSION_BUS_ADDRESS=unix:path={runtime}/bus",
                    "/usr/bin/python3", "-I", "-", action], input=(HERE / "settings-user.py").read_bytes(),
                   check=True, timeout=20)


def main(action):
    require(os.geteuid() == 0, "请由修复助手申请管理员授权。")
    current = pkg(NAME)
    if action == "rollback":
        require(not current or current.startswith(("not-installed ", "config-files "))
                or current.split()[-1] in ("1.0.2", VERSION), "已安装其他版本，请使用对应版本回退。")
        user_settings(account(), "rollback")
        if current and not current.startswith(("not-installed ", "config-files ")):
            subprocess.run(["dpkg", "--remove", NAME], check=True)
        print("已回退系统密码人脸验证。")
        return
    require(action in ("check", "apply"), "不支持的修复操作。")
    require(BACKEND.is_file() and hashlib.sha256(BACKEND.read_bytes()).hexdigest() == FACE_HASH,
            "请先从修复助手安装或更新「TB710FU 锁屏人脸解锁」。")
    if current and not current.startswith(("not-installed ", "config-files ")):
        require(subprocess.run(["dpkg", "--compare-versions", current.split()[-1], "le", VERSION]).returncode == 0,
                "本机已安装更新版本，已停止降级。")
    data = (HERE / DEB).read_bytes()
    require(hashlib.sha256(data).hexdigest() == DIGEST, "人脸验证安装包校验失败。")
    if action == "check":
        print("已有识别组件和安装包校验通过。")
        return
    with tempfile.TemporaryDirectory(prefix="tb710fu-face-package-") as tmp:
        staged = Path(tmp) / DEB
        staged.write_bytes(data)
        active = subprocess.run(["systemctl", "is-active", "--quiet", NAME]).returncode == 0
        pam = Path("/etc/pam.d/common-auth")
        if (current != f"installed {VERSION}" or not active or "/pam-face-password.so" not in pam.read_text()
                or output("dpkg", "--verify", NAME)):
            subprocess.run(["dpkg", "--install", staged], check=True)
        require(subprocess.run(["systemctl", "is-active", "--quiet", NAME]).returncode == 0
                and "/pam-face-password.so" in pam.read_text(), "验证服务或 PAM 配置尚未生效。")
    print("已安装。请从应用列表打开「系统密码人脸验证」选择模式。")


if __name__ == "__main__":
    try:
        if sys.argv[1:] == ["check"]:
            main("check")
        else:
            with open("/run/lock/tb710fu-face-password-catalog.lock", "a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                main(sys.argv[1])
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        sys.exit(f"系统密码人脸验证：{exc}")
