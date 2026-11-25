# Ubuntu 启动器切换到 U-Boot（单独安装）

适用于小新 Pad Pro GT（TB710FU）单系统、GPT 根分区名 `arch`、A 槽 kShim 启动的 Ubuntu。当前只收录 `7.2.6-tb710fu-20261124` 的已验证镜像组合。20261118 等其他内核请继续使用原启动器。

在修复助手中选中此条目，下载后可以单独运行。刷新目录与下载只获取条目信息和附件。运行“安装”需要管理员授权。已有匹配 U-Boot 的平板会校验当前状态并结束。迁移完成由用户选择重启时间。

脚本核对当前固件、主备 GPT、A 槽状态、原 boot 镜像和内核模块目录，再从本机 boot_a 提取 Ubuntu 内核和基础 DTB。DTB 补入原 kShim 传递的固件和帧缓冲保留区。启动链为 U-Boot → systemd-boot → Ubuntu。分区名保留为 `arch`。

安装需要联网获取两个有固定 SHA-256 的上游包。U-Boot 的二进制差异仅用于匹配的上游镜像，全部 EFI 文件和最终 boot.img 会与已实机启动版本逐项核对。来源见 [TB710FU U-Boot 发布包](https://github.com/deinthoedoka/tb710fu-update/releases/tag/repo-aarch64) 和 [Ubuntu arm64 systemd-boot-efi 软件包](https://ports.ubuntu.com/ubuntu-ports/pool/universe/s/systemd/systemd-boot-efi_259.9-0ubuntu0.1_arm64.deb)。

写入 boot_a 会影响下一次开机。安装前准备可用的电脑端恢复通道，并保持供电。脚本会把原 96 MiB 镜像校验并保存在本机 `/var/backups/tb710fu-uboot-uefi/boot_a-kshim.img`。完成后建议将它另存到电脑，再重启。经其他途径安装 U-Boot 的设备请保留原安装流程生成的备份。

`TB710FU_FIX_ACTION=check` 只读检查适用状态；`apply` 执行迁移；`rollback` 核对当前设备、固件、U-Boot 和已备份的原 kShim 镜像后恢复 boot_a，回读通过后手动重启。原 kShim 不读取新增的 EFI 文件。

验证记录：20261124 内核的同一 U-Boot 镜像与五个 EFI 文件经实机重启验证，内核报告 `EFI v2.11 by Das U-Boot`，根分区为 `arch`，图形目标和 USB SSH 已恢复。
