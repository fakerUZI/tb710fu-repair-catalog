# BOOTFIX 时钟同步修复

在「Ubuntu 一次安装」中选择系统维护即可安装。首次同步还需要网络允许访问 NTP 的 UDP 123 端口。

修复会恢复 Ubuntu `systemd-timesyncd`，停止旧的单点 HTTP 校时。新服务按系统已有 NTP 配置工作，缺省时在阿里云、Ubuntu 和 Cloudflare 时间源之间重试。原版 BOOTFIX 使用的 `259.5-0ubuntu3.4` 版本配有已校验的 Ubuntu ARM64 软件包，可离线安装。其他版本须能从本机 Ubuntu 软件源取得与 `libsystemd-shared` 匹配的版本。已装有 chrony 等其他时钟服务的设备沿用原服务。

应用后用 `timedatectl status` 查看系统时钟是否同步。服务启动与第一次同步是两件事，接入网络后需等服务器回应。时区、DNS 和现有 Ubuntu 软件源配置均沿用系统设置。

从终端以 `TB710FU_FIX_ACTION=check tb710fu-repair-assistant` 打开助手，可执行只读检查。以 `TB710FU_FIX_ACTION=rollback tb710fu-repair-assistant` 打开后运行同一项，可恢复原先的定时器与时间同步服务配置。安装过的软件包由 APT 管理，回退仅恢复服务与本条新增配置。

离线包来自 Ubuntu `resolute-security/main`，文件为 `systemd-timesyncd_259.5-0ubuntu3.4_arm64.deb`，SHA-256 为 `7191f7cde87d64bb708dee5c9e08fa02af00b034c5dbd17146215fe216ae74c6`。已用 Ubuntu 包索引核对。
