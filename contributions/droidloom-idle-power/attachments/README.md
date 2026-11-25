# DroidLoom 空闲关停与 Ubuntu 电源调度

适用于 TB710FU Ubuntu 的 BOOTFIX 按需启动器。最后一个安卓应用退出、也没有前台服务后，连续确认空闲 120 秒即请求停止容器。后台任务栈中最小化的应用和音乐前台服务会延后关停。下次点击安卓应用时，启动器会重新启动容器。

每 5 秒采样一次。Ubuntu 26.04.1 实机退出安卓设置后，125.4 秒发出停止请求，135.8 秒完成关停。自动挂起的等待时间由 Ubuntu 桌面电源设置控制。

平衡和节能档位的 CPU、GPU 频率上限与调度参数参考 [tb710fu-power-tuning 2026.11.24-1](https://github.com/deinthoedoka/tb710fu-update/releases/download/repo-aarch64/tb710fu-power-tuning-2026.11.24-1-aarch64.pkg.tar.zst)。切换 Ubuntu 电源档位时自动应用，停止调度服务时恢复此前的数值。

在修复助手详情页下载并运行。安装会备份兼容的原启动器。可以用以下命令查看关停记录及调度状态。

```sh
journalctl --user -b -u tb710fu-droidloom-idle.service
systemctl status tb710fu-profile-tuning.service
```

如需撤销，退出助手后在终端以桌面用户运行以下命令，再打开本条目并点击运行。撤销会恢复备份的按需启动器并移除本项服务。

```sh
TB710FU_FIX_ACTION=rollback tb710fu-repair-assistant
```
