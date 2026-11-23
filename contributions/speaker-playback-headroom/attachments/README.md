# 内置扬声器爆音与无声修复

适用于 TB710FU 的 Ubuntu 26.04 桌面用户。需要 PipeWire、WirePlumber 0.5、Python 3、`pactl` 和用户会话的 systemd。修复助手可用当前桌面用户安装，安装前可先暂停播放和录音。

脚本仅为 `alsa_output.platform-sound.pro-output-0` 增加 WirePlumber 播放缓冲余量。实机原值为 256 帧，安装后为 960 帧。在 48 kHz 下，相当于增加约 15 毫秒的缓冲余量。应用会短暂重启 WirePlumber、确认新扬声器节点已加载规则，并恢复原来的左右声道音量和静音状态。

## 命令行检查与回退

在此条目的目录中运行以下命令。修复助手下载的附件也可以通过 `TB710FU_ATTACHMENTS_DIR` 指定。

```sh
bash script.sh
TB710FU_FIX_ACTION=apply bash script.sh
TB710FU_FIX_ACTION=rollback bash script.sh
```

无动作时执行只读检查。回退会移除内容与附件一致的规则并重启 WirePlumber。此前手动安装的相同配置可以被本条目接管。遇到内容不同的同名文件或符号链接时，脚本会退出并保留原文件。

## 实机验证

在 Ubuntu 26.04.1、内核 `7.2.6-tb710fu-20261118`、PipeWire 1.6.2 和 WirePlumber 0.5.13 上，Chrome 有播放流，但 ALSA 硬件指针停止推进。重建音频路由后恢复声音。相同的 960 帧规则加载后，45 秒内硬件指针持续推进，Chrome 的 PipeWire 错误计数为零，用户用原场景在最大音量复测时听起来大致正常。

这是用户态缓解措施。Linux 上游已修复 Q6APM 驱动在缓冲环末尾少报告一帧的问题。针对该设备的内核修复仍待实机验证。[上游修复](https://github.com/torvalds/linux/commit/0ea3e0b196f9dcc17b40ac881a6efef06459975b)
