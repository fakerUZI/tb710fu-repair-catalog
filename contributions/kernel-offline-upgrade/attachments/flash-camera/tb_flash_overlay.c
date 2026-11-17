// SPDX-License-Identifier: GPL-2.0-only
/* Flash-only overlay; correct the OF hotplug parent before binding the PMIC LED. */
#include <linux/firmware.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/of_platform.h>
#include <linux/platform_device.h>
#include <linux/spmi.h>

#define FLASH_PATH "/soc@0/spmi@c400000/pmic@1/led-controller@ee00"
static int overlay_id = -1;
static int __init tb_flash_init(void)
{
    const struct firmware *fw;
    struct device_node *node, *parent;
    struct spmi_device *spmi;
    struct platform_device *pdev;
    int ret = request_firmware_direct(&fw, "tb-flash.dtbo", NULL);
    if (ret)
        return ret;
    ret = of_overlay_fdt_apply(fw->data, fw->size, &overlay_id, NULL);
    release_firmware(fw);
    if (ret)
        goto undo;
    node = of_find_node_by_path(FLASH_PATH);
    parent = of_get_parent(node);
    spmi = spmi_find_device_by_of_node(parent);
    pdev = of_find_device_by_node(node);
    ret = -ENODEV;
    if (!spmi || !pdev)
        goto put;
    /* OF hotplug only locates platform parents; PMIC is an SPMI device. */
    if (pdev->dev.parent != &spmi->dev) {
        if (pdev->dev.driver) {
            ret = -EBUSY;
            goto put;
        }
        ret = device_move(&pdev->dev, &spmi->dev, DPM_ORDER_DEV_LAST);
        if (ret)
            goto put;
    }
    ret = pdev->dev.driver ? 1 : device_attach(&pdev->dev);
    ret = ret > 0 ? 0 : (ret < 0 ? ret : -ENODEV);
put:
    if (pdev)
        put_device(&pdev->dev);
    spmi_device_put(spmi);
    of_node_put(parent);
    of_node_put(node);
    if (!ret)
        return 0;
undo:
    if (overlay_id >= 0)
        of_overlay_remove(&overlay_id);
    return ret;
}
static void __exit tb_flash_exit(void)
{
    int ret = of_overlay_remove(&overlay_id);
    pr_info("tb-flash-overlay: removal rc=%d\n", ret);
}
module_init(tb_flash_init);
module_exit(tb_flash_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("TB710FU PM8550 flash overlay with correct SPMI parent");
