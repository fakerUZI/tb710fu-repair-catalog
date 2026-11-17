// SPDX-License-Identifier: GPL-2.0-only
/* Explicitly loaded, removable GC02M1 camera graph overlay. */
#include <linux/firmware.h>
#include <linux/module.h>
#include <linux/of.h>

static int overlay_id = -1;
static char *overlay_name = "tb-macro-id.dtbo";
module_param(overlay_name, charp, 0400);

static int __init tb_macro_registration_init(void)
{
	const struct firmware *fw;
	int ret;

	ret = request_firmware_direct(&fw, overlay_name, NULL);
	if (ret)
		return ret;
	ret = of_overlay_fdt_apply(fw->data, fw->size, &overlay_id, NULL);
	release_firmware(fw);
	if (ret) {
		if (overlay_id >= 0)
			of_overlay_remove(&overlay_id);
		return ret;
	}
	pr_info("tb-macro-registration: camera overlay applied, id=%d\n", overlay_id);
	return 0;
}

static void __exit tb_macro_registration_exit(void)
{
	int ret = of_overlay_remove(&overlay_id);
	pr_info("tb-macro-registration: removal rc=%d\n", ret);
}

module_init(tb_macro_registration_init);
module_exit(tb_macro_registration_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("TB710FU removable GC02M1 camera overlay");
