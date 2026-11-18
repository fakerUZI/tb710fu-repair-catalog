# Droidloom 42 安装、升级与《重返未来：1999》启动修复

适用于 Lenovo TB710FU 的 Ubuntu 26.04 ARM64、已登录的 systemd Wayland 桌面。适用于全新安装、Ubuntu 手工安装的 release 28 升级，以及现有 release 42 的兼容修复。已验证的平板内核为 `7.2.6-tb710fu-20261118`。

## 症状和修复

- 《重返未来：1999》4.1.0 同意协议后闪退。游戏内置 LuaJIT 先报 `bad light userdata pointer` 并退出。Droidloom 的 systemd 服务通过 `setarch --addr-compat-layout` 使用兼容内存布局，应用的默认映射进入旧版 LuaJIT 可接受的地址范围。Ubuntu 的全局 ASLR 保持启用。
- Android 虚拟 Wi-Fi 曾因联网校验失败被永久禁用自动重连。修复在原设置为空时为 Android 配置返回 HTTP 204 的小米联网校验端点，再恢复连接 `Droidloom`。新端点保留真实联网校验。
- Android 报告启动完成后，虚拟 Wi-Fi 可能尚未就绪。连接命令失败时会等待并重试，以 Android 的实际联网校验为准。最终失败时保留命令退出码和诊断信息，按原流程回退。
- 应用前检查宿主机的 root 身份，以及 `/etc/droidloom` 和现有 `cell.json` 的属主和权限。若目录被沙箱创建成 `nobody` 所有，先确认来源并保留内容，再由宿主管理员修复权限。
- 如果当前内核把 DMA heap 编成模块，脚本按需加载 `system_heap`，并登记在系统启动时加载。该设备供 Android 媒体解码使用。

## 执行方法

在修复助手里先下载和审查 Bash 入口及附件，管理员确认后应用。直接执行入口的默认动作是只读检查。

```bash
bash script.sh
sudo env TB710FU_FIX_ACTION=apply bash script.sh
sudo env TB710FU_FIX_ACTION=rollback bash script.sh
```

没有 Droidloom 时会从 [Droidloom 官方发布](https://github.com/denialwm/droidloom/releases/tag/packages) 下载 ARM64 的 release 42 运行时和 Android 镜像，合计约 980 MB。更新 release 28 使用相同的官方包。脚本固定文件长度和 SHA-256，支持续传，校验通过后才解包。Ubuntu 自带的 `unshare` 缺少 `--forward-signals` 时，复用已有可用的 Droidloom 专用副本，或从 [util-linux 官方源码](https://www.kernel.org/pub/linux/utils/util-linux/v2.42/sha256sums.asc) 构建专用版本。构建需要 GCC、make 和 libc 开发文件，脚本按需从 Ubuntu 软件源安装。缓存位于 `/var/cache/tb710fu-droidloom-r42`。

升级会关闭当前 Droidloom 应用窗口。release 28 使用新数据目录启动，原 Android 数据和应用仍保存在原目录。已配置的 release 42 使用原数据目录。此条目只安装或配置 Droidloom，游戏请使用 [深蓝互动官网](https://re.bluepoch.com/) 下载的 APK 自行安装。桌面启动器应带上实际入口 Activity，命令如下。

```bash
droidloomctl launch com.shenlan.m.reverse1999 \
  --component com.shenlan.m.reverse1999/com.ssgame.mobile.gamesdk.frame.AppStartUpActivity
```

## 风险与回退

需要管理员权限和已登录的 Wayland 桌面。运行时、Android 镜像、Droidloom systemd 配置及 Android 的联网校验设置可能变更。固件、GPT 和 Android APK 无写入。应用前请保存 Droidloom 内工作并为下载、解包和本机备份预留约 10 GB。新目录需要重新安装安卓应用，旧版存档随旧数据留在本机。安装失败会尝试恢复快照，人工回退可用上述 `rollback`。快照保存在 `/var/backups/tb710fu-droidloom-r42-repair`。再次应用时，同一用户已完成回退的快照会转存到相邻的带时间戳目录。回退保留下载缓存、新旧 Android 数据和 Ubuntu 依赖；已加载的内核模块随正常系统重启重新读取模块配置。回退联网校验端点时，虚拟 Wi-Fi 的保存记录仍保留。

如果未来通过其他渠道升级了 Droidloom，先核对本条目的回退范围。脚本拒绝覆盖未知安装以及向已替换的运行时回退。

## 验证边界

在上述平板上手动按相同步骤从 release 28 迁移至 42，重启容器后虚拟 Wi-Fi 自动重连并通过校验。官网 APK 的安装件 SHA-256 与官网下载件一致，已从桌面入口通过资源更新检查，停在约 17.9 GB 首次资源下载确认页。登录和实战待验证。本投稿入口已在平板完成只读预检。无安装分支、首次快照和下载缓存使用隔离测试。Ubuntu ARM64 容器验证了官方包的校验、解包和运行时动态依赖，以及官方源码的 `unshare` 构建。全新安装与回退仍待通过修复助手在平板上端到端验证。
