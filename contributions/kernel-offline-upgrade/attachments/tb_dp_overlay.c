// SPDX-License-Identifier: GPL-2.0-only
/* Enable the already-described SM8650 DisplayPort before MSM binds. */
#include <linux/firmware.h>
#include <linux/module.h>
#include <linux/of.h>

static int overlay_id = -1;

static int __init tb_dp_init(void)
{
    const struct firmware *fw;
    struct device_node *node;
    int ret;

    if (!of_machine_is_compatible("lenovo,tb710fu"))
        return -ENODEV;
    node = of_find_node_by_path("/soc@0/display-subsystem@ae00000/displayport-controller@af54000");
    if (!node)
        return -ENODEV;
    if (!of_device_is_compatible(node, "qcom,sm8650-dp")) {
        of_node_put(node);
        return -ENODEV;
    }
    if (of_device_is_available(node)) {
        of_node_put(node);
        return -EALREADY;
    }
    of_node_put(node);
    ret = request_firmware_direct(&fw, "tb710fu-dp-20261117.dtbo", NULL);
    if (ret)
        return ret;
    ret = of_overlay_fdt_apply(fw->data, fw->size, &overlay_id, NULL);
    release_firmware(fw);
    if (ret && overlay_id >= 0)
        of_overlay_remove(&overlay_id);
    if (!ret)
        pr_info("TB710FU: DisplayPort enabled before MSM bind\n");
    return ret;
}

static void __exit tb_dp_exit(void)
{
    if (overlay_id >= 0)
        of_overlay_remove(&overlay_id);
}

module_init(tb_dp_init);
module_exit(tb_dp_exit);
MODULE_LICENSE("GPL");
MODULE_VERSION("tb710fu-dp-20261117.1");
MODULE_FIRMWARE("tb710fu-dp-20261117.dtbo");
MODULE_DESCRIPTION("TB710FU existing DisplayPort graph enablement");
