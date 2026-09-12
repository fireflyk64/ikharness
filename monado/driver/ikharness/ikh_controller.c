// Copyright 2026, IK Harness contributors
// SPDX-License-Identifier: BSD-2-Clause
/*!
 * @file
 * @brief  Socket driven hand controller device.
 *
 * Emulates a Valve Index controller (or the Khronos simple controller) whose
 * buttons are never pressed; only the grip/aim poses matter for IK. Grip and
 * aim report the same pose: the orchestrator is responsible for placing the
 * virtual controller where a real one would sit in the hand.
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
#include <assert.h>


struct ikh_controller
{
	struct xrt_device base;
	struct ikh_hub *hub;
	uint32_t index;
	enum ikh_controller_profile profile;
};

static inline struct ikh_controller *
ikh_controller(struct xrt_device *xdev)
{
	return (struct ikh_controller *)xdev;
}


/*
 *
 * Input tables.
 *
 */

static enum xrt_input_name index_inputs[] = {
    XRT_INPUT_INDEX_SYSTEM_CLICK,    XRT_INPUT_INDEX_SYSTEM_TOUCH,     XRT_INPUT_INDEX_A_CLICK,
    XRT_INPUT_INDEX_A_TOUCH,         XRT_INPUT_INDEX_B_CLICK,          XRT_INPUT_INDEX_B_TOUCH,
    XRT_INPUT_INDEX_SQUEEZE_VALUE,   XRT_INPUT_INDEX_SQUEEZE_FORCE,    XRT_INPUT_INDEX_TRIGGER_CLICK,
    XRT_INPUT_INDEX_TRIGGER_VALUE,   XRT_INPUT_INDEX_TRIGGER_TOUCH,    XRT_INPUT_INDEX_THUMBSTICK,
    XRT_INPUT_INDEX_THUMBSTICK_CLICK, XRT_INPUT_INDEX_THUMBSTICK_TOUCH, XRT_INPUT_INDEX_TRACKPAD,
    XRT_INPUT_INDEX_TRACKPAD_FORCE,  XRT_INPUT_INDEX_TRACKPAD_TOUCH,   XRT_INPUT_INDEX_GRIP_POSE,
    XRT_INPUT_INDEX_AIM_POSE,
};

static enum xrt_output_name index_outputs[] = {
    XRT_OUTPUT_NAME_INDEX_HAPTIC,
};

static struct xrt_binding_input_pair index_to_simple_inputs[4] = {
    {XRT_INPUT_SIMPLE_SELECT_CLICK, XRT_INPUT_INDEX_TRIGGER_VALUE},
    {XRT_INPUT_SIMPLE_MENU_CLICK, XRT_INPUT_INDEX_B_CLICK},
    {XRT_INPUT_SIMPLE_GRIP_POSE, XRT_INPUT_INDEX_GRIP_POSE},
    {XRT_INPUT_SIMPLE_AIM_POSE, XRT_INPUT_INDEX_AIM_POSE},
};

static struct xrt_binding_output_pair index_to_simple_outputs[1] = {
    {XRT_OUTPUT_NAME_SIMPLE_VIBRATION, XRT_OUTPUT_NAME_INDEX_HAPTIC},
};

static struct xrt_binding_profile index_binding_profiles[1] = {
    {
        .name = XRT_DEVICE_SIMPLE_CONTROLLER,
        .inputs = index_to_simple_inputs,
        .input_count = ARRAY_SIZE(index_to_simple_inputs),
        .outputs = index_to_simple_outputs,
        .output_count = ARRAY_SIZE(index_to_simple_outputs),
    },
};

static enum xrt_input_name simple_inputs[] = {
    XRT_INPUT_SIMPLE_SELECT_CLICK,
    XRT_INPUT_SIMPLE_MENU_CLICK,
    XRT_INPUT_SIMPLE_GRIP_POSE,
    XRT_INPUT_SIMPLE_AIM_POSE,
};

static enum xrt_output_name simple_outputs[] = {
    XRT_OUTPUT_NAME_SIMPLE_VIBRATION,
};


/*
 *
 * Member functions.
 *
 */

static void
ikh_controller_destroy(struct xrt_device *xdev)
{
	struct ikh_controller *c = ikh_controller(xdev);
	struct ikh_hub *hub = c->hub;

	u_var_remove_root(c);
	u_device_free(&c->base);

	ikh_hub_release(hub);
}

static xrt_result_t
ikh_controller_update_inputs(struct xrt_device *xdev)
{
	struct ikh_controller *c = ikh_controller(xdev);
	bool connected = ikh_hub_is_connected(c->hub, c->index);
	int64_t now = os_monotonic_get_ns();

	for (uint32_t i = 0; i < xdev->input_count; i++) {
		xdev->inputs[i].active = connected;
		xdev->inputs[i].timestamp = now;
		U_ZERO(&xdev->inputs[i].value);
	}

	return XRT_SUCCESS;
}

