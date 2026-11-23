# TB710FU 恢复系统挂起

适用于 Ubuntu 电源菜单缺少「挂起」的情况。在修复助手更新仓库后，下载并运行「恢复系统挂起（s2idle）」。应用需要管理员权限。完成后可从 GNOME 右上角快捷设置的电源菜单手动挂起。

脚本启用内核的 `mem / s2idle` 模式，按需解除 `sleep.target` 和 `suspend.target` 的本地屏蔽。它按当前系统状态判断，兼容不同 Ubuntu 用户名、磁盘容量和启动镜像。应用本身只修改配置，现有的空闲或合盖挂起策略会在功能恢复后生效。

- 检查用 `TB710FU_FIX_ACTION=check`，查询内核挂起能力、systemd 配置和 logind 状态。
- 应用用 `TB710FU_FIX_ACTION=apply`，修复助手默认使用此操作。
- 回退用 `TB710FU_FIX_ACTION=rollback`，恢复本次原有的屏蔽状态并清除本条目创建的配置。回退记录保存在本机 `/var/lib/tb710fu-suspend-restore/before.json`。

已在 TB710FU Ubuntu 的 20261118 内核上实测两次 `s2idle` 挂起，均在同一次开机中定时唤醒，解锁后背光正常恢复。首次自行尝试挂起时请先保存工作。
