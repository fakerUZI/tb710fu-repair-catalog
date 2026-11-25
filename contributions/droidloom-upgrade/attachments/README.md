# Droidloom 升级到最新版（测试版）

适用于 TB710FU Ubuntu 26.04 ARM64 上已完成配置、Android 数据已迁移到宿主目录的 Droidloom，从 0.1.0-42 起可重复使用。修复助手 0.10.19 在启动、刷新仓库和运行期间每小时查询官方 ARM64 软件包索引。发现更高版本时，在「未安装／可更新」中显示本机与目标版本。点击条目单独下载并应用更新。

运行前保存安卓窗口中的工作，并预留 10 GiB。脚本重新查询官方仓库，按当前 runtime 和 image 的精确依赖选择 base，支持上游复用较早的 base。通过 HTTPS 下载并按索引核对大小和 SHA-256，完成暂存后才停止服务。当前版本相同或更高时直接退出。宿主数据目录保留。已配置 Google 应用扩展的设备需另行核对。完成后用桌面账号执行 `droidloomctl start`。

保留整个条目目录时也可以在终端执行 `bash script.sh check` 查询，或执行 `sudo bash script.sh apply` 升级。

设置 `TB710FU_FIX_ACTION=check` 可只读查询，`rollback` 可离线恢复最近一次软件和主机配置快照。沿用原升级条目的快照目录 `/var/backups/tb710fu-droidloom-r49`，多次升级时较早快照带时间戳留存在同一父目录。已启动新版后产生的 Android 数据变更独立于软件快照。

昨天发布的手写笔触控兼容测试版适用于 0.1.0-42 至 0.1.0-49。42 对应输入 ABI 7，43–49 对应 ABI 8，均要求原生输入 ABI 2。超出范围时该测试版启动器回到系统自带的窗口程序。未来上游版本在 TB710FU 上的运行情况仍需按版本实测。

[官方 ARM64 索引](https://denialwm.github.io/droidloom/aarch64/droidloom.db.tar.gz) · [官方软件包](https://github.com/denialwm/droidloom/releases/tag/packages)