static xrt_result_t
ikh_controller_get_tracked_pose(struct xrt_device *xdev,
                                enum xrt_input_name name,
                                int64_t at_timestamp_ns,
                                struct xrt_space_relation *out_relation)
{
	struct ikh_controller *c = ikh_controller(xdev);

	switch (name) {
	case XRT_INPUT_INDEX_GRIP_POSE:
	case XRT_INPUT_INDEX_AIM_POSE:
		if (c->profile != IKH_CONTROLLER_INDEX) {
			U_LOG_XDEV_UNSUPPORTED_INPUT(&c->base, c->hub->log_level, name);
			return XRT_ERROR_INPUT_UNSUPPORTED;
		}
		break;
	case XRT_INPUT_SIMPLE_GRIP_POSE:
	case XRT_INPUT_SIMPLE_AIM_POSE:
		if (c->profile != IKH_CONTROLLER_SIMPLE) {
			U_LOG_XDEV_UNSUPPORTED_INPUT(&c->base, c->hub->log_level, name);
			return XRT_ERROR_INPUT_UNSUPPORTED;
		}
		break;
	default: U_LOG_XDEV_UNSUPPORTED_INPUT(&c->base, c->hub->log_level, name); return XRT_ERROR_INPUT_UNSUPPORTED;
	}

	ikh_hub_get_relation(c->hub, c->index, out_relation);

	return XRT_SUCCESS;
}

static xrt_result_t
ikh_controller_set_output(struct xrt_device *xdev, enum xrt_output_name name, const struct xrt_output_value *value)
{
	// Haptics are accepted and ignored.
	return XRT_SUCCESS;
}


/*
 *
 * 'Exported' functions.
 *
 */

struct xrt_device *
ikh_controller_create(struct ikh_hub *hub, uint32_t index, enum ikh_controller_profile profile, bool left)
{
	enum xrt_input_name *inputs = NULL;
	uint32_t input_count = 0;
	enum xrt_output_name *outputs = NULL;
	uint32_t output_count = 0;
	struct xrt_binding_profile *binding_profiles = NULL;
	uint32_t binding_profile_count = 0;
	enum xrt_device_name name;
	enum xrt_device_type type;
	const char *profile_str;

	switch (profile) {
	case IKH_CONTROLLER_INDEX:
		name = XRT_DEVICE_INDEX_CONTROLLER;
		type = left ? XRT_DEVICE_TYPE_LEFT_HAND_CONTROLLER : XRT_DEVICE_TYPE_RIGHT_HAND_CONTROLLER;
		inputs = index_inputs;
		input_count = ARRAY_SIZE(index_inputs);
		outputs = index_outputs;
		output_count = ARRAY_SIZE(index_outputs);
		binding_profiles = index_binding_profiles;
		binding_profile_count = ARRAY_SIZE(index_binding_profiles);
		profile_str = "Index";
		break;
	case IKH_CONTROLLER_SIMPLE:
		name = XRT_DEVICE_SIMPLE_CONTROLLER;
		// The simple controller profile is handedness-agnostic in Monado.
		type = XRT_DEVICE_TYPE_ANY_HAND_CONTROLLER;
		inputs = simple_inputs;
		input_count = ARRAY_SIZE(simple_inputs);
		outputs = simple_outputs;
		output_count = ARRAY_SIZE(simple_outputs);
		profile_str = "Simple";
		break;
	default: assert(false && "unsupported controller profile"); return NULL;
	}

	struct ikh_controller *c =
	    U_DEVICE_ALLOCATE(struct ikh_controller, U_DEVICE_ALLOC_NO_FLAGS, input_count, output_count);

	ikh_hub_reference(hub);
	c->hub = hub;
	c->index = index;
	c->profile = profile;

	u_device_populate_function_pointers(&c->base, ikh_controller_get_tracked_pose, ikh_controller_destroy);
	c->base.update_inputs = ikh_controller_update_inputs;
	c->base.set_output = ikh_controller_set_output;
	c->base.name = name;
	c->base.device_type = type;
	c->base.tracking_origin = &hub->origin;
	c->base.binding_profiles = binding_profiles;
	c->base.binding_profile_count = binding_profile_count;
	c->base.supported.orientation_tracking = true;
	c->base.supported.position_tracking = true;
	c->base.supported.hand_tracking = false;

	for (uint32_t i = 0; i < input_count; i++) {
		c->base.inputs[i].name = inputs[i];
		c->base.inputs[i].active = true;
	}
	for (uint32_t i = 0; i < output_count; i++) {
		c->base.outputs[i].name = outputs[i];
	}

	snprintf(c->base.str, XRT_DEVICE_NAME_LEN, "IK Harness %s %s Controller", profile_str, left ? "Left" : "Right");
	snprintf(c->base.serial, XRT_DEVICE_NAME_LEN, "%s", hub->descs[index].serial);

	u_var_add_root(c, c->base.str, true);
	u_var_add_pose(c, &hub->states[index].pose, "pose");

	return &c->base;
}
