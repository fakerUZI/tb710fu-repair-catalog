// SPDX-License-Identifier: GPL-2.0-only
#include <linux/firmware.h>
#include <linux/module.h>
#include <linux/of.h>

#define OVERLAY "tb710fu/pogo-keyboard.dtbo"

static int overlay_id;

static int __init tb_pogo_overlay_init(void)
{
	const struct firmware *fw;
	int ret;

	if (!of_machine_is_compatible("lenovo,tb710fu"))
		return -ENODEV;

	/* Attaching firmware devres to the unbound gpio-keys prevents reprobe. */
	ret = request_firmware_direct(&fw, OVERLAY, NULL);
	if (ret) {
		pr_err("tb_pogo_overlay: failed to load %s: %d\n", OVERLAY, ret);
		return ret;
	}

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
