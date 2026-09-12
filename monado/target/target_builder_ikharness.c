// Copyright 2026, IK Harness contributors
// SPDX-License-Identifier: BSD-2-Clause
/*!
 * @file
 * @brief  IK harness driver builder.
 *
 * Always available in a Monado built with XRT_BUILD_DRIVER_IKHARNESS, it
 * outranks every hardware builder so a test machine with real headsets
 * attached still gets the socket driven devices. Set IKH_ENABLE=0 to hide it.
 *
 * @ingroup xrt_iface
 */

#include "xrt/xrt_config_drivers.h"
#include "xrt/xrt_prober.h"
#include "xrt/xrt_system.h"
#include "xrt/xrt_tracking.h"

#include "util/u_debug.h"
#include "util/u_logging.h"
#include "util/u_misc.h"

#include "target_builder_helpers.h"
#include "target_builder_interface.h"

#include "ikharness/ikh_interface.h"

#include <assert.h>


#ifndef XRT_BUILD_DRIVER_IKHARNESS
#error "Must only be built with XRT_BUILD_DRIVER_IKHARNESS set"
#endif

DEBUG_GET_ONCE_BOOL_OPTION(ikh_enable, "IKH_ENABLE", true)

static const char *driver_list[] = {
    "ikharness",
};


/*
 *
 * Member functions.
 *
 */

static xrt_result_t
ikharness_estimate_system(struct xrt_builder *xb,
                          cJSON *config,
                          struct xrt_prober *xp,
                          struct xrt_builder_estimate *estimate)
{
	struct ikh_config cfg;
	if (!ikh_config_load(config, &cfg)) {
		// Config broken: report nothing so a clear error is logged and no system is built from us.
		return XRT_SUCCESS;
	}

	estimate->certain.head = true;
	estimate->certain.left = cfg.controllers != IKH_CONTROLLER_NONE;
	estimate->certain.right = cfg.controllers != IKH_CONTROLLER_NONE;
	estimate->certain.dof6 = true;
	estimate->certain.extra_device_count = cfg.tracker_count;
	estimate->priority = 1000;

	return XRT_SUCCESS;
}

static xrt_result_t
ikharness_open_system_impl(struct xrt_builder *xb,
                           cJSON *config,
                           struct xrt_prober *xp,
                           struct xrt_tracking_origin *origin,
                           struct xrt_system_devices *xsysd,
                           struct xrt_frame_context *xfctx,
                           struct t_builder_options *tbo)
{
	struct ikh_config cfg;
	if (!ikh_config_load(config, &cfg)) {
		U_LOG_E("ikharness: invalid configuration");
		return XRT_ERROR_DEVICE_CREATION_FAILED;
	}

	struct ikh_hub *hub = NULL;
	xrt_result_t xret = ikh_hub_create(&cfg, &hub);
	if (xret != XRT_SUCCESS) {
		return xret;
	}

	struct xrt_device *xdevs[XRT_SYSTEM_MAX_DEVICES] = {0};
	uint32_t count = 0;
	struct xrt_device *head = NULL;
	struct xrt_device *left = NULL;
	struct xrt_device *right = NULL;

	uint32_t room = (uint32_t)(ARRAY_SIZE(xsysd->static_xdevs) - xsysd->static_xdev_count);
	xret = ikh_hub_create_devices(hub, xdevs, room, &count, &head, &left, &right);

	// Devices hold their own references now.
	ikh_hub_release(hub);

	if (xret != XRT_SUCCESS) {
		return xret;
	}

	for (uint32_t i = 0; i < count; i++) {
		xsysd->static_xdevs[xsysd->static_xdev_count++] = xdevs[i];
	}

	tbo->head = head;
	tbo->left = left;
	tbo->right = right;

	// Stage and local share the same origin, apps that use LOCAL get a 1.6 m raised, floor-aligned frame.
	tbo->T_stage_local.orientation = (struct xrt_quat)XRT_QUAT_IDENTITY;
	tbo->T_stage_local.position = (struct xrt_vec3){0.0f, 1.6f, 0.0f};

	return XRT_SUCCESS;
}

static void
ikharness_destroy(struct xrt_builder *xb)
{
	free(xb);
}


/*
 *
 * 'Exported' functions.
 *
 */

struct xrt_builder *
t_builder_ikharness_create(void)
{
	struct t_builder *ub = U_TYPED_CALLOC(struct t_builder);

	ub->base.estimate_system = ikharness_estimate_system;
	ub->base.open_system = t_builder_open_system_static_roles;
	ub->base.destroy = ikharness_destroy;
	ub->base.identifier = "ikharness";
	ub->base.name = "IK harness socket driven devices builder";
	ub->base.driver_identifiers = driver_list;
	ub->base.driver_identifier_count = ARRAY_SIZE(driver_list);
	ub->base.exclude_from_automatic_discovery = !debug_get_bool_option_ikh_enable();

	ub->open_system_static_roles = ikharness_open_system_impl;

	return &ub->base;
}
