// SPDX-License-Identifier: GPL-2.0-only
/* GalaxyCore GC02M1_RAW, 24 MHz input, one-lane RAW10. */

#include <linux/clk.h>
#include <linux/delay.h>
#include <linux/gpio/consumer.h>
#include <linux/i2c.h>
#include <linux/module.h>
#include <linux/pm_runtime.h>
#include <linux/regulator/consumer.h>

#include <media/v4l2-cci.h>
#include <media/v4l2-ctrls.h>
#include <media/v4l2-fwnode.h>
#include <media/v4l2-subdev.h>

#define GC02M1_RAW_WIDTH 1600
#define GC02M1_RAW_HEIGHT 1200
#define GC02M1_RAW_HTS 2192
#define GC02M1_RAW_VTS 1268
#define GC02M1_RAW_EXPOSURE_MAX (GC02M1_RAW_VTS - 16)
#define GC02M1_RAW_CODE MEDIA_BUS_FMT_SBGGR10_1X10
#define GC02M1_RAW_PIXEL_RATE 84000000

static const s64 gc02m1_raw_link_freq[] = { 336000000 };
static const char * const gc02m1_raw_supply_names[] = { "vreg_l4b_1p8", "vreg_l15b_1p8", "vreg_l14b_3p2" };

struct gc02m1_raw {
	struct v4l2_subdev sd;
	struct media_pad pad;
	struct v4l2_ctrl_handler ctrls;
	struct regmap *regmap;
	struct clk *xclk;
	struct gpio_desc *reset;
	struct regulator_bulk_data supplies[ARRAY_SIZE(gc02m1_raw_supply_names)];
	bool streaming;
};

static struct gc02m1_raw *to_gc02m1_raw(struct v4l2_subdev *sd)
{
	return container_of(sd, struct gc02m1_raw, sd);
}

static const struct cci_reg_sequence gc02m1_raw_init_regs[] = {
	{ CCI_REG8(0xfe), 0x00 },
};

