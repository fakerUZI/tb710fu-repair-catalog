# Droidloom 49 对应源码

二进制基于 Droidloom 提交 `572e1c51c5caf75183efe25ad811f98c1268bf56`。宿主端使用锁定的 Cargo 依赖。在该版本源码树应用本目录两个补丁后，运行 `cargo build --locked --release -p droidloom-audio` 构建 ARM64 Ubuntu 音频服务。安卓端按该提交的 AOSP 源码清单及 Android.bp 构建 vendor ARM64 的 `android.hardware.camera.provider-service.droidloom` 与 `libdroidloom_minigbm`。本条目的桥接程序和安装代码位于相邻的 attachments 目录。

[Droidloom 完整源码及构建脚本](https://github.com/denialwm/droidloom/tree/572e1c51c5caf75183efe25ad811f98c1268bf56) · [Android 上游源码与固定修订清单](https://github.com/denialwm/droidloom/tree/572e1c51c5caf75183efe25ad811f98c1268bf56/android/manifest) · [Cargo.lock](https://github.com/denialwm/droidloom/blob/572e1c51c5caf75183efe25ad811f98c1268bf56/Cargo.lock) · [构建说明](https://github.com/denialwm/droidloom/blob/572e1c51c5caf75183efe25ad811f98c1268bf56/docs/BUILDING.md)
