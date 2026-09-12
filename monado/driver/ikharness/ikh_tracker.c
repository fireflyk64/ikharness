// Copyright 2026, IK Harness contributors
// SPDX-License-Identifier: BSD-2-Clause
/*!
 * @file
 * @brief  Socket driven generic tracker device.
 *
 * Presents itself as a Vive Tracker so both the OpenVR layer (generic tracker
 * device class) and the OpenXR bindings (vive_tracker_htcx profile, when the
 * runtime enables it) know what to do with it.
 *
 * @ingroup drv_ikharness
 */

#include "xrt/xrt_device.h"

#include "os/os_time.h"

#include "util/u_device.h"
#include "util/u_logging.h"
#include "util/u_misc.h"
#include "util/u_var.h"

#include "ikh_internal.h"

#include <stdio.h>


enum ikh_tracker_input
{
	IKH_TRACKER_INPUT_GENERIC_POSE = 0,
	IKH_TRACKER_INPUT_GRIP_POSE,
	IKH_TRACKER_INPUT_COUNT,
};

struct ikh_tracker
{
	struct xrt_device base;
	struct ikh_hub *hub;
	uint32_t index;
};

static inline struct ikh_tracker *
ikh_tracker(struct xrt_device *xdev)
{
	return (struct ikh_tracker *)xdev;
}

static void
ikh_tracker_destroy(struct xrt_device *xdev)
{
	struct ikh_tracker *t = ikh_tracker(xdev);
	struct ikh_hub *hub = t->hub;

	u_var_remove_root(t);
	u_device_free(&t->base);

	ikh_hub_release(hub);
}

static xrt_result_t
ikh_tracker_update_inputs(struct xrt_device *xdev)
{
	struct ikh_tracker *t = ikh_tracker(xdev);
	bool connected = ikh_hub_is_connected(t->hub, t->index);
	int64_t now = os_monotonic_get_ns();

	for (uint32_t i = 0; i < xdev->input_count; i++) {
		xdev->inputs[i].active = connected;
		xdev->inputs[i].timestamp = now;
	}

	return XRT_SUCCESS;
}

static xrt_result_t
ikh_tracker_get_tracked_pose(struct xrt_device *xdev,
                             enum xrt_input_name name,
                             int64_t at_timestamp_ns,
                             struct xrt_space_relation *out_relation)
{
	struct ikh_tracker *t = ikh_tracker(xdev);

	switch (name) {
	case XRT_INPUT_GENERIC_TRACKER_POSE:
	case XRT_INPUT_VIVE_TRACKER_GRIP_POSE: break;
	default: U_LOG_XDEV_UNSUPPORTED_INPUT(&t->base, t->hub->log_level, name); return XRT_ERROR_INPUT_UNSUPPORTED;
	}

	ikh_hub_get_relation(t->hub, t->index, out_relation);

	return XRT_SUCCESS;
}

struct xrt_device *
ikh_tracker_create(struct ikh_hub *hub, uint32_t index)
{
	struct ikh_tracker *t =
	    U_DEVICE_ALLOCATE(struct ikh_tracker, U_DEVICE_ALLOC_NO_FLAGS, IKH_TRACKER_INPUT_COUNT, 0);

	ikh_hub_reference(hub);
	t->hub = hub;
	t->index = index;

	u_device_populate_function_pointers(&t->base, ikh_tracker_get_tracked_pose, ikh_tracker_destroy);
	t->base.update_inputs = ikh_tracker_update_inputs;
	t->base.name = XRT_DEVICE_VIVE_TRACKER_GEN3;
	t->base.device_type = XRT_DEVICE_TYPE_GENERIC_TRACKER;
	t->base.tracking_origin = &hub->origin;
	t->base.supported.orientation_tracking = true;
	t->base.supported.position_tracking = true;

	t->base.inputs[IKH_TRACKER_INPUT_GENERIC_POSE].name = XRT_INPUT_GENERIC_TRACKER_POSE;
	t->base.inputs[IKH_TRACKER_INPUT_GRIP_POSE].name = XRT_INPUT_VIVE_TRACKER_GRIP_POSE;

	snprintf(t->base.str, XRT_DEVICE_NAME_LEN, "IK Harness Tracker (%s)", hub->descs[index].role);
	snprintf(t->base.serial, XRT_DEVICE_NAME_LEN, "%s", hub->descs[index].serial);

	u_var_add_root(t, t->base.str, true);
	u_var_add_pose(t, &hub->states[index].pose, "pose");

	return &t->base;
}