static const struct cci_reg_sequence gc02m1_raw_3264x2448_regs[] = {
	{ CCI_REG8(0xfc), 0x01 },
	{ CCI_REG8(0xf4), 0x41 },
	{ CCI_REG8(0xf5), 0xe3 },
	{ CCI_REG8(0xf6), 0x44 },
	{ CCI_REG8(0xf8), 0x38 },
	{ CCI_REG8(0xf9), 0x82 },
	{ CCI_REG8(0xfa), 0x00 },
	{ CCI_REG8(0xfd), 0x80 },
	{ CCI_REG8(0xfc), 0x81 },
	{ CCI_REG8(0xfe), 0x03 },
	{ CCI_REG8(0x01), 0x0b },
	{ CCI_REG8(0xf7), 0x01 },
	{ CCI_REG8(0xfc), 0x80 },
	{ CCI_REG8(0xfc), 0x80 },
	{ CCI_REG8(0xfc), 0x80 },
	{ CCI_REG8(0xfc), 0x8e },
	{ CCI_REG8(0xfe), 0x00 },
	{ CCI_REG8(0x87), 0x09 },
	{ CCI_REG8(0xee), 0x72 },
	{ CCI_REG8(0xfe), 0x01 },
	{ CCI_REG8(0x8c), 0x90 },
	{ CCI_REG8(0xfe), 0x00 },
	{ CCI_REG8(0x90), 0x00 },
	{ CCI_REG8(0x03), 0x04 },
	{ CCI_REG8(0x04), 0x7d },
	{ CCI_REG8(0x41), 0x04 },
	{ CCI_REG8(0x42), 0xf4 },
	{ CCI_REG8(0x05), 0x04 },
	{ CCI_REG8(0x06), 0x48 },
	{ CCI_REG8(0x07), 0x00 },
	{ CCI_REG8(0x08), 0x18 },
	{ CCI_REG8(0x9d), 0x18 },
	{ CCI_REG8(0x09), 0x00 },
	{ CCI_REG8(0x0a), 0x02 },
	{ CCI_REG8(0x0d), 0x04 },
	{ CCI_REG8(0x0e), 0xbc },
	{ CCI_REG8(0x17), 0x83 },
	{ CCI_REG8(0x19), 0x04 },
	{ CCI_REG8(0x24), 0x00 },
	{ CCI_REG8(0x56), 0x20 },
	{ CCI_REG8(0x5b), 0x00 },
	{ CCI_REG8(0x5e), 0x01 },
	{ CCI_REG8(0x21), 0x3c },
	{ CCI_REG8(0x44), 0x20 },
	{ CCI_REG8(0xcc), 0x01 },
	{ CCI_REG8(0x1a), 0x04 },
	{ CCI_REG8(0x1f), 0x11 },
	{ CCI_REG8(0x27), 0x30 },
	{ CCI_REG8(0x2b), 0x00 },
	{ CCI_REG8(0x33), 0x00 },
	{ CCI_REG8(0x53), 0x90 },
	{ CCI_REG8(0xe6), 0x50 },
	{ CCI_REG8(0x39), 0x07 },
	{ CCI_REG8(0x43), 0x04 },
	{ CCI_REG8(0x46), 0x4a },
	{ CCI_REG8(0x7c), 0xa0 },
	{ CCI_REG8(0xd0), 0xbe },
	{ CCI_REG8(0xd1), 0x60 },
	{ CCI_REG8(0xd2), 0x40 },
	{ CCI_REG8(0xd3), 0xf3 },
	{ CCI_REG8(0xde), 0x1d },
	{ CCI_REG8(0xcd), 0x05 },
	{ CCI_REG8(0xce), 0x6f },
	{ CCI_REG8(0xfc), 0x88 },
	{ CCI_REG8(0xfe), 0x10 },
	{ CCI_REG8(0xfe), 0x00 },
	{ CCI_REG8(0xfc), 0x8e },
	{ CCI_REG8(0xfe), 0x00 },
	{ CCI_REG8(0xfe), 0x00 },
	{ CCI_REG8(0xfe), 0x00 },
	{ CCI_REG8(0xfe), 0x00 },
	{ CCI_REG8(0xfc), 0x88 },
	{ CCI_REG8(0xfe), 0x10 },
	{ CCI_REG8(0xfe), 0x00 },
	{ CCI_REG8(0xfc), 0x8e },
	{ CCI_REG8(0xfe), 0x04 },
	{ CCI_REG8(0xe0), 0x01 },
	{ CCI_REG8(0xfe), 0x00 },
	{ CCI_REG8(0xfe), 0x01 },
	{ CCI_REG8(0x53), 0x54 },
	{ CCI_REG8(0x87), 0x53 },
	{ CCI_REG8(0x89), 0x03 },
	{ CCI_REG8(0xfe), 0x00 },
	{ CCI_REG8(0xb0), 0x74 },
	{ CCI_REG8(0xb1), 0x04 },
	{ CCI_REG8(0xb2), 0x00 },
	{ CCI_REG8(0xb6), 0x00 },
	{ CCI_REG8(0xfe), 0x04 },
	{ CCI_REG8(0xd8), 0x00 },
	{ CCI_REG8(0xc0), 0x40 },
	{ CCI_REG8(0xc0), 0x00 },
	{ CCI_REG8(0xc0), 0x00 },
	{ CCI_REG8(0xc0), 0x00 },
	{ CCI_REG8(0xc0), 0x60 },
	{ CCI_REG8(0xc0), 0x00 },
	{ CCI_REG8(0xc0), 0xc0 },
	{ CCI_REG8(0xc0), 0x2a },
	{ CCI_REG8(0xc0), 0x80 },
	{ CCI_REG8(0xc0), 0x00 },
	{ CCI_REG8(0xc0), 0x00 },
	{ CCI_REG8(0xc0), 0x40 },
	{ CCI_REG8(0xc0), 0xa0 },
	{ CCI_REG8(0xc0), 0x00 },
	{ CCI_REG8(0xc0), 0x90 },
	{ CCI_REG8(0xc0), 0x19 },
	{ CCI_REG8(0xc0), 0xc0 },
	{ CCI_REG8(0xc0), 0x00 },
	{ CCI_REG8(0xc0), 0xd0 },
	{ CCI_REG8(0xc0), 0x2f },
	{ CCI_REG8(0xc0), 0xe0 },
	{ CCI_REG8(0xc0), 0x00 },
	{ CCI_REG8(0xc0), 0x90 },
	{ CCI_REG8(0xc0), 0x39 },
	{ CCI_REG8(0xc0), 0x00 },
	{ CCI_REG8(0xc0), 0x01 },
	{ CCI_REG8(0xc0), 0x20 },
	{ CCI_REG8(0xc0), 0x04 },
	{ CCI_REG8(0xc0), 0x20 },
	{ CCI_REG8(0xc0), 0x01 },
	{ CCI_REG8(0xc0), 0xe0 },
	{ CCI_REG8(0xc0), 0x0f },
	{ CCI_REG8(0xc0), 0x40 },
	{ CCI_REG8(0xc0), 0x01 },
	{ CCI_REG8(0xc0), 0xe0 },
	{ CCI_REG8(0xc0), 0x1a },
	{ CCI_REG8(0xc0), 0x60 },
	{ CCI_REG8(0xc0), 0x01 },
	{ CCI_REG8(0xc0), 0x20 },
	{ CCI_REG8(0xc0), 0x25 },
	{ CCI_REG8(0xc0), 0x80 },
	{ CCI_REG8(0xc0), 0x01 },
	{ CCI_REG8(0xc0), 0xa0 },
	{ CCI_REG8(0xc0), 0x2c },
	{ CCI_REG8(0xc0), 0xa0 },
	{ CCI_REG8(0xc0), 0x01 },
	{ CCI_REG8(0xc0), 0xe0 },
	{ CCI_REG8(0xc0), 0x32 },
	{ CCI_REG8(0xc0), 0xc0 },
	{ CCI_REG8(0xc0), 0x01 },
	{ CCI_REG8(0xc0), 0x20 },
	{ CCI_REG8(0xc0), 0x38 },
	{ CCI_REG8(0xc0), 0xe0 },
	{ CCI_REG8(0xc0), 0x01 },
	{ CCI_REG8(0xc0), 0x60 },
	{ CCI_REG8(0xc0), 0x3c },
	{ CCI_REG8(0xc0), 0x00 },
	{ CCI_REG8(0xc0), 0x02 },
	{ CCI_REG8(0xc0), 0xa0 },
	{ CCI_REG8(0xc0), 0x40 },
	{ CCI_REG8(0xc0), 0x80 },
	{ CCI_REG8(0xc0), 0x02 },
	{ CCI_REG8(0xc0), 0x18 },
	{ CCI_REG8(0xc0), 0x5c },
	{ CCI_REG8(0xfe), 0x00 },
	{ CCI_REG8(0x9f), 0x10 },
	{ CCI_REG8(0xfe), 0x00 },
	{ CCI_REG8(0x26), 0x20 },
	{ CCI_REG8(0xfe), 0x01 },
	{ CCI_REG8(0x40), 0x22 },
	{ CCI_REG8(0x46), 0x7f },
	{ CCI_REG8(0x49), 0x0f },
	{ CCI_REG8(0x4a), 0xf0 },
	{ CCI_REG8(0xfe), 0x04 },
	{ CCI_REG8(0x14), 0x80 },
	{ CCI_REG8(0x15), 0x80 },
	{ CCI_REG8(0x16), 0x80 },
	{ CCI_REG8(0x17), 0x83 },
	{ CCI_REG8(0xfe), 0x01 },
	{ CCI_REG8(0x41), 0x20 },
	{ CCI_REG8(0x4c), 0x00 },
	{ CCI_REG8(0x4d), 0x0c },
	{ CCI_REG8(0x44), 0x08 },
	{ CCI_REG8(0x48), 0x03 },
	{ CCI_REG8(0xfe), 0x01 },
	{ CCI_REG8(0x90), 0x01 },
	{ CCI_REG8(0x91), 0x00 },
	{ CCI_REG8(0x92), 0x06 },
	{ CCI_REG8(0x93), 0x00 },
	{ CCI_REG8(0x94), 0x06 },
	{ CCI_REG8(0x95), 0x04 },
	{ CCI_REG8(0x96), 0xb0 },
	{ CCI_REG8(0x97), 0x06 },
	{ CCI_REG8(0x98), 0x40 },
	{ CCI_REG8(0xfe), 0x03 },
	{ CCI_REG8(0x01), 0x23 },
	{ CCI_REG8(0x03), 0xce },
	{ CCI_REG8(0x04), 0x48 },
	{ CCI_REG8(0x15), 0x00 },
	{ CCI_REG8(0x21), 0x10 },
	{ CCI_REG8(0x22), 0x05 },
	{ CCI_REG8(0x23), 0x20 },
	{ CCI_REG8(0x25), 0x20 },
	{ CCI_REG8(0x26), 0x08 },
	{ CCI_REG8(0x29), 0x06 },
	{ CCI_REG8(0x2a), 0x0a },
	{ CCI_REG8(0x2b), 0x08 },
	{ CCI_REG8(0xfe), 0x01 },
	{ CCI_REG8(0x8c), 0x10 },
	{ CCI_REG8(0xfe), 0x00 },
	{ CCI_REG8(0x3e), 0x00 },
};

