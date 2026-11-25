# Droidloom 手写笔测试版对应源码

测试程序来自 [Droidloom 0.1.0 release 49](https://github.com/denialwm/droidloom/tree/572e1c51c5caf75183efe25ad811f98c1268bf56)，应用同目录的 `droidloom-49-pen-compat.patch`。沿用上游 GPL-3.0-or-later 许可，许可及版权声明随附。

在 ARM64 Ubuntu 26.04 上安装工具链 Rust 1.93.0 及 `libgbm-dev`、`libwayland-dev`、`libxkbcommon-dev`、`libdrm-dev`、`pkg-config` 和系统编译工具。检出上面的提交，在源码根目录运行下列命令。Cargo 依赖及校验值由上游 `Cargo.lock` 固定。

```sh
git apply /path/to/droidloom-49-pen-compat.patch
cargo test --locked -p droidloom-wayland
cargo build --locked --release -p droidloom-wayland
strip target/release/droidloom-wayland
```

补丁在窗口侧接收 Wayland tablet-v2，并以一个独立于鼠标与手指触点的标识写入现有 Droidloom 触控协议。该构建沿用 Droidloom 49 的 Android 输入 ABI 8，可与系统原版安卓镜像配合使用。

release 43–48 也使用输入 ABI 8。窗口协议会与旧版 Android Composer 协商双方支持的功能。

release 42 的输入 ABI 为 7。请从上游 release 42 提交 `df72bc9` 构建该版本，在上述同样的 ARM64 环境和工具链下，先应用共用的手写笔协议处理及 release 42 的窗口端接入补丁，再运行相同的测试和构建命令。

```sh
git apply --include='*/stylus.rs' /path/to/droidloom-49-pen-compat.patch
git apply /path/to/droidloom-42-presenter.patch
```

### release 49 安卓原生笔候选补丁

`droidloom-49-native-stylus.patch` 叠加在上述 release 49 触控兼容补丁上。它需要配套构建 Wayland 窗口程序和 Android 的 `libinputflinger.so`。窗口程序读取系统中接收库的功能标记，匹配后才发送原生笔记录。现有修复助手分发的二进制使用上一版触控通道。

在应用两份补丁的源码根目录可运行 `cargo test --locked -p droidloom-wayland -p droidloom-input-protocol -p droidloom-android-policy`。这些测试覆盖笔、手指共存、轻重压力、取消和拒收恢复。Android 系统库需使用同一 release 的上游 AOSP/Soong 构建流程生成，随后核对安装到容器的库内含 `DROIDLOOM_STYLUS_WIRE=1;`。

实机验收时，在安卓笔记软件关闭手指书写并选择压感笔刷。用笔轻写、重写、抬笔后再落笔，再用手指滑动。分别记录刚切入应用后的第一笔，以及连续书写时笔尖与墨迹的距离。切入延迟和持续跟笔延迟应分别测量。
