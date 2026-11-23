// SPDX-License-Identifier: GPL-2.0-only
#include <linux/firmware.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/of_platform.h>
#include <linux/platform_device.h>

#define OVERLAY "tb710fu/pogo-keyboard.dtbo"

static int overlay_id;

static int __init tb_pogo_overlay_init(void)
{
	const struct firmware *fw;
	struct device_node *node;
	struct platform_device *keys;
	int ret;

	if (!of_machine_is_compatible("lenovo,tb710fu"))
		return -ENODEV;

	node = of_find_node_by_path("/gpio-keys");
	if (!node)
		return -ENODEV;
	keys = of_find_device_by_node(node);
	of_node_put(node);
	if (!keys)
		return -ENODEV;
	ret = request_firmware_direct(&fw, OVERLAY, &keys->dev);
	put_device(&keys->dev);
	if (ret)
		return ret;

	ret = of_overlay_fdt_apply(fw->data, fw->size, &overlay_id, NULL);
	release_firmware(fw);
	if (ret && overlay_id)
		of_overlay_remove(&overlay_id);

	return ret;
}

static void __exit tb_pogo_overlay_exit(void)
{
	if (overlay_id)
		of_overlay_remove(&overlay_id);
}

module_init(tb_pogo_overlay_init);
module_exit(tb_pogo_overlay_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("TB710FU Pogo UART and Hall overlay");
MODULE_FIRMWARE(OVERLAY);
