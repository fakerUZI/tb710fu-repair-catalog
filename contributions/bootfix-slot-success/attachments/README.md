# BOOTFIX A 槽启动确认

适用于已经从 **2026-11-22 BOOTFIX** 的 A 槽进入 Ubuntu 的 TB710FU。原安装器会给 A 槽设置试启动次数，但恢复的系统缺少成功确认步骤。次数耗尽后，平板可能回到 bootloader Fastboot。

在修复助手更新仓库后，打开「BOOTFIX A 槽启动确认（单独安装）」。先运行只读检查，只有显示 `successful=no` 且 `unbootable=no` 时才应用。应用结束后再检查，预期显示 `successful=yes`，`retry` 数字保持原值。已经成功的槽位无需再应用。

申请管理员权限后，脚本查验机型、当前 A 槽、可写的 ext4 根分区、主备 GPT 和原版 BOOTFIX 启动镜像。应用只写入 A 槽启动成功位和对应的 GPT 校验值。写入前将 GPT 备份留在本机 `/var/lib/tb710fu-boot-slot/boot-gpt.before.bin`，供离线恢复。修复助手不会自动回退这个启动成功位。

如果平板停在 Fastboot，修复助手须等 Ubuntu 恢复启动后才可运行。只有确认原安装已完成、A 槽保留正式 Ubuntu 引导，同时重试已经耗尽，才使用 `fastboot set_active a` 恢复试启动机会，再用 `fastboot reboot` 进入 Ubuntu。恢复计数无需重新安装或格式化。

已在 TB710FU 上完成只读检查。在 4 KiB 扇区的隔离 GPT 和厂商布局副本上验证了写入、回读、保留重试次数、保留 B 槽以及备份恢复。没有在计数已耗尽的用户设备上执行实机恢复。
