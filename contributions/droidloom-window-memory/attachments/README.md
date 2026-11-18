# Droidloom 窗口切换卡顿缓解

适用于 Lenovo TB710FU、Ubuntu 26.04 ARM64、支持 zram 的内核和已安装的 Droidloom。实机验证为内核 `7.2.6-tb710fu-20261118`、Droidloom release 42。GNOME 在切换大尺寸安卓窗口时遇到 Adreno 图形内存回收和磁盘交换阻塞。

应用保存 3 GiB、优先级 100、压缩算法为 `lzo-rle` 的 zram 配置，并将 `vm.watermark_scale_factor` 设为 100，让后台内存回收更早启动。现有磁盘交换继续作为后备。Ubuntu 软件源中的 `systemd-zram-generator` 会按需安装。保存工作后在方便时重启，配置随后生效。

## 应用与回退

在修复助手里用管理员权限应用。直接执行 Bash 入口默认只读。

```sh
bash script.sh
sudo env TB710FU_FIX_ACTION=apply bash script.sh
sudo env TB710FU_FIX_ACTION=rollback bash script.sh
```

配置作用于全系统。压缩交换会消耗部分 RAM 和 CPU。已有其他 zram0 配置、zram-tools 或同名配置不同内容时，请先核对现有系统策略。应用和回退均不主动重新初始化交换设备。快照在 `/var/backups/tb710fu-droidloom-window-memory/`。回退移除本条目创建的配置。若生成器由本条目安装，回退也会尝试仅移除该包，保留系统依赖检查。下一次启动恢复原配置。

## 验证

平板曾测到一次约 13.4 秒的 GNOME 回调间隔。手动启用同一组设置后，连续 12 次进出全屏，监测到最长回调间隔 80 毫秒。已在实机验证生成器生成优先级 100 的交换单元。数值来自指定设备的一轮测试。投稿脚本的应用和回退在隔离环境验证，平板只运行只读检查，修复助手端到端和重启待验证。
