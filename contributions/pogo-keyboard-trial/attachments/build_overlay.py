#!/usr/bin/env python3
"""Build the Pogo UART and Hall overlay against an inspected TB710FU tree."""

import argparse
from pathlib import Path
import struct
import subprocess
import tempfile


SOC = "soc@0"
PINS = f"{SOC}/pinctrl@f100000"
GENI = f"{SOC}/geniqup@ac0000"
ENGINE = "a9c000"
UART = f"{GENI}/serial@{ENGINE}"
RESOURCES = ("reg", "interrupts", "clocks", "interconnects", "power-domains")


def strings(path):
    return path.read_bytes().rstrip(b"\0").decode("ascii").split("\0")


def cells(path):
    data = path.read_bytes()
    if not data or len(data) % 4:
        raise ValueError(f"设备树属性不是有效的 u32 数组：{path.name}")
    return [word[0] for word in struct.iter_unpack(">I", data)]


def render(cells_):
    return " ".join(f"0x{value:x}" for value in cells_)


def make_dts(tree):
    if "lenovo,tb710fu" not in strings(tree / "compatible"):
        raise ValueError("需要 TB710FU 的设备树")
    if (tree / UART).exists():
        raise ValueError("已存在 Pogo UART 节点，请先核对当前内核")
    i2c = tree / GENI / f"i2c@{ENGINE}"
    spi = tree / GENI / f"spi@{ENGINE}"
    if any(strings(node / "status") != ["disabled"] for node in (i2c, spi)):
        raise ValueError("串行引擎已经由其他设备使用")
    resources = {name: cells(i2c / name) for name in RESOURCES}
    if resources["reg"] != [0, int(ENGINE, 16), 0, 0x4000] or any(
            cells(spi / name) != value for name, value in resources.items()):
        raise ValueError("当前串行引擎资源与候选不一致")
    if strings(i2c / "clock-names") != ["se"] or strings(i2c / "interconnect-names") != [
            "qup-core", "qup-config", "qup-memory"]:
        raise ValueError("当前串行引擎的时钟或互连描述与候选不一致")
    gpio, = cells(tree / PINS / "phandle")
    if cells(tree / "gpio-keys/event-hall-sensor/gpios") != [gpio, 81, 1]:
        raise ValueError("找不到待迁移的 GPIO81 合盖配置")
    old_pins = cells(tree / "gpio-keys/pinctrl-0")
    properties = "\n".join(f"            {name} = <{render(value)}>;" for name, value in resources.items())
    return f'''/dts-v1/;
/plugin/;

&{{/{PINS}}} {{
    pogo_uart_default: pogo-uart-default-state {{
        mux {{
            pins = "gpio62", "gpio63";
            function = "qup1_se7";
        }};
        config {{
            pins = "gpio62", "gpio63";
            drive-strength = <2>;
            bias-disable;
        }};
    }};
    pogo_hall_default: pogo-hall-default-state {{
        pins = "gpio66", "gpio81";
        function = "gpio";
        bias-pull-up;
    }};
}};

&{{/{GENI}}} {{
    serial@{ENGINE} {{
        compatible = "qcom,geni-uart";
{properties}
        clock-names = "se";
        interconnect-names = "qup-core", "qup-config", "qup-memory";
        pinctrl-names = "default";
        pinctrl-0 = <&pogo_uart_default>;
        status = "okay";
    }};
}};

&{{/gpio-keys}} {{
    pinctrl-0 = <{render(old_pins)}>, <&pogo_hall_default>;
    event-hall-sensor {{
        gpios = <0x{gpio:x} 66 1>;
    }};
}};
'''


def compile_dtbo(dts, output):
    # The kernel rejects overlay symbols when the live tree has no symbol table.
    # Path targets need no exported symbols. /plugin/ emits the local fixups.
    with tempfile.TemporaryDirectory() as workspace:
        source = Path(workspace) / "pogo.dts"
        source.write_text(dts)
        subprocess.run(["dtc", "-I", "dts", "-O", "dtb", "-o", output, source], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", type=Path, default=Path("/sys/firmware/devicetree/base"))
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    compile_dtbo(make_dts(args.tree), args.out)


if __name__ == "__main__":
    main()