static int gc02m1_raw_power_on(struct device *dev)
{
 struct gc02m1_raw *s=to_gc02m1_raw(dev_get_drvdata(dev));
 const int volts[]={1800000,1800000,2800000};
 int i,ret;
 for(i=0;i<ARRAY_SIZE(s->supplies);i++)
  if(regulator_get_voltage(s->supplies[i].consumer)!=volts[i])return -EINVAL;
 gpiod_set_value_cansleep(s->reset,1);
 for(i=0;i<ARRAY_SIZE(s->supplies);i++) {
  ret=regulator_enable(s->supplies[i].consumer);if(ret)goto undo;fsleep(1000);
 }
 gpiod_set_value_cansleep(s->reset,0);fsleep(1000);
 ret=clk_prepare_enable(s->xclk);if(ret)goto undo;fsleep(5000);return 0;
undo:
 gpiod_set_value_cansleep(s->reset,1);
 while(i--)regulator_disable(s->supplies[i].consumer);
 return ret;
}
static int gc02m1_raw_power_off(struct device *dev)
{
 struct gc02m1_raw *s=to_gc02m1_raw(dev_get_drvdata(dev));int i;
 clk_disable_unprepare(s->xclk);gpiod_set_value_cansleep(s->reset,1);fsleep(1000);
 for(i=ARRAY_SIZE(s->supplies)-1;i>=0;i--)regulator_disable(s->supplies[i].consumer);
 return 0;
}
static int gc02m1_raw_set_ctrl(struct v4l2_ctrl *ctrl)
{
 struct gc02m1_raw *s=container_of(ctrl->handler,struct gc02m1_raw,ctrls);
 int ret=0;
 if(ctrl->id!=V4L2_CID_EXPOSURE && ctrl->id!=V4L2_CID_ANALOGUE_GAIN)return 0;
 ret=pm_runtime_get_if_in_use(s->sd.dev);if(ret<=0)return ret;ret=0;
 cci_write(s->regmap,CCI_REG8(0xfe),0,&ret);
 if(ctrl->id==V4L2_CID_EXPOSURE) {
  cci_write(s->regmap,CCI_REG8(0x03),(ctrl->val>>8)&0x3f,&ret);
  cci_write(s->regmap,CCI_REG8(0x04),ctrl->val&0xff,&ret);
 } else {
  static const int gains[]={1024,1536,2035,2519,3165,3626,4147,4593,5095,5697,6270,6714,7210,7686,8214,10337};
  int i=ARRAY_SIZE(gains)-1,fine;
  while(i>0 && ctrl->val<gains[i])i--;
  fine=ctrl->val*1024/gains[i];
  cci_write(s->regmap,CCI_REG8(0xb6),i,&ret);
  cci_write(s->regmap,CCI_REG8(0xb1),(fine>>8)&0x3f,&ret);
  cci_write(s->regmap,CCI_REG8(0xb2),fine&0xff,&ret);
 }
 pm_runtime_put(s->sd.dev);return ret;
}

