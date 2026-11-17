# TB710FU 日期内核升级：20261117

目标内核 `7.2.6-tb710fu-20261117`。本条目是目录第一项，也是唯一完整内核升级入口；七个分片均为新的日期版载荷，历史“原版 → snap1”脚本、旧格式分支和旧分片已移除。

已在 Ubuntu 26.04.1 ARM64 实机完成 `7.2.6-tb710fu-snap1` → 日期版升级、重启和自动收尾。实际 `uname -r`、嵌入配置和模块版本均已核验，Snap strict/full、USB、前后摄、对焦及隔离 NAT 测试通过。完整证据和边界见 [VALIDATION.md](VALIDATION.md)。

本次版本条件接受所有合法、不带日期的旧内核，不限制 snap1 白名单。仍须匹配已审核的固件、GPT、boot、后摄 overlay 和配套文件；未知定制系统会在写入前停止。后续更新仅接受标准日期版本，目标日期必须严格晚于设备当前内核日期，不允许同日覆盖、倒退或虚构未来日期。规则见 [KERNEL-VERSIONING.md](KERNEL-VERSIONING.md)。

## 已包含的修复与独立补充

发布的 boot 设备树已包含内置麦克风 WCD/SoundWire DAI 1 采集链路，ath12k 模块已含 vdev 计数补丁。旧 `microphone-snap1` 刷写入口及 `ath12k-stats-timeout-recovery` 临时重载入口已从目录和当前仓库删除；历史记录保留在 Git 中。

现有麦克风 ALSA 初始化服务和 WirePlumber 路由继续沿用，完整内核安装器不会新建这些用户态文件。输入法、电源键、窗口触控、DroidLoom 按需启动与容器音频为独立用户态配置，仍保留各自目录入口。

`install` 只执行完整内核升级。相机、微距、自动对焦、原厂风格界面和手电筒的新增配套仍需显式执行本文末尾的 `camera-install` / `flash-install`，不能仅凭附件存在就视为已安装。

## 使用

修复助手必须下载本条目全部附件，核验 catalog.json 中所有 SHA256，再将附件目录交给唯一 Bash 入口。每个分片最多 8 MiB，小于应用单附件 10 MiB 限制。

```bash
# 默认 check，仅现场只读预检，不写分区。
sudo env TB710FU_ATTACHMENTS_DIR=/path/to/attachments bash /path/to/script.sh
# 安装：备份、模块检查、写 boot_a、完整回读，不自动重启。
sudo env TB710FU_ATTACHMENTS_DIR=/path/to/attachments \
  TB710FU_KERNEL_ACTION=install TB710FU_KERNEL_USER=ubuntu bash /path/to/script.sh
# 保存工作后手动重启。
sudo reboot
```

`TB710FU_KERNEL_ACTION` 支持 check/install/validate/status/finish/rollback，兼容已有 `TB710FU_SNAP_KERNEL_ACTION`。validate 只重组并核验包，不访问设备，不需要 root；check 在平板上执行现场预检。status/finish/rollback 需要管理员权限。已运行目标版本时 check 可报告已完成；install 拒绝重复刷入同日目标。安装动作须先展示风险并取得用户授权。

需要 Python 3.11+、完整 Ubuntu ARM64/systemd、gdisk/fdisk/kmod/util-linux，电量至少 30%，/var/tmp 至少 512 MiB、根分区至少 1 GiB 空间。安装本身离线；启动后 Snap 收尾若需下载缺少的软件包/hello-world，则需要网络。失败后可通过 finish 重试，不重复刷写。

## 前摄与私有配套

完整公开模块包含后摄/对焦及正式网络模块。已经启用前摄的设备，须预先具备 `/opt/tb710fu-front-camera/7.2.6-tb710fu-20261117/` 下匹配的新私有模块 `gc13a2_raw.ko`、`tb_front_registration.ko`，其哈希和 vermagic 必须通过升级器校验。本轮平板已单独重编、安装并实测这些配套；私有模块和表数据不在 GitHub 载荷中。没有启用前摄的系统不会被自动开启前摄。

这些手动加载模块继续保留在私有目录；安装器单独校验其依赖，不把它们塞进公共模块目录或生成自动加载别名。若配套缺失，安装会在写 boot 前停止。

## 回退

每版本独立备份目录：`/var/backups/tb710fu-kernel-upgrade/7.2.6-tb710fu-20261117/`，其 `kernel-upgrade-active` 指向本次记录。保留现场 boot、六份 GPT、配套文件、状态及持久化安装器。备份含设备身份，应保密。

```bash
sudo python3 /var/backups/tb710fu-kernel-upgrade/7.2.6-tb710fu-20261117/kernel-upgrade-active/payload/upgrade-ubuntu-kernel.py --status
sudo python3 /var/backups/tb710fu-kernel-upgrade/7.2.6-tb710fu-20261117/kernel-upgrade-active/payload/upgrade-ubuntu-kernel.py --rollback
sudo reboot
```

回退前核验所有备份、当前固件/GPT 和文件；发现损坏备份或其他操作改动会拒绝覆盖。正在运行的新内核模块不会被删除，旧内核及原私有前摄模块均保留。完全无法启动时需外部救援使用本机备份。源码回退不等于设备启动分区回退。

## 手电筒与相机切换可选补充

[flash-camera/README.md](flash-camera/README.md) 提供 flash-check / flash-install / flash-rollback 动作，仅在当前已审核日期内核上加载模块和运行态覆盖并更新相机应用，不更换七个原 Image 分片，也不新增内核升级入口。新增 `camera-check / camera-install / camera-rollback` 动作提供[主摄自动对焦、第二后摄与增益适配](flash-camera/CAMERA-QUALITY.md)。同日仅补充 ABI 匹配模块与用户态包，不替换原 Image。GNOME Snapshot 同步硬件闪光仍未完成。
