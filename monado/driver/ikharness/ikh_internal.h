// Copyright 2026, IK Harness contributors
// SPDX-License-Identifier: BSD-2-Clause
/*!
 * @file
 * @brief  Internal structures shared between the hub and its devices.
 * @ingroup drv_ikharness
 */

#pragma once

#include "xrt/xrt_device.h"
#include "xrt/xrt_tracking.h"

#include "os/os_threading.h"

#include "util/u_logging.h"

#include "ikh_interface.h"
#include "ikh_protocol.h"

#ifdef __cplusplus
extern "C" {
#endif


/*!
 * Latest state of one published device, protected by @ref ikh_hub::mutex.
 */
struct ikh_device_state
{
	struct xrt_pose pose;
	struct xrt_vec3 linear_velocity;
	struct xrt_vec3 angular_velocity;
	uint32_t flags; //!< enum ikh_pose_flags
	int64_t timestamp_ns;
};

/*!
 * Static description of one published device.
 */
struct ikh_device_desc_internal
{
	enum ikh_device_kind kind;
	char role[IKH_ROLE_NAME_LEN];
	char serial[IKH_SERIAL_LEN];
};

/*!
 * The hub owns the socket server, the shared tracking origin and the state
 * of every device. Devices hold a reference to it.
 */
struct ikh_hub
{
	struct ikh_config config;

	//! Shared by all devices, identity offset: device poses are stage poses.
	struct xrt_tracking_origin origin;

	enum u_logging_level log_level;

	//! Guards states, stats and refcount.
	struct os_mutex mutex;

	struct os_thread_helper oth;
	int accept_fd;
	int conn_fd;

	uint32_t device_count;
	struct ikh_device_desc_internal descs[IKH_MAX_DEVICES];
	struct ikh_device_state states[IKH_MAX_DEVICES];

	//! Stats, guarded by mutex.
	uint64_t last_frame_id;
	int64_t last_frame_ns;
	uint64_t frames_received;
	uint32_t clients_served;

	int refcount;
};

#define IKH_TRACE(H, ...) U_LOG_IFL_T((H)->log_level, __VA_ARGS__)
#define IKH_DEBUG(H, ...) U_LOG_IFL_D((H)->log_level, __VA_ARGS__)
#define IKH_INFO(H, ...) U_LOG_IFL_I((H)->log_level, __VA_ARGS__)
#define IKH_WARN(H, ...) U_LOG_IFL_W((H)->log_level, __VA_ARGS__)
#define IKH_ERROR(H, ...) U_LOG_IFL_E((H)->log_level, __VA_ARGS__)


/*
 *
 * Hub functions used by devices.
 *
 */

void
ikh_hub_reference(struct ikh_hub *hub);

/*!
 * Copy the current relation of device @p index into @p out_relation. Ignores
 * timestamps on purpose: the driver holds the last pose it was given.
 */
void
ikh_hub_get_relation(struct ikh_hub *hub, uint32_t index, struct xrt_space_relation *out_relation);

/*!
 * Whether the device currently reports as connected/active.
 */
bool
ikh_hub_is_connected(struct ikh_hub *hub, uint32_t index);


/*
 *
 * Device constructors, each takes a hub reference.
 *
 */

struct xrt_device *
ikh_hmd_create(struct ikh_hub *hub, uint32_t index);

struct xrt_device *
ikh_controller_create(struct ikh_hub *hub, uint32_t index, enum ikh_controller_profile profile, bool left);

struct xrt_device *
ikh_tracker_create(struct ikh_hub *hub, uint32_t index);


#ifdef __cplusplus
}
#endif
