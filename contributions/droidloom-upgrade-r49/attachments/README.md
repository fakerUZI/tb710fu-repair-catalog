# Droidloom 升级到 release 49（测试版）

适用于 Lenovo TB710FU 上运行 Ubuntu 26.04 ARM64、已完成配置的 Droidloom 0.1.0 release 42–48。已经随 BOOTFIX 安装了 release 49 的用户运行时会直接提示已是目标版本。

在修复助手中单独安装。先保存安卓窗口中的工作，确认有约 10 GiB 可用空间。脚本需要管理员权限，下载并校验官方 ARM64 runtime、image、base 三个固定软件包，再停止安卓运行时。软件和主机配置的回退快照保存在 `/var/backups/tb710fu-droidloom-r49`。现有 Android 数据目录保留。升级成功后，用桌面账号执行 `droidloomctl start`。

官方包合计约 981 MB，缓存支持续传。当前脚本适用于已迁移到宿主目录的 Android 数据。配置有 Google 应用扩展的设备需先核对与 release 49 匹配的扩展。

设置 `TB710FU_FIX_ACTION=check` 可查看当前版本和升级目标。设置 `TB710FU_FIX_ACTION=rollback` 可恢复软件和主机配置快照。回退不会倒转已启动新版后产生的 Android 数据变更。

已在 TB710FU release 49 上完成只读检查，并在隔离环境验证了版本判断、文件校验和回退逻辑。旧版平板的完整升级仍待实测。

[官方软件包](https://github.com/denialwm/droidloom/releases/tag/packages) · [旧版移植安装审核](https://github.com/fakerUZI/tb710fu-repair-catalog/pull/21)
