// Copyright 2026, IK Harness contributors
// SPDX-License-Identifier: BSD-2-Clause
/*!
 * @file
 * @brief  Socket driven HMD device.
 * @ingroup drv_ikharness
 */

#include "xrt/xrt_device.h"

#include "os/os_time.h"

#include "math/m_api.h"
#include "math/m_mathinclude.h"

#include "util/u_device.h"
#include "util/u_distortion_mesh.h"
#include "util/u_logging.h"
#include "util/u_misc.h"
#include "util/u_var.h"

#include "ikh_internal.h"

#include <stdio.h>


struct ikh_hmd
{
	struct xrt_device base;
	struct ikh_hub *hub;
	uint32_t index;
};

static inline struct ikh_hmd *
ikh_hmd(struct xrt_device *xdev)
{
	return (struct ikh_hmd *)xdev;
}

static void
ikh_hmd_destroy(struct xrt_device *xdev)
{
	struct ikh_hmd *hmd = ikh_hmd(xdev);
	struct ikh_hub *hub = hmd->hub;

	u_var_remove_root(hmd);
	u_device_free(&hmd->base);

	ikh_hub_release(hub);
}

static xrt_result_t
ikh_hmd_get_tracked_pose(struct xrt_device *xdev,
                         enum xrt_input_name name,
                         int64_t at_timestamp_ns,
                         struct xrt_space_relation *out_relation)
{
	struct ikh_hmd *hmd = ikh_hmd(xdev);

	if (name != XRT_INPUT_GENERIC_HEAD_POSE) {
		U_LOG_XDEV_UNSUPPORTED_INPUT(&hmd->base, hmd->hub->log_level, name);
		return XRT_ERROR_INPUT_UNSUPPORTED;
	}

	ikh_hub_get_relation(hmd->hub, hmd->index, out_relation);

	return XRT_SUCCESS;
}

struct xrt_device *
ikh_hmd_create(struct ikh_hub *hub, uint32_t index)
{
	const struct ikh_config *cfg = &hub->config;

	enum u_device_alloc_flags flags = (enum u_device_alloc_flags)(U_DEVICE_ALLOC_HMD);
	struct ikh_hmd *hmd = U_DEVICE_ALLOCATE(struct ikh_hmd, flags, 1, 0);

	ikh_hub_reference(hub);
	hmd->hub = hub;
	hmd->index = index;

	hmd->base.update_inputs = u_device_noop_update_inputs;
	hmd->base.get_tracked_pose = ikh_hmd_get_tracked_pose;
	hmd->base.get_view_poses = u_device_get_view_poses;
	hmd->base.get_visibility_mask = u_device_get_visibility_mask;
	hmd->base.destroy = ikh_hmd_destroy;
	hmd->base.name = XRT_DEVICE_GENERIC_HMD;
	hmd->base.device_type = XRT_DEVICE_TYPE_HMD;
	hmd->base.tracking_origin = &hub->origin;
	hmd->base.supported.orientation_tracking = true;
	hmd->base.supported.position_tracking = true;

	snprintf(hmd->base.str, XRT_DEVICE_NAME_LEN, "IK Harness HMD");
	snprintf(hmd->base.serial, XRT_DEVICE_NAME_LEN, "%s", hub->descs[index].serial);

	hmd->base.inputs[0].name = XRT_INPUT_GENERIC_HEAD_POSE;

	uint32_t view_count = cfg->hmd.view_count == 1 ? 1 : 2;
	hmd->base.hmd->view_count = view_count;

	// Physical panel size only matters for the distortion-less mesh, keep a plausible 16:9-ish panel.
	struct u_device_simple_info info;
	info.display.w_pixels = cfg->hmd.w_pixels;
	info.display.h_pixels = cfg->hmd.h_pixels;
	info.display.w_meters = 0.13f;
	info.display.h_meters = 0.07f;
	info.lens_horizontal_separation_meters = cfg->hmd.ipd_m;
	info.lens_vertical_position_meters = 0.07f / 2.0f;

	const float fov_rad = cfg->hmd.fov_deg * (float)(M_PI / 180.0);
	bool ret;
	if (view_count == 1) {
		info.fov[0] = fov_rad;
		ret = u_device_setup_one_eye(&hmd->base, &info);
	} else {
		info.fov[0] = fov_rad;
		info.fov[1] = fov_rad;
		ret = u_device_setup_split_side_by_side(&hmd->base, &info);
	}
	if (!ret) {
		IKH_ERROR(hub, "Failed to setup HMD info");
		ikh_hmd_destroy(&hmd->base);
		return NULL;
	}

	if (cfg->hmd.refresh_hz > 0.0f) {
		hmd->base.hmd->screens[0].nominal_frame_interval_ns =
		    (uint64_t)(1000000000.0 / (double)cfg->hmd.refresh_hz);
	}

	// No lens distortion: apps render exactly what the compositor shows.
	u_distortion_mesh_set_none(&hmd->base);

	u_var_add_root(hmd, "IK Harness HMD", true);
	u_var_add_pose(hmd, &hub->states[index].pose, "pose");

	return &hmd->base;
}
