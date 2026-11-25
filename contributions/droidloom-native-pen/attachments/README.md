# Droidloom 49 安卓原生手写笔

适用于 Lenovo TB710FU 的 Ubuntu ARM64 GNOME Wayland、已能在 Ubuntu 下使用的原装笔，以及标准 Droidloom 0.1.0 release 49。启动时窗口程序从 Wayland 接收笔尖和压力，安卓输入库分别向应用报告手写笔与手指。使用会响应压感的笔刷可以检验轻重笔迹。

在修复助手单独安装，需要管理员权限。安装器核对两个程序的完整校验值和原版系统输入库，将备份放在 `/usr/local/libexec/tb710fu-droidloom-native-pen/`。Android 输入库供本机容器共用，启动覆盖写入发起安装的桌面账号。请保存安卓应用工作后，在该账号的桌面终端运行 `droidloomctl restart`。

打开笔记软件，关闭「手指书写」，选择压感笔刷，依次轻写、重写、抬笔后重新落笔。手指可以用于笔记软件自身支持的缩放或平移。切入应用后的第一笔和连续书写时的跟笔表现可分别观察。

`TB710FU_FIX_ACTION=check` 只读核对适用条件。`TB710FU_FIX_ACTION=rollback` 恢复备份的输入库并移除本版的用户启动覆盖，重启容器后生效。后续升级 Droidloom 时，启动器检测到版本改变会使用系统程序。其他 Droidloom 版本的触控兼容范围见原测试条目。

本版基于 Droidloom 0.1.0 release 49。配套的宿主端与安卓输入库源码补丁、上游提交及许可证可在[修复仓库的源码目录](https://github.com/fakerUZI/tb710fu-repair-catalog/tree/main/contributions/droidloom-pen-compat/source)查看。笔刷对压力的响应和实际跟笔延迟仍待实体笔实测。

单屏平板启动 Droidloom 时，窗口程序使用该输出当前的刷新率初始化安卓内置显示器。多屏沿用原有的窗口所在屏幕选择逻辑。已有 release 49 原生笔修复可直接应用此版本，重启 Droidloom 后载入新的窗口程序。