static const struct v4l2_ctrl_ops gc02m1_raw_ctrl_ops = {
	.s_ctrl = gc02m1_raw_set_ctrl,
};

static int gc02m1_raw_init_controls(struct gc02m1_raw *sensor)
{
	struct v4l2_ctrl_handler *hdl = &sensor->ctrls;
	struct v4l2_fwnode_device_properties props;
	struct v4l2_ctrl *ctrl;
	int ret;

	ret = v4l2_fwnode_device_parse(sensor->sd.dev, &props);
	if (ret)
		return ret;
	v4l2_ctrl_handler_init(hdl, 8);
	ctrl = v4l2_ctrl_new_int_menu(hdl, NULL, V4L2_CID_LINK_FREQ, 0, 0,
				      gc02m1_raw_link_freq);
	if (ctrl)
		ctrl->flags |= V4L2_CTRL_FLAG_READ_ONLY;
	v4l2_ctrl_new_std(hdl, NULL, V4L2_CID_PIXEL_RATE,
			  GC02M1_RAW_PIXEL_RATE, GC02M1_RAW_PIXEL_RATE, 1, GC02M1_RAW_PIXEL_RATE);
	ctrl = v4l2_ctrl_new_std(hdl, NULL, V4L2_CID_HBLANK,
				 GC02M1_RAW_HTS - GC02M1_RAW_WIDTH, GC02M1_RAW_HTS - GC02M1_RAW_WIDTH,
				 1, GC02M1_RAW_HTS - GC02M1_RAW_WIDTH);
	if (ctrl)
		ctrl->flags |= V4L2_CTRL_FLAG_READ_ONLY;
	ctrl = v4l2_ctrl_new_std(hdl, NULL, V4L2_CID_VBLANK,
				 GC02M1_RAW_VTS - GC02M1_RAW_HEIGHT, GC02M1_RAW_VTS - GC02M1_RAW_HEIGHT,
				 1, GC02M1_RAW_VTS - GC02M1_RAW_HEIGHT);
	if (ctrl)
		ctrl->flags |= V4L2_CTRL_FLAG_READ_ONLY;
	v4l2_ctrl_new_std(hdl, &gc02m1_raw_ctrl_ops, V4L2_CID_EXPOSURE,
			  4, GC02M1_RAW_EXPOSURE_MAX, 1, 1149);
	v4l2_ctrl_new_std(hdl, &gc02m1_raw_ctrl_ops, V4L2_CID_ANALOGUE_GAIN,
			  1024, 12288, 1, 1024);
	v4l2_ctrl_new_fwnode_properties(hdl, &gc02m1_raw_ctrl_ops, &props);
	if (hdl->error) {
		ret = hdl->error;
		v4l2_ctrl_handler_free(hdl);
		return ret;
	}
	sensor->sd.ctrl_handler = hdl;
	return 0;
}

