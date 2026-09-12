// Copyright 2026, IK Harness contributors
// SPDX-License-Identifier: BSD-2-Clause
/*!
 * @file
 * @brief  Hub of the IK harness driver: config, device table, socket server.
 * @ingroup drv_ikharness
 */

#ifndef _GNU_SOURCE
#define _GNU_SOURCE // accept4
#endif

#include "xrt/xrt_config_os.h"

#include "os/os_time.h"

#include "math/m_api.h"

#include "util/u_debug.h"
#include "util/u_file.h"
#include "util/u_json.h"
#include "util/u_logging.h"
#include "util/u_misc.h"
#include "util/u_var.h"

#include "ikh_internal.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <inttypes.h>

#include <unistd.h>
#include <sys/socket.h>
#include <sys/select.h>
#include <sys/types.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <arpa/inet.h>


DEBUG_GET_ONCE_LOG_OPTION(ikh_log, "IKH_LOG", U_LOGGING_INFO)
DEBUG_GET_ONCE_OPTION(ikh_config_path, "IKH_CONFIG", NULL)
DEBUG_GET_ONCE_NUM_OPTION(ikh_port, "IKH_PORT", 0)
DEBUG_GET_ONCE_OPTION(ikh_bind, "IKH_BIND", NULL)


/*
 *
 * Config.
 *
 */

static const char *default_tracker_roles[] = {
    "waist", "chest", "left_foot", "right_foot", "left_knee", "right_knee", "left_elbow", "right_elbow",
};

enum u_logging_level
ikh_log_level(void)
{
	return debug_get_log_option_ikh_log();
}

static void
config_set_defaults(struct ikh_config *c)
{
	U_ZERO(c);
	c->port = IKH_DEFAULT_PORT;
	snprintf(c->bind_addr, sizeof(c->bind_addr), "127.0.0.1");
	c->hmd.w_pixels = 1280;
	c->hmd.h_pixels = 720;
	c->hmd.fov_deg = 90.0f;
	c->hmd.ipd_m = 0.063f;
	c->hmd.view_count = 2;
	c->hmd.refresh_hz = 90.0f;
	c->controllers = IKH_CONTROLLER_INDEX;
	c->tracker_count = ARRAY_SIZE(default_tracker_roles);
	for (uint32_t i = 0; i < c->tracker_count; i++) {
		snprintf(c->tracker_roles[i], sizeof(c->tracker_roles[i]), "%s", default_tracker_roles[i]);
	}
}

static bool
config_apply_json(const cJSON *root, struct ikh_config *c)
{
	if (root == NULL || !cJSON_IsObject(root)) {
		U_LOG_E("ikharness config is not a JSON object");
		return false;
	}

	int version = 1;
	u_json_get_int(u_json_get(root, "version"), &version);
	if (version != 1) {
		U_LOG_E("ikharness config version %d not supported (want 1)", version);
		return false;
	}

	int port = 0;
	if (u_json_get_int(u_json_get(root, "port"), &port) && port > 0 && port < 65536) {
		c->port = (uint16_t)port;
	}
	u_json_get_string_into_array(u_json_get(root, "bind"), c->bind_addr, sizeof(c->bind_addr));

	const cJSON *hmd = u_json_get(root, "hmd");
	if (hmd != NULL) {
		int v = 0;
		float f = 0.0f;
		if (u_json_get_int(u_json_get(hmd, "width"), &v) && v > 0) {
			c->hmd.w_pixels = (uint32_t)v;
		}
		if (u_json_get_int(u_json_get(hmd, "height"), &v) && v > 0) {
			c->hmd.h_pixels = (uint32_t)v;
		}
		if (u_json_get_int(u_json_get(hmd, "view_count"), &v) && (v == 1 || v == 2)) {
			c->hmd.view_count = (uint32_t)v;
		}
		if (u_json_get_float(u_json_get(hmd, "fov_deg"), &f) && f > 10.0f && f < 180.0f) {
			c->hmd.fov_deg = f;
		}
		if (u_json_get_float(u_json_get(hmd, "ipd_m"), &f) && f > 0.0f) {
			c->hmd.ipd_m = f;
		}
		if (u_json_get_float(u_json_get(hmd, "refresh_hz"), &f) && f > 0.0f) {
			c->hmd.refresh_hz = f;
		}
	}

	char controllers[32] = {0};
	if (u_json_get_string_into_array(u_json_get(root, "controllers"), controllers, sizeof(controllers))) {
		if (strcmp(controllers, "index") == 0) {
			c->controllers = IKH_CONTROLLER_INDEX;
		} else if (strcmp(controllers, "simple") == 0) {
			c->controllers = IKH_CONTROLLER_SIMPLE;
		} else if (strcmp(controllers, "none") == 0) {
			c->controllers = IKH_CONTROLLER_NONE;
		} else {
			U_LOG_E("ikharness: unknown controllers profile '%s' (index, simple, none)", controllers);
			return false;
		}
	}

	const cJSON *trackers = u_json_get(root, "trackers");
	if (trackers != NULL) {
		if (!cJSON_IsArray(trackers)) {
			U_LOG_E("ikharness: 'trackers' must be an array of role strings");
			return false;
		}
		c->tracker_count = 0;
		const cJSON *item = NULL;
		cJSON_ArrayForEach(item, trackers)
		{
			if (c->tracker_count >= IKH_MAX_TRACKERS) {
				U_LOG_W("ikharness: more than %d trackers, ignoring the rest", IKH_MAX_TRACKERS);
				break;
			}
			if (!cJSON_IsString(item) || item->valuestring[0] == '\0') {
				U_LOG_E("ikharness: tracker roles must be non-empty strings");
				return false;
			}
			snprintf(c->tracker_roles[c->tracker_count], sizeof(c->tracker_roles[0]), "%s",
			         item->valuestring);
			c->tracker_count++;
		}
	}

	return true;
}

