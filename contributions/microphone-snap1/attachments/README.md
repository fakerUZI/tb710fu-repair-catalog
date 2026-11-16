# TB710FU snap1 内置麦克风修复（待审核）

适用 Lenovo TB710FU、Ubuntu ARM64、`7.2.6-tb710fu-snap1`，已配置 TDM/AW882xx 扬声器及 WirePlumber pro-audio。只接受下列已验证启动镜像和附件中匹配的引导固件/GPT 布局；其他内核、系统和镜像会拒绝。需要 Python 3.8+、Bash、alsa-utils、gdisk、fdisk、util-linux、systemd、WirePlumber。脚本不下载依赖。

## 问题与改动

原设备树只有扬声器后端。早期麦克风链路又误用 TX SoundWire DAI 0：当前驱动把第一个 DAI 注册为播放，采集从 DAI 1 开始。结果 WCD 从端上电，但主控采集端未配置，录音只出现瞬态，随后全零。

此包从精确匹配的 snap1 原镜像重建已验证的麦克风启动镜像，增加 WCD 采集链路并选择 SoundWire DAI 1。保留内核、原始 WCD 模块、后摄基线、启动菜单和电源配置。安装开机 AMIC1/ADC1/DEC0、AMIC3/ADC2/DEC1 路由及麦克风名称。模拟增益保持 12，数字增益 +12 dB；较大敲击可能削波，可在系统中降低输入音量。

## 使用

修复助手应先校验 catalog 与每个附件。**默认 `check` 只读检查**；目录条目的管理员标志使助手请求管理员权限。检查通过不等于已经安装。

独立运行（将路径换为下载后的本地路径，不依赖当前工作目录）：

```bash
sudo env TB710FU_ATTACHMENTS_DIR=/path/to/attachments \
  TB710FU_FIX_ACTION=check bash /path/to/script.sh
sudo env TB710FU_ATTACHMENTS_DIR=/path/to/attachments \
  TB710FU_FIX_ACTION=apply bash /path/to/script.sh
```

`apply` 表示明确请求修改启动分区和三个配置文件。操作前保存工作，并确保自己有可用的独立恢复办法。脚本重新读取当前固件和六个 UFS GPT，动态解析 boot_a 标签，不使用固定磁盘号或历史分区坐标。先在 `/var/backups/tb710fu-microphone-snap1/` 保存权限 700 的私有启动/GPT 信息和原配置，校验补丁重建结果，再写 boot_a 并完整回读。**不写 GPT、不调整分区、不自动重启**。成功后保存工作、自行重启，在声音设置选择“TB710FU 内置麦克风”。用户会话的默认输入/音量由 WirePlumber 管理，本安装器不跨用户修改偏好。

- 原 snap1 boot SHA256：`6a588ec97a053d85afcf2b8dd8a62c479f50c7d6ec45552036c1a25e618fdf0e`
- 修复 boot SHA256：`b2c5db7b73afebf6b83a0b786000e768186c08e27daa6a8907bf24ff5d61bda2`
- 镜像大小：100663296 字节。`boot-delta.json` 是固定 4 KiB 块差异，不是可单独刷写的启动镜像。
- 已安装同一包且文件未变时重复 apply 不重复写入；文件或镜像改变则停止。

设备树的可读差异见 `DTB-CHANGE.patch`，用于审阅；不应作为运行时覆盖层加载。它只增加音频 phandle、采集链路和音频路由，不改后摄、电源或分区配置。

## 真实验证范围

2026-11-16 在该 Ubuntu 单系统平板、上述内核上，修复后的两只麦克风连续产生变化的采样，并分别检测到扬声器播放的安静 880 Hz 声学参考信号；测试音停止后回落到环境噪声。PipeWire 双通道采集和重启后自动路由/默认输入验证通过。原 WCD 模块未替换，扬声器、后摄/对焦服务保持正常。未在本任务中另行拍摄后摄画面，未执行前摄或 Android 运行时实验，也未保存或上传语音录音。

**新目录安装包装不等同于此前的人工部署**：补丁全镜像重建与最终 SHA256、离线回归测试已通过；此安装器的完整 apply/rollback 尚未在真实块设备上重新执行。此包装的 check 已在当前修复成功的平板上通过；详见 VALIDATION.md。目录状态保持 community-review，不宣称跨设备通用，也不自动合并。

## 回退与风险

写启动分区期间断电、存储故障、设备状态变化可能导致不能启动；Python 的异常恢复不能覆盖断电/进程被强杀。启动失败需通过自己的恢复环境使用本机私有 boot-before.img，不能将备份上传。附件中的固件哈希/GPT 几何是兼容范围，不是设备私有备份。镜像补丁只发布本次硬件修复所需的已审查字节，不包含整机备份、凭据、账户、设备唯一标识或退役前摄资料。

若系统可启动，使用同一审核过的包：

```bash
sudo env TB710FU_ATTACHMENTS_DIR=/path/to/attachments \
  TB710FU_FIX_ACTION=rollback bash /path/to/script.sh
```

回退重新核实当前固件/GPT/根分区身份，只恢复本安装器最近一次 apply 前的镜像、三个配置文件和麦克风单元启用状态；配置在此后修改则拒绝覆盖。回退后自行重启。不会复原别的任务或其他历史镜像。若 apply 前已经使用修复版 boot，回退仍保留该 boot；源码回退与设备启动分区恢复是不同状态。

离线回归：`python3 attachments/test-repair.py`。包内无密码、认证令牌、设备私有日志或恢复包。

## 本轮代码审核修正

命令行不设置动作时只读 check；当前修复助手会显式传入动作，默认运行 apply，因此不能把应用的“运行脚本”按钮视为只读检查。执行前核对动作与写 boot 风险。

安装和回退均对短写作完整写入处理。回退前先验证全部原配置备份，并准备校验过的当前修复镜像；捕获回退写入异常时，会重新核对设备/GPT/固件，恢复回退前镜像与配置、保留活动记录以便重试。安装异常同样重新核对设备后恢复。正常 SIGTERM 被转为可捕获的事务错误；断电、SIGKILL、重复中断或不可恢复存储故障仍无法保证恢复。