static int gc02m1_raw_set_fmt(struct v4l2_subdev *sd,
			  struct v4l2_subdev_state *state,
			  struct v4l2_subdev_format *format)
{
	struct gc02m1_raw *sensor = to_gc02m1_raw(sd);

	if (format->pad)
		return -EINVAL;
	if (format->which == V4L2_SUBDEV_FORMAT_ACTIVE && sensor->streaming)
		return -EBUSY;

	format->format = (struct v4l2_mbus_framefmt) {
		.width = GC02M1_RAW_WIDTH,
		.height = GC02M1_RAW_HEIGHT,
		.code = GC02M1_RAW_CODE,
		.field = V4L2_FIELD_NONE,
		.colorspace = V4L2_COLORSPACE_RAW,
		.ycbcr_enc = V4L2_YCBCR_ENC_DEFAULT,
		.quantization = V4L2_QUANTIZATION_FULL_RANGE,
		.xfer_func = V4L2_XFER_FUNC_NONE,
	};
	*v4l2_subdev_state_get_format(state, 0) = format->format;
	return 0;
}

static int gc02m1_raw_init_state(struct v4l2_subdev *sd,
			     struct v4l2_subdev_state *state)
{
	struct v4l2_subdev_format format = { .which = V4L2_SUBDEV_FORMAT_TRY };

