# Droidloom 手写笔触控兼容（测试版）

适用于 Lenovo TB710FU 的 Ubuntu GNOME Wayland 和 Droidloom 0.1.0 release 49 ARM64。安装前先确认手写笔在 Ubuntu 原生应用里可用。

在修复助手单独安装，保存安卓应用中的工作后，运行 `droidloomctl restart`，再打开笔记应用。应用如果有「允许手指书写」选项，请开启。依次测试笔尖点击、慢速连线、轻重笔迹和抬笔断线，并检查笔与手指可以交替使用。

测试版接收 Wayland 的笔尖坐标、接触状态和压感，使用 Droidloom 已有的安卓触控通道。安卓应用收到手指类型的触点，笔尖悬停、倾斜、侧键和仅手写笔模式需要安卓原生手写笔事件支持。应用是否使用触点压感取决于其笔刷实现。

安装只写入当前账号的 Droidloom 启动覆盖和测试程序。无需管理员权限。原版程序和安卓应用数据保留在原路径。检测到非 release 49 时，启动覆盖会运行系统自带的 Droidloom。

运行脚本时设置 `TB710FU_FIX_ACTION=check` 可检查适用条件。设置 `TB710FU_FIX_ACTION=rollback` 可撤销本测试版。撤销后重启 Droidloom，恢复原版窗口程序。

已在 20261124 内核的 TB710FU 上验证安装、Droidloom 49 重启和安卓设置启动。实体手写笔和笔记软件书写待实测。
