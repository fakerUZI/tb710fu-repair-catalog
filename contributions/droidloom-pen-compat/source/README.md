# Droidloom 49 对应源码

测试程序来自 [Droidloom 0.1.0 release 49](https://github.com/denialwm/droidloom/tree/572e1c51c5caf75183efe25ad811f98c1268bf56)，应用同目录的 `droidloom-49-pen-compat.patch`。沿用上游 GPL-3.0-or-later 许可，许可及版权声明随附。

在 ARM64 Ubuntu 26.04 上安装工具链 Rust 1.93.0 及 `libgbm-dev`、`libwayland-dev`、`libxkbcommon-dev`、`libdrm-dev`、`pkg-config` 和系统编译工具。检出上面的提交，在源码根目录运行下列命令。Cargo 依赖及校验值由上游 `Cargo.lock` 固定。

```sh
git apply /path/to/droidloom-49-pen-compat.patch
cargo test --locked -p droidloom-wayland
cargo build --locked --release -p droidloom-wayland
strip target/release/droidloom-wayland
```

补丁在窗口侧接收 Wayland tablet-v2，并以一个独立于鼠标与手指触点的标识写入现有 Droidloom 触控协议。该构建沿用 Droidloom 49 的 Android 输入 ABI 8，可与系统原版安卓镜像配合使用。