	return gc02m1_raw_set_fmt(sd, state, &format);
}

static int gc02m1_raw_enum_code(struct v4l2_subdev *sd,
			    struct v4l2_subdev_state *state,
			    struct v4l2_subdev_mbus_code_enum *code)
{
	if (code->pad || code->index)
		return -EINVAL;
	code->code = GC02M1_RAW_CODE;
	return 0;
}

static int gc02m1_raw_enum_size(struct v4l2_subdev *sd,
			    struct v4l2_subdev_state *state,
			    struct v4l2_subdev_frame_size_enum *size)
{
	if (size->pad || size->index || size->code != GC02M1_RAW_CODE)
		return -EINVAL;
	size->min_width = GC02M1_RAW_WIDTH;
	size->max_width = GC02M1_RAW_WIDTH;
	size->min_height = GC02M1_RAW_HEIGHT;
	size->max_height = GC02M1_RAW_HEIGHT;
	return 0;
}

static int gc02m1_raw_get_selection(struct v4l2_subdev *sd,
				struct v4l2_subdev_state *state,
				struct v4l2_subdev_selection *sel)
{
	if (sel->pad)
		return -EINVAL;
	switch (sel->target) {
	case V4L2_SEL_TGT_NATIVE_SIZE:
	case V4L2_SEL_TGT_CROP:
	case V4L2_SEL_TGT_CROP_DEFAULT:
	case V4L2_SEL_TGT_CROP_BOUNDS:
		sel->r = (struct v4l2_rect) {
			.width = GC02M1_RAW_WIDTH,
			.height = GC02M1_RAW_HEIGHT,
		};
		return 0;
	default:
		return -EINVAL;
	}
}

static int gc02m1_raw_s_stream(struct v4l2_subdev *sd, int enable)
{
	struct gc02m1_raw *sensor = to_gc02m1_raw(sd);
	struct v4l2_subdev_state *state;
	int ret = 0;

	state = v4l2_subdev_lock_and_get_active_state(sd);
	if (sensor->streaming == !!enable)
		goto unlock;
	if (!enable) {
		ret = cci_write(sensor->regmap, CCI_REG8(0x3e), 0, NULL);
		sensor->streaming = false;
		/* Reset and remove power even if the stream-off write failed. */
		pm_runtime_put_sync(sd->dev);
		goto unlock;
	}

	ret = pm_runtime_resume_and_get(sd->dev);
	if (ret < 0)
		goto unlock;
	cci_multi_reg_write(sensor->regmap, gc02m1_raw_init_regs,
			    ARRAY_SIZE(gc02m1_raw_init_regs), &ret);
	cci_multi_reg_write(sensor->regmap, gc02m1_raw_3264x2448_regs,
			    ARRAY_SIZE(gc02m1_raw_3264x2448_regs), &ret);
	if (!ret)
		ret = __v4l2_ctrl_handler_setup(&sensor->ctrls);
	cci_write(sensor->regmap, CCI_REG8(0x3e), 0x90, &ret);
	if (ret)
		pm_runtime_put_sync(sd->dev);
	else
		sensor->streaming = true;
unlock:
	v4l2_subdev_unlock_state(state);
	return ret;
}