bool
ikh_config_load(const struct cJSON *main_config, struct ikh_config *out)
{
	config_set_defaults(out);

	const char *path = debug_get_option_ikh_config_path();
	if (path != NULL && path[0] != '\0') {
		size_t size = 0;
		char *content = u_file_read_content_from_path(path, &size);
		if (content == NULL) {
			U_LOG_E("ikharness: could not read IKH_CONFIG file '%s'", path);
			return false;
		}
		cJSON *root = cJSON_Parse(content);
		free(content);
		if (root == NULL) {
			U_LOG_E("ikharness: failed to parse IKH_CONFIG file '%s'", path);
			return false;
		}
		bool ok = config_apply_json(root, out);
		cJSON_Delete(root);
		if (!ok) {
			return false;
		}
		U_LOG_I("ikharness: loaded config from '%s'", path);
	} else if (main_config != NULL) {
		const cJSON *node = cJSON_GetObjectItemCaseSensitive(main_config, "ikharness");
		if (node != NULL && !config_apply_json(node, out)) {
			return false;
		}
	}

	long port = debug_get_num_option_ikh_port();
	if (port > 0 && port < 65536) {
		out->port = (uint16_t)port;
	}
	const char *bind_addr = debug_get_option_ikh_bind();
	if (bind_addr != NULL && bind_addr[0] != '\0') {
		snprintf(out->bind_addr, sizeof(out->bind_addr), "%s", bind_addr);
	}

	return true;
}


/*
 *
 * Device table and initial poses.
 *
 */

static struct xrt_pose
initial_pose_for(enum ikh_device_kind kind, const char *role)
{
	struct xrt_pose p = XRT_POSE_IDENTITY;

	// A neutral standing pose for a ~1.7 m person, facing -Z, feet on the floor.
	switch (kind) {
	case IKH_DEVICE_HMD: p.position = (struct xrt_vec3){0.0f, 1.60f, 0.0f}; return p;
	case IKH_DEVICE_CONTROLLER_LEFT: p.position = (struct xrt_vec3){-0.25f, 1.05f, -0.20f}; return p;
	case IKH_DEVICE_CONTROLLER_RIGHT: p.position = (struct xrt_vec3){0.25f, 1.05f, -0.20f}; return p;
	default: break;
	}

	struct
	{
		const char *role;
		struct xrt_vec3 pos;
	} table[] = {
	    {"waist", {0.0f, 0.95f, 0.0f}},          {"hip", {0.0f, 0.95f, 0.0f}},
	    {"chest", {0.0f, 1.30f, 0.0f}},          {"left_foot", {-0.10f, 0.08f, 0.0f}},
	    {"right_foot", {0.10f, 0.08f, 0.0f}},    {"left_knee", {-0.10f, 0.50f, 0.0f}},
	    {"right_knee", {0.10f, 0.50f, 0.0f}},    {"left_elbow", {-0.40f, 1.25f, -0.05f}},
	    {"right_elbow", {0.40f, 1.25f, -0.05f}}, {"left_shoulder", {-0.18f, 1.45f, 0.0f}},
	    {"right_shoulder", {0.18f, 1.45f, 0.0f}},
	};
	for (size_t i = 0; i < ARRAY_SIZE(table); i++) {
		if (strcmp(table[i].role, role) == 0) {
			p.position = table[i].pos;
			return p;
		}
	}

	p.position = (struct xrt_vec3){0.0f, 1.0f, 0.0f};
	return p;
}

