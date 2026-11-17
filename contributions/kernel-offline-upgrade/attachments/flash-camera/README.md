# 20261117 内核补充：手电筒与前后摄切换

本补充仅适用于已审核的 `7.2.6-tb710fu-20261117`、对应 boot/配置/固件/GPT。它通过匹配的外部模块和可移除 DT 覆盖启用 PM8550 LED，并更新相机 UI；不改 Image，不刷写启动、DTBO 或固件分区，内核日期保持不变。原日期版七个分片保持原来的已验证内容。

仍使用目录中第一项、唯一的内核修复入口。下载并验证该条目所有附件后：

```bash
sudo env TB710FU_ATTACHMENTS_DIR=/path/to/attachments \
  TB710FU_KERNEL_ACTION=flash-check bash /path/to/script.sh
sudo env TB710FU_ATTACHMENTS_DIR=/path/to/attachments \
  TB710FU_KERNEL_ACTION=flash-install TB710FU_KERNEL_USER=ubuntu bash /path/to/script.sh
sudo env TB710FU_ATTACHMENTS_DIR=/path/to/attachments \
  TB710FU_KERNEL_ACTION=flash-rollback bash /path/to/script.sh
```

旧内核先通过原 install 动作完成日期版升级和重启；本补充不会绕过内核、配置、boot、固件、GPT 或现有应用文件校验。所有模块必须匹配目标 vermagic。它没有添加第二套内核升级条目。

## 手电筒

当前 OEM vendor_boot/dtbo 重新读取并核验，与本机 SoC/board ID 对应的 OEM MTP DTBO 为输入证据。其 torch 通道 0、3 各 100mA，flash 各 500mA、500ms；主线绑定的通道从 1 开始，对应 `led-sources=<1 4>`。设置合计 200mA torch、1A/500ms flash 上限。普通快捷开关只请求约 100mA 总 torch 电流，默认关闭、60 秒自动关闭，重复开启请求不会续期。

主线驱动用同一个 LED class 的 brightness 提供 torch、flash_* 提供闪光功能，不需要按 issue 示例猜造两个不同 LED 子节点。运行态设备是 `/sys/class/leds/tb710fu:flash:rear`。模块服务明确加载驱动，解决设备禁用/无子节点/未加载的问题；OF 热插拔给 SPMI 子设备选错 platform 父设备时，覆盖模块将其归到真正 PMIC，再绑定获取 regmap。没有 kexec、分区刷写或裸 PMIC 寄存器测试。

另提供 V4L2 flash 桥接模块，并修正 qcom flash 驱动移除/失败清理时的越界索引：先递减有效数量，再释放对应项。只在外部模块编译时打开桥接声明，不改变运行 Image 的嵌入配置或全局内核 ABI。不是在未匹配的内核上强行改 vermagic；必须使用对应已准备构建树/完整配置/符号表，目录中含 GPL 源码与 Makefile。

GNOME 50 快捷设置新增“手电筒”开关。系统 D-Bus 服务只允许 root 或已验证的活动本机图形会话控制；SSH/非活动/远程会话不能直接写灯。亮度 sysfs 不改成全局可写。服务退出、休眠或所属会话锁定时设计为关闭，最长 60 秒。服务及扩展均在正常桌面登录后使用；当前测试时平板处于 GDM，磁贴的实际点击仍需图形会话验收。

## 相机

“后置相机”入口更新为“相机（前后摄）”，顶部提供“后摄/前摄”下拉选择。原前摄入口也进入同一应用的前摄模式，第二次启动会把现有窗口切到所选摄像头，不重复抢占设备。

切换会关闭旧预览、清理缓存图像和延迟对焦，重新按目标摄像头配置预览；前摄隐藏手动对焦，后摄原缩放、拖动、对焦和拍照逻辑保留。拍照时禁止切换，保留至少三帧预热、全黑 JPEG 拒绝、尺寸/完整性校验和私有临时文件原子保存。SoftISP 实际可协商的全幅 RGB/JPEG 为两摄均 4200×3120；不能把 RAW 的 4208 宽度直接作为 RGB caps。

实机在独立图形会话测试“后→前→后→前”，四次均有预览并保存内容非空的 4200×3120 JPEG，测试临时照片已清理，没有改用户已有照片。前摄色彩/镜头参数仍未校准。

## 验证边界

- 新增 8 项 torch 定时/故障测试、9 项安装/回退/已审核修订升级测试通过；原目录测试继续通过。
- LED 注册、实际驱动绑定、低亮度开关及 fault 为空通过。D-Bus 管理员开关通过，普通 SSH 写入被拒绝；实际自动熄灭为 60.3 秒。
- 后摄只采样亮度、不保存图像的开关对照：关闭均值 71.745，开启 166.052，再关闭回落到 97.180；开关后瞬态与曝光调整存在，但观察到明显光学响应。用户直接目视确认仍未收到。
- 相机四次切换/拍照、前摄入口直接启动（130 帧预览及一次全幅拍照）、原黑帧保存回归通过。模块从卸载到重新加载、服务启停、重复安装及保留首次快照通过。
- 正常重启后，硬件与控制服务自动 active、ConditionResult=yes、Result=success，LED 初始为 0。再次开关成功，前摄 10 帧 RGB/后摄三帧 RAW 通过，系统无失败单元，boot_a/boot_b 哈希保持不变。
- 通过现有第一项 Bash 入口实际执行 flash-check/flash-install，配套与现场校验通过，正式桌面入口为“相机（前后摄）”。
- 长时间温升、真正挂起恢复、普通用户图形会话里的磁贴点击未完成，不声称覆盖所有电池/热状态。
- **GNOME Snapshot 的相机同步硬件闪光尚未接通。** 注册 V4L2 子设备不等于完成 CAMSS/传感器闪光关联或应用控制。[Snapshot 50 的 FlashBin](https://raw.githubusercontent.com/GNOME/snapshot/50.0/src/widgets/flash_bin.rs) 是界面动画，不能以它当作硬件闪光证据。issue #18 保持打开，不能当作全部验收完成。

## 回退和后续内核

首次快照 `/var/backups/tb710fu-flash-camera-20261117/` 保留原应用、桌面入口、文件、服务和扩展状态。后续已审核修订不会覆盖这些原始数据；未知用户改动或损坏备份会在回退前阻止覆盖。恢复脚本及完整配套持久化在该目录的 payload 链接下，删除下载目录后仍可：

```bash
sudo python3 /var/backups/tb710fu-flash-camera-20261117/payload/install-flash-camera.py rollback --user ubuntu
```

回退先关灯、停止服务并卸载覆盖，恢复文件与扩展设置；不删除照片或原始快照。私有前摄模块不在此补充中，也不被改动。

服务有精确的 ConditionKernelVersion；下次日期内核升级时需重编/适配此补充，不能加载旧模块。未来完整内核可按新发布日期将 V4L2 flash 配置和驱动修正纳入新 Image；日期必须晚于当前 20261117。当前七个已发布 Image 分片不因本补充而被静默换包。