static const struct v4l2_subdev_video_ops gc02m1_raw_video_ops = {
	.s_stream = gc02m1_raw_s_stream,
};

static const struct v4l2_subdev_pad_ops gc02m1_raw_pad_ops = {
	.enum_mbus_code = gc02m1_raw_enum_code,
	.enum_frame_size = gc02m1_raw_enum_size,
	.get_fmt = v4l2_subdev_get_fmt,
	.set_fmt = gc02m1_raw_set_fmt,
	.get_selection = gc02m1_raw_get_selection,
};

static const struct v4l2_subdev_ops gc02m1_raw_ops = {
	.video = &gc02m1_raw_video_ops,
	.pad = &gc02m1_raw_pad_ops,
};

static const struct v4l2_subdev_internal_ops gc02m1_raw_internal_ops = {
	.init_state = gc02m1_raw_init_state,
};

static int gc02m1_raw_check_endpoint(struct device *dev)
{
	struct v4l2_fwnode_endpoint bus = { .bus_type = V4L2_MBUS_CSI2_DPHY };
	struct fwnode_handle *ep;
	unsigned long bitmap;
	int ret, i;

	ep = fwnode_graph_get_next_endpoint(dev_fwnode(dev), NULL);
	if (!ep)
		return -EINVAL;
	ret = v4l2_fwnode_endpoint_alloc_parse(ep, &bus);
	fwnode_handle_put(ep);
	if (ret)
		goto free;
	ret = -EINVAL;
	if (bus.bus.mipi_csi2.num_data_lanes != 1)
		goto free;
	for (i = 0; i < 1; i++)
		if (bus.bus.mipi_csi2.data_lanes[i] != i + 1)
			goto free;
	ret = v4l2_link_freq_to_bitmap(dev, bus.link_frequencies,
				       bus.nr_of_link_frequencies, gc02m1_raw_link_freq,
				       ARRAY_SIZE(gc02m1_raw_link_freq), &bitmap);
free:
	v4l2_fwnode_endpoint_free(&bus);
	return ret;
}

static int gc02m1_raw_probe(struct i2c_client *client)
{
	struct device *dev = &client->dev;
	struct gc02m1_raw *sensor;
	u64 id;
	int ret, i;

	ret = gc02m1_raw_check_endpoint(dev);
	if (ret)
		return dev_err_probe(dev, ret, "Unsupported CSI-2 endpoint\n");
	sensor = devm_kzalloc(dev, sizeof(*sensor), GFP_KERNEL);
	if (!sensor)
		return -ENOMEM;
	sensor->regmap = devm_cci_regmap_init_i2c(client, 8);
	if (IS_ERR(sensor->regmap))
		return dev_err_probe(dev, PTR_ERR(sensor->regmap), "Failed to create regmap\n");
	sensor->xclk = devm_v4l2_sensor_clk_get(dev, NULL);
	if (IS_ERR(sensor->xclk))
		return dev_err_probe(dev, PTR_ERR(sensor->xclk), "Failed to get sensor clock\n");
	if (clk_get_rate(sensor->xclk) != 24000000)
		return dev_err_probe(dev, -EINVAL, "Only a 24 MHz input clock is supported\n");
	for (i = 0; i < ARRAY_SIZE(sensor->supplies); i++)
		sensor->supplies[i].supply = gc02m1_raw_supply_names[i];
	ret = devm_regulator_bulk_get(dev, ARRAY_SIZE(sensor->supplies), sensor->supplies);
	if (ret)
		return dev_err_probe(dev, ret, "Failed to get supplies\n");
	sensor->reset = devm_gpiod_get(dev, "reset", GPIOD_OUT_HIGH);
	if (IS_ERR(sensor->reset))
		return dev_err_probe(dev, PTR_ERR(sensor->reset), "Failed to get reset GPIO\n");

	v4l2_i2c_subdev_init(&sensor->sd, client, &gc02m1_raw_ops);
	sensor->sd.internal_ops = &gc02m1_raw_internal_ops;
	sensor->sd.flags |= V4L2_SUBDEV_FL_HAS_DEVNODE;
	sensor->sd.entity.function = MEDIA_ENT_F_CAM_SENSOR;
	sensor->pad.flags = MEDIA_PAD_FL_SOURCE;

	ret = gc02m1_raw_power_on(dev);
	if (ret)
		return dev_err_probe(dev, ret, "Failed to power sensor\n");
	ret = cci_read(sensor->regmap, CCI_REG16(0xf0), &id, NULL);
	gc02m1_raw_power_off(dev);
	if (ret)
		return dev_err_probe(dev, ret, "Failed to read sensor ID\n");
	if (id != 0x02e0)
		return dev_err_probe(dev, -ENODEV, "Unexpected sensor ID: %#llx\n", id);

	dev_info(dev, "GC02M1_RAW identity confirmed: %#llx\n", id);

	ret = gc02m1_raw_init_controls(sensor);
	if (ret)
		return ret;
	ret = media_entity_pads_init(&sensor->sd.entity, 1, &sensor->pad);
	if (ret)
		goto free_controls;
	sensor->sd.state_lock = sensor->ctrls.lock;
	ret = v4l2_subdev_init_finalize(&sensor->sd);
	if (ret)
		goto clean_entity;

	pm_runtime_set_suspended(dev);
	pm_runtime_enable(dev);
	ret = v4l2_async_register_subdev_sensor(&sensor->sd);
	if (ret)
		goto disable_pm;
	return 0;

disable_pm:
	pm_runtime_disable(dev);
	v4l2_subdev_cleanup(&sensor->sd);
clean_entity:
	media_entity_cleanup(&sensor->sd.entity);
free_controls:
	v4l2_ctrl_handler_free(&sensor->ctrls);
	return ret;
}