static void
add_device(struct ikh_hub *hub, enum ikh_device_kind kind, const char *role, const char *serial)
{
	uint32_t i = hub->device_count++;
	struct ikh_device_desc_internal *d = &hub->descs[i];
	d->kind = kind;
	snprintf(d->role, sizeof(d->role), "%s", role);
	snprintf(d->serial, sizeof(d->serial), "%s", serial);

	struct ikh_device_state *s = &hub->states[i];
	s->pose = initial_pose_for(kind, role);
	s->linear_velocity = (struct xrt_vec3)XRT_VEC3_ZERO;
	s->angular_velocity = (struct xrt_vec3)XRT_VEC3_ZERO;
	s->flags = IKH_POSE_DEFAULT;
	s->timestamp_ns = os_monotonic_get_ns();
}

static void
build_device_table(struct ikh_hub *hub)
{
	const struct ikh_config *c = &hub->config;
	hub->device_count = 0;

	add_device(hub, IKH_DEVICE_HMD, "", "IKH-HMD");
	if (c->controllers != IKH_CONTROLLER_NONE) {
		add_device(hub, IKH_DEVICE_CONTROLLER_LEFT, "", "IKH-CTRL-L");
		add_device(hub, IKH_DEVICE_CONTROLLER_RIGHT, "", "IKH-CTRL-R");
	}
	for (uint32_t i = 0; i < c->tracker_count && hub->device_count < IKH_MAX_DEVICES; i++) {
		char serial[IKH_SERIAL_LEN];
		snprintf(serial, sizeof(serial), "IKH-TRK-%s", c->tracker_roles[i]);
		add_device(hub, IKH_DEVICE_TRACKER, c->tracker_roles[i], serial);
	}
}


/*
 *
 * Socket helpers.
 *
 */

static bool
wait_readable(struct ikh_hub *hub, int fd)
{
	fd_set set;
	int ret = 0;

	if (fd < 0) {
		return false;
	}

	while (os_thread_helper_is_running(&hub->oth) && ret == 0) {
		struct timeval timeout = {.tv_sec = 0, .tv_usec = 250000};
		FD_ZERO(&set);
		FD_SET(fd, &set);
		ret = select(fd + 1, &set, NULL, NULL, &timeout);
		if (ret < 0 && errno == EINTR) {
			ret = 0;
		}
	}

	if (ret < 0) {
		IKH_ERROR(hub, "select: %s", strerror(errno));
		return false;
	}
	return ret > 0;
}

//! Read exactly @p size bytes, returns false on stop/disconnect/error.
static bool
read_exact(struct ikh_hub *hub, int fd, void *buf, size_t size)
{
	size_t current = 0;
	while (current < size) {
		if (!wait_readable(hub, fd)) {
			return false;
		}
		ssize_t ret = read(fd, (uint8_t *)buf + current, size - current);
		if (ret < 0) {
			if (errno == EINTR) {
				continue;
			}
			IKH_ERROR(hub, "read: %s", strerror(errno));
			return false;
		}
		if (ret == 0) {
			IKH_INFO(hub, "Client disconnected");
			return false;
		}
		current += (size_t)ret;
	}
	return true;
}

