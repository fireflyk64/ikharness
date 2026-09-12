// Copyright 2026, IK Harness contributors
// SPDX-License-Identifier: BSD-2-Clause
/*!
 * @file
 * @brief  Interface to the IK harness driver.
 *
 * The driver publishes an HMD, two hand controllers and any number of generic
 * trackers whose poses are fed over a socket by an external orchestrator, see
 * @ref ikh_protocol.h. It is meant for deterministic, headless evaluation of
 * inverse-kinematics systems in XR applications.
 *
 * @ingroup drv_ikharness
 */

#pragma once

#include "xrt/xrt_compiler.h"
#include "xrt/xrt_defines.h"
#include "xrt/xrt_results.h"
#include "util/u_logging.h"

#include <stdint.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/*!
 * @defgroup drv_ikharness IK harness driver
 * @ingroup drv
 *
 * @brief Socket driven HMD, controllers and trackers for IK evaluation.
 */

struct cJSON;
struct xrt_device;
struct xrt_tracking_origin;
struct ikh_hub;

#define IKH_MAX_TRACKERS 32

enum ikh_controller_profile
{
	IKH_CONTROLLER_NONE = 0,
	IKH_CONTROLLER_INDEX,
	IKH_CONTROLLER_SIMPLE,
};

/*!
 * Fully resolved driver configuration.
 */
struct ikh_config
{
	uint16_t port;
	char bind_addr[64];

	struct
	{
		uint32_t w_pixels;
		uint32_t h_pixels;
		float fov_deg;
		float ipd_m;
		uint32_t view_count;
		float refresh_hz;
	} hmd;

	enum ikh_controller_profile controllers;

	uint32_t tracker_count;
	char tracker_roles[IKH_MAX_TRACKERS][32];
};

/*!
 * Fill @p out with defaults, then override from (in order of precedence):
 * the file named by the IKH_CONFIG environment variable, the "ikharness"
 * object in Monado's main config (@p main_config, may be NULL), and the
 * IKH_PORT / IKH_BIND environment variables.
 *
 * @return false only on a hard parse error.
 */
bool
ikh_config_load(const struct cJSON *main_config, struct ikh_config *out);

/*!
 * Log level requested via IKH_LOG.
 */
enum u_logging_level
ikh_log_level(void);

/*!
 * Create the hub: parses nothing itself, takes an already loaded config,
 * starts the socket server thread. The hub is reference counted; the caller
 * owns one reference and every device created from it owns one.
 */
xrt_result_t
ikh_hub_create(const struct ikh_config *config, struct ikh_hub **out_hub);

/*!
 * Create all devices described by the config. Devices are written to
 * @p out_xdevs in wire-protocol index order.
 *
 * @param[out] out_head   The HMD.
 * @param[out] out_left   Left controller or NULL.
 * @param[out] out_right  Right controller or NULL.
 */
xrt_result_t
ikh_hub_create_devices(struct ikh_hub *hub,
                       struct xrt_device **out_xdevs,
                       uint32_t max_xdevs,
                       uint32_t *out_count,
                       struct xrt_device **out_head,
                       struct xrt_device **out_left,
                       struct xrt_device **out_right);

/*!
 * Drop a reference, the hub is destroyed (thread stopped, sockets closed)
 * when the last one goes away.
 */
void
ikh_hub_release(struct ikh_hub *hub);


#ifdef __cplusplus
}
#endif