static void gc02m1_raw_remove(struct i2c_client *client)
{
	struct v4l2_subdev *sd = i2c_get_clientdata(client);
	struct gc02m1_raw *sensor = to_gc02m1_raw(sd);

	v4l2_async_unregister_subdev(sd);
	pm_runtime_disable(sd->dev);
	if (!pm_runtime_status_suspended(sd->dev))
		gc02m1_raw_power_off(sd->dev);
	pm_runtime_set_suspended(sd->dev);
	v4l2_subdev_cleanup(sd);
	media_entity_cleanup(&sd->entity);
	v4l2_ctrl_handler_free(&sensor->ctrls);
}

static int gc02m1_raw_suspend(struct device *dev)
{
	struct v4l2_subdev *sd = dev_get_drvdata(dev);
	struct gc02m1_raw *sensor = to_gc02m1_raw(sd);
	struct v4l2_subdev_state *state;
	int ret;

	state = v4l2_subdev_lock_and_get_active_state(sd);
	ret = sensor->streaming ? -EBUSY : pm_runtime_force_suspend(dev);
	v4l2_subdev_unlock_state(state);
	return ret;
}

static const struct dev_pm_ops gc02m1_raw_pm_ops = {
	SYSTEM_SLEEP_PM_OPS(gc02m1_raw_suspend, pm_runtime_force_resume)
	RUNTIME_PM_OPS(gc02m1_raw_power_off, gc02m1_raw_power_on, NULL)
};

static const struct of_device_id gc02m1_raw_of_match[] = {
	{ .compatible = "tb710fu,gc02m1-raw-20261117" },
	{ }
};
MODULE_DEVICE_TABLE(of, gc02m1_raw_of_match);

static struct i2c_driver gc02m1_raw_driver = {
	.driver = {
		.name = "gc02m1_raw",
		.of_match_table = gc02m1_raw_of_match,
		.pm = pm_ptr(&gc02m1_raw_pm_ops),
	},
	.probe = gc02m1_raw_probe,
	.remove = gc02m1_raw_remove,
};
module_i2c_driver(gc02m1_raw_driver);

MODULE_DESCRIPTION("GalaxyCore GC02M1_RAW image sensor driver");
MODULE_LICENSE("GPL");