static bool
write_all(struct ikh_hub *hub, int fd, const void *buf, size_t size)
{
	size_t current = 0;
	while (current < size) {
		ssize_t ret = send(fd, (const uint8_t *)buf + current, size - current, MSG_NOSIGNAL);
		if (ret < 0) {
			if (errno == EINTR) {
				continue;
			}
			IKH_ERROR(hub, "send: %s", strerror(errno));
			return false;
		}
		if (ret == 0) {
			return false;
		}
		current += (size_t)ret;
	}
	return true;
}

static bool
send_msg(struct ikh_hub *hub, int fd, uint16_t type, const void *payload, uint32_t payload_size)
{
	struct ikh_msg_header h = {
	    .magic = IKH_PROTOCOL_MAGIC,
	    .version = IKH_PROTOCOL_VERSION,
	    .type = type,
	    .payload_size = payload_size,
	};
	if (!write_all(hub, fd, &h, sizeof(h))) {
		return false;
	}
	if (payload_size > 0 && !write_all(hub, fd, payload, payload_size)) {
		return false;
	}
	return true;
}

static bool
send_hello(struct ikh_hub *hub, int fd)
{
	size_t size = sizeof(struct ikh_msg_hello) + hub->device_count * sizeof(struct ikh_device_desc);
	uint8_t *buf = U_TYPED_ARRAY_CALLOC(uint8_t, size);

	struct ikh_msg_hello *hello = (struct ikh_msg_hello *)buf;
	hello->device_count = hub->device_count;
	hello->reserved = 0;

	struct ikh_device_desc *descs = (struct ikh_device_desc *)(buf + sizeof(*hello));
	for (uint32_t i = 0; i < hub->device_count; i++) {
		descs[i].index = i;
		descs[i].kind = (uint32_t)hub->descs[i].kind;
		snprintf(descs[i].role, sizeof(descs[i].role), "%s", hub->descs[i].role);
		snprintf(descs[i].serial, sizeof(descs[i].serial), "%s", hub->descs[i].serial);
	}

	bool ok = send_msg(hub, fd, IKH_MSG_HELLO, buf, (uint32_t)size);
	free(buf);
	return ok;
}

static bool
send_ack(struct ikh_hub *hub, int fd, uint16_t type)
{
	struct ikh_msg_ack ack;
	os_mutex_lock(&hub->mutex);
	ack.frame_id = hub->last_frame_id;
	ack.applied_at_ns = hub->last_frame_ns;
	os_mutex_unlock(&hub->mutex);
	return send_msg(hub, fd, type, &ack, sizeof(ack));
}

static bool
apply_frame(struct ikh_hub *hub, const uint8_t *payload, uint32_t payload_size)
{
	if (payload_size < sizeof(struct ikh_msg_frame)) {
		IKH_ERROR(hub, "FRAME payload too small (%u bytes)", payload_size);
		return false;
	}

	struct ikh_msg_frame frame;
	memcpy(&frame, payload, sizeof(frame));

	size_t expected = sizeof(frame) + (size_t)frame.pose_count * sizeof(struct ikh_pose);
	if (payload_size != expected) {
		IKH_ERROR(hub, "FRAME payload size %u does not match %u poses (expected %zu)", payload_size,
		          frame.pose_count, expected);
		return false;
	}

	int64_t now = os_monotonic_get_ns();
	int64_t stamp = frame.timestamp_ns > 0 ? frame.timestamp_ns : now;
	const uint8_t *ptr = payload + sizeof(frame);

	os_mutex_lock(&hub->mutex);
	for (uint32_t i = 0; i < frame.pose_count; i++, ptr += sizeof(struct ikh_pose)) {
		struct ikh_pose p;
		memcpy(&p, ptr, sizeof(p));

		if (p.index >= hub->device_count) {
			IKH_WARN(hub, "FRAME %" PRIu64 ": pose for unknown device index %u ignored", frame.frame_id,
			         p.index);
			continue;
		}

		struct ikh_device_state *s = &hub->states[p.index];
		s->pose.position = (struct xrt_vec3){p.position[0], p.position[1], p.position[2]};
		s->pose.orientation = (struct xrt_quat){p.orientation[0], p.orientation[1], p.orientation[2],
		                                        p.orientation[3]};
		if (!math_quat_validate_within_1_percent(&s->pose.orientation)) {
			IKH_WARN(hub, "FRAME %" PRIu64 ": device %u orientation not normalized, normalizing",
			         frame.frame_id, p.index);
		}
		math_quat_normalize(&s->pose.orientation);
		s->linear_velocity = (struct xrt_vec3){p.linear_velocity[0], p.linear_velocity[1], p.linear_velocity[2]};
		s->angular_velocity =
		    (struct xrt_vec3){p.angular_velocity[0], p.angular_velocity[1], p.angular_velocity[2]};
		s->flags = p.flags;
		s->timestamp_ns = stamp;
	}
	hub->last_frame_id = frame.frame_id;
	hub->last_frame_ns = now;
	hub->frames_received++;
	os_mutex_unlock(&hub->mutex);

	IKH_TRACE(hub, "Applied frame %" PRIu64 " with %u poses", frame.frame_id, frame.pose_count);

	return true;
}

