# Steam 中文与表情字体修复

在修复助手中单独选择这项修复，下载并运行。此条目不会进入「一键升级」。应用后，请完全退出并重新打开 Steam。

适用于 TB710FU 上的 Ubuntu 26.04 ARM64 和已经完成首次更新的 Steam Snap。修复使用系统已有的 `fontconfig`、`fonts-noto-cjk`、`fonts-noto-color-emoji`。缺少依赖时，可先通过 Ubuntu 软件源安装它们。

Steam 的内层运行环境缺少 CJK 字体时，中文可能显示为方框或缺字。脚本把系统的 Noto CJK 常规、粗体和 Noto Color Emoji 复制到当前用户的 `~/snap/steam/common/.local/share/fonts/tb710fu-cjk/`，占用约 50 MB。Steam 界面在重开后加载字体，脚本运行期间不会关闭 Steam。

## 检查与回退

```sh
bash script.sh
TB710FU_FIX_ACTION=apply bash script.sh
TB710FU_FIX_ACTION=rollback bash script.sh
```

命令行默认只读检查。回退移除脚本校验通过的专用字体目录。已有同名自定义文件时会保留其内容并报错。此前手动放入且与系统字体字节一致的三个文件可以由脚本接管。回退后需要完全退出并重新打开 Steam。

## 验证范围

在 Ubuntu 26.04.1、内核 `7.2.6-tb710fu-20261118` 和 Steam Snap 修订 290 上，修复前内层字体查询对中文和表情样字均退回 DejaVu Sans。加入相同字体后，在 Valve 的运行环境中验证了中文样字和表情覆盖。重开真实 Steam 客户端后确认界面进程加载了 Noto CJK 常规字体。
