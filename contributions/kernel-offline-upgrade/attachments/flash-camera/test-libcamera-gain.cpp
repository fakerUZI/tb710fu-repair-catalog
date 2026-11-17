// SPDX-License-Identifier: GPL-2.0-or-later
#include <cmath>
#include <iostream>
#include <utility>
#include "../../src/ipa/libipa/camera_sensor_helper.h"
#include "test.h"
using namespace libcamera::ipa;
class TbGainTest : public Test {
protected:
 int run() override {
  for (const char *name : {"ov13b10", "gc13a2_raw", "gc02m1_raw"}) {
   auto h = CameraSensorHelperFactoryBase::create(name);
   if (!h) return TestFail;
   unsigned int scale = std::string(name)=="ov13b10" ? 128 : 1024;
   for (double gain : {1., 2., 4., 8.}) {
    auto code = static_cast<unsigned int>(scale*gain);
    if (std::abs(h->gain(code)-gain)>1e-9 || h->gainCode(gain)!=code) return TestFail;
   }
  }
  return CameraSensorHelperFactoryBase::create("tb-invalid") ? TestFail : TestPass;
 }
};
TEST_REGISTER(TbGainTest)