static void
serve_client(struct ikh_hub *hub, int fd)
{
	uint8_t *payload = U_TYPED_ARRAY_CALLOC(uint8_t, IKH_MAX_PAYLOAD_SIZE);

	if (!send_hello(hub, fd)) {
		free(payload);
		return;
	}

	while (os_thread_helper_is_running(&hub->oth)) {
		struct ikh_msg_header h;
		if (!read_exact(hub, fd, &h, sizeof(h))) {
			break;
		}
		if (h.magic != IKH_PROTOCOL_MAGIC) {
			IKH_ERROR(hub, "Bad magic 0x%08x, dropping client", h.magic);
			break;
		}
		if (h.version != IKH_PROTOCOL_VERSION) {
			IKH_ERROR(hub, "Protocol version %u not supported (want %u), dropping client", h.version,
			          IKH_PROTOCOL_VERSION);
			break;
		}
		if (h.payload_size > IKH_MAX_PAYLOAD_SIZE) {
			IKH_ERROR(hub, "Payload of %u bytes too large, dropping client", h.payload_size);
			break;
		}
		if (h.payload_size > 0 && !read_exact(hub, fd, payload, h.payload_size)) {
			break;
		}

		bool ok = true;
		switch (h.type) {
		case IKH_MSG_FRAME:
			ok = apply_frame(hub, payload, h.payload_size);
			if (ok) {
				ok = send_ack(hub, fd, IKH_MSG_ACK);
			}
			break;
		case IKH_MSG_PING: ok = send_ack(hub, fd, IKH_MSG_PONG); break;
		case IKH_MSG_QUERY: ok = send_hello(hub, fd); break;
		default: IKH_WARN(hub, "Unknown message type %u ignored", h.type); break;
		}
		if (!ok) {
			break;
		}
	}

	free(payload);
}

static int
setup_listen_socket(struct ikh_hub *hub)
{
	int fd = socket(AF_INET, SOCK_STREAM | SOCK_CLOEXEC, 0);
	if (fd < 0) {
		IKH_ERROR(hub, "socket: %s", strerror(errno));
		return -1;
	}

	int flag = 1;
	if (setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &flag, sizeof(flag)) < 0) {
		IKH_WARN(hub, "setsockopt(SO_REUSEADDR): %s", strerror(errno));
	}

	struct sockaddr_in addr = {0};
	addr.sin_family = AF_INET;
	addr.sin_port = htons(hub->config.port);
	if (inet_pton(AF_INET, hub->config.bind_addr, &addr.sin_addr) != 1) {
		IKH_ERROR(hub, "Invalid bind address '%s'", hub->config.bind_addr);
		close(fd);
		return -1;
	}

	if (bind(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
		IKH_ERROR(hub, "bind %s:%u: %s", hub->config.bind_addr, hub->config.port, strerror(errno));
		close(fd);
		return -1;
	}
	if (listen(fd, 4) < 0) {
		IKH_ERROR(hub, "listen: %s", strerror(errno));
		close(fd);
		return -1;
	}

	IKH_INFO(hub, "Listening on %s:%u", hub->config.bind_addr, hub->config.port);
	return fd;
}

