# TB710FU 完整虚拟键盘

适用于 TB710FU、Ubuntu 26.04 ARM64 和 GNOME Shell 50 Wayland。需要已安装并配置的 Fcitx5、WowType 和 TB710FU 的 `tb-fcitx5.service`。

这一条目安装完整的七个虚拟键盘扩展：输入策略、快捷键与预编辑、键盘开关、WowType 触摸适配、主题与语音浮条、Kimpanel 和退格回退。包含 Fcitx5 显示环境启动配置、Kimpanel 各模块、本地化与 schema。全扩展 text 页的逗号、句号跟随中英文输入法，两个符号页有 Tab、方向键、Shift 换页、中英文标点、括号和比较符号。

Git 的 `source/` 可逐文件审查，应用所需文件都在 `attachments/`。安装时校验全部附件，并生成 Kimpanel 的编译 schema。快捷栏包含会话内剪贴板历史和本地截图入口。

## 使用和回退

在修复助手选择「完整虚拟键盘」。也可在本条目目录运行 Bash 入口：

```bash
bash script.sh
TB710FU_FIX_ACTION=apply bash script.sh
TB710FU_FIX_ACTION=rollback bash script.sh
```

第一条为检查。应用会替换当前账号的同名键盘扩展、Fcitx5 会话启动配置和预编辑开关。本机私有快照位于 `$XDG_STATE_HOME/tb710fu-virtual-keyboard/`，默认在 `~/.local/state/`。应用或回退后请保存工作，然后注销并重新登录。若修复后另有本地改动，回退会停下来保留改动。

Fcitx5 和 IBus libpinyin 修复是不同输入法方案。使用此条目前应恢复 Fcitx5 与 WowType。旧的独立候选区修复也会修改部分同名文件，请使用各自的回退入口完成切换。

## 验证

已核对 TB710FU / GNOME Shell 50.1 使用的七个扩展。中文、英文键帽跟随与实际标点输出已在平板实测。整包的检查、应用、失败恢复与回退在隔离目录验证，平板核对了现有组件。通过修复助手应用后重新登录的完整实机流程仍待验证。许可和第三方来源见 `source/licenses/`。
