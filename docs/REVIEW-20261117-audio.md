# PR #15 审核记录

保留原有 WirePlumber 参数；已核对其含义与[官方 ALSA 配置文档](https://pipewire.pages.freedesktop.org/wireplumber/daemon/configuration/alsa.html)一致。`priority.driver` 是音频图时钟选择优先级，period-size/headroom 是设备周期与额外缓冲请求，不把请求 256 帧等同于硬件实际采用 256 帧。

审查发现原入口的音量/静音解析受系统语言影响、回退依赖仍存在的音频节点、音量恢复失败后没有撤销配置，且无法区分同名手工规则。增加可审阅的 Python 安装器与测试附件，统一使用 C locale，固定已验证的平台和 WirePlumber 0.5，保存首个原配置和权限；拒绝覆盖后来修改的文件；重复应用不重复重启；回退在节点消失或会话停止时仍可恢复配置。失败路径覆盖重启、节点等待、静音、通道音量和最终状态提交，并保留无法自动恢复的记录。

音频规则内容与原投稿逐字节一致。28 项隔离测试在 macOS 和 ARM64 Linux 上通过，覆盖只读检查、平台限制、首次/重复安装、原有同名规则保留、用户改动、链接拒绝、服务/音量/静音失败恢复、等待超时、会话停止与节点消失后的回退、快照损坏及状态提交失败。没有访问真实音频设备，没有在平板运行安装或回退。

测试命令：

```sh
python3 contributions/droidloom-speaker-audio/attachments/test-repair.py
```

原方案的实机观察和本次安装器的隔离测试分别保留边界，继续使用 community-review，不声明已完成整机重启、同时录音或长期稳定性验证。