static void *
run_thread(void *ptr)
{
	struct ikh_hub *hub = (struct ikh_hub *)ptr;

	int lfd = setup_listen_socket(hub);
	if (lfd < 0) {
		IKH_ERROR(hub, "No listen socket, poses can not be updated");
		return NULL;
	}
	hub->accept_fd = lfd;

	while (os_thread_helper_is_running(&hub->oth)) {
		if (!wait_readable(hub, lfd)) {
			break;
		}
		struct sockaddr_in peer = {0};
		socklen_t peer_len = sizeof(peer);
		int fd = accept4(lfd, (struct sockaddr *)&peer, &peer_len, SOCK_CLOEXEC);
		if (fd < 0) {
			if (errno == EINTR) {
				continue;
			}
			IKH_ERROR(hub, "accept: %s", strerror(errno));
			break;
		}

		int flag = 1;
		setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &flag, sizeof(flag));

		char peer_str[INET_ADDRSTRLEN] = "?";
		inet_ntop(AF_INET, &peer.sin_addr, peer_str, sizeof(peer_str));
		IKH_INFO(hub, "Client connected from %s:%u", peer_str, ntohs(peer.sin_port));

		hub->conn_fd = fd;
		os_mutex_lock(&hub->mutex);
		hub->clients_served++;
		os_mutex_unlock(&hub->mutex);

		serve_client(hub, fd);

		hub->conn_fd = -1;
		close(fd);
		IKH_INFO(hub, "Client connection closed");
	}

	IKH_DEBUG(hub, "Leaving server thread");
	return NULL;
}


/*
 *
 * Hub lifetime.
 *
 */

static void
hub_destroy(struct ikh_hub *hub)
{
	IKH_DEBUG(hub, "Destroying hub");

	os_thread_helper_stop_and_wait(&hub->oth);
	os_thread_helper_destroy(&hub->oth);

	if (hub->conn_fd >= 0) {
		close(hub->conn_fd);
		hub->conn_fd = -1;
	}
	if (hub->accept_fd >= 0) {
		close(hub->accept_fd);
		hub->accept_fd = -1;
	}

	u_var_remove_root(hub);
	os_mutex_destroy(&hub->mutex);
	free(hub);
}

void
ikh_hub_reference(struct ikh_hub *hub)
{
	os_mutex_lock(&hub->mutex);
	hub->refcount++;
	os_mutex_unlock(&hub->mutex);
}

void
ikh_hub_release(struct ikh_hub *hub)
{
	os_mutex_lock(&hub->mutex);
	int count = --hub->refcount;
	os_mutex_unlock(&hub->mutex);

	if (count == 0) {
		hub_destroy(hub);
	}
}

void
ikh_hub_get_relation(struct ikh_hub *hub, uint32_t index, struct xrt_space_relation *out_relation)
{
	struct ikh_device_state s;

	os_mutex_lock(&hub->mutex);
	s = hub->states[index];
	os_mutex_unlock(&hub->mutex);

	uint32_t flags = 0;
	if (s.flags & IKH_POSE_ORIENTATION_VALID) {
		flags |= XRT_SPACE_RELATION_ORIENTATION_VALID_BIT;
	}
	if (s.flags & IKH_POSE_POSITION_VALID) {
		flags |= XRT_SPACE_RELATION_POSITION_VALID_BIT;
	}
	if (s.flags & IKH_POSE_TRACKED) {
		if (s.flags & IKH_POSE_ORIENTATION_VALID) {
			flags |= XRT_SPACE_RELATION_ORIENTATION_TRACKED_BIT;
		}
		if (s.flags & IKH_POSE_POSITION_VALID) {
			flags |= XRT_SPACE_RELATION_POSITION_TRACKED_BIT;
		}
	}
	if (s.flags & IKH_POSE_LINEAR_VELOCITY_VALID) {
		flags |= XRT_SPACE_RELATION_LINEAR_VELOCITY_VALID_BIT;
	}
	if (s.flags & IKH_POSE_ANGULAR_VELOCITY_VALID) {
		flags |= XRT_SPACE_RELATION_ANGULAR_VELOCITY_VALID_BIT;
	}

	out_relation->pose = s.pose;
	out_relation->linear_velocity = s.linear_velocity;
	out_relation->angular_velocity = s.angular_velocity;
	out_relation->relation_flags = (enum xrt_space_relation_flags)flags;
}

bool
ikh_hub_is_connected(struct ikh_hub *hub, uint32_t index)
{
	os_mutex_lock(&hub->mutex);
	bool connected = (hub->states[index].flags & IKH_POSE_CONNECTED) != 0;
	os_mutex_unlock(&hub->mutex);
	return connected;
}

xrt_result_t
ikh_hub_create(const struct ikh_config *config, struct ikh_hub **out_hub)
{
	struct ikh_hub *hub = U_TYPED_CALLOC(struct ikh_hub);
	hub->config = *config;
	hub->log_level = ikh_log_level();
	hub->accept_fd = -1;
	hub->conn_fd = -1;
	hub->refcount = 1;

	hub->origin.type = XRT_TRACKING_TYPE_OTHER;
	hub->origin.initial_offset = (struct xrt_pose)XRT_POSE_IDENTITY;
	snprintf(hub->origin.name, sizeof(hub->origin.name), "IK Harness");

	if (os_mutex_init(&hub->mutex) != 0) {
		U_LOG_E("ikharness: failed to init mutex");
		free(hub);
		return XRT_ERROR_ALLOCATION;
	}

	build_device_table(hub);

	if (os_thread_helper_init(&hub->oth) != 0) {
		IKH_ERROR(hub, "Failed to init thread helper");
		os_mutex_destroy(&hub->mutex);
		free(hub);
		return XRT_ERROR_ALLOCATION;
	}
	if (os_thread_helper_start(&hub->oth, run_thread, hub) != 0) {
		IKH_ERROR(hub, "Failed to start server thread");
		os_thread_helper_destroy(&hub->oth);
		os_mutex_destroy(&hub->mutex);
		free(hub);
		return XRT_ERROR_ALLOCATION;
	}
	os_thread_helper_name(&hub->oth, "ikharness");

	u_var_add_root(hub, "IK Harness Hub", true);
	u_var_add_ro_u64(hub, &hub->last_frame_id, "last_frame_id");
	u_var_add_ro_u64(hub, &hub->frames_received, "frames_received");
	u_var_add_ro_u32(hub, &hub->clients_served, "clients_served");
	u_var_add_log_level(hub, &hub->log_level, "log_level");

	IKH_INFO(hub, "Created hub with %u devices (controllers=%s, trackers=%u)", hub->device_count,
	         config->controllers == IKH_CONTROLLER_INDEX    ? "index"
	         : config->controllers == IKH_CONTROLLER_SIMPLE ? "simple"
	                                                        : "none",
	         config->tracker_count);

	*out_hub = hub;
	return XRT_SUCCESS;
}

xrt_result_t
ikh_hub_create_devices(struct ikh_hub *hub,
                       struct xrt_device **out_xdevs,
                       uint32_t max_xdevs,
                       uint32_t *out_count,
                       struct xrt_device **out_head,
                       struct xrt_device **out_left,
                       struct xrt_device **out_right)
{
	*out_count = 0;
	*out_head = NULL;
	*out_left = NULL;
	*out_right = NULL;

	if (hub->device_count > max_xdevs) {
		IKH_ERROR(hub, "Too many devices (%u) for the system device list (%u)", hub->device_count, max_xdevs);
		return XRT_ERROR_DEVICE_CREATION_FAILED;
	}

	for (uint32_t i = 0; i < hub->device_count; i++) {
		struct xrt_device *xdev = NULL;
		switch (hub->descs[i].kind) {
		case IKH_DEVICE_HMD:
			xdev = ikh_hmd_create(hub, i);
			*out_head = xdev;
			break;
		case IKH_DEVICE_CONTROLLER_LEFT:
			xdev = ikh_controller_create(hub, i, hub->config.controllers, true);
			*out_left = xdev;
			break;
		case IKH_DEVICE_CONTROLLER_RIGHT:
			xdev = ikh_controller_create(hub, i, hub->config.controllers, false);
			*out_right = xdev;
			break;
		case IKH_DEVICE_TRACKER: xdev = ikh_tracker_create(hub, i); break;
		}
		if (xdev == NULL) {
			IKH_ERROR(hub, "Failed to create device %u", i);
			for (uint32_t j = 0; j < *out_count; j++) {
				xrt_device_destroy(&out_xdevs[j]);
			}
			*out_count = 0;
			return XRT_ERROR_DEVICE_CREATION_FAILED;
		}
		out_xdevs[(*out_count)++] = xdev;
	}

	return XRT_SUCCESS;
}
