// Copyright 2026, IK Harness contributors
// SPDX-License-Identifier: BSD-2-Clause
/*!
 * @file
 * @brief  Wire protocol between an IK-harness orchestrator and the Monado
 *         "ikharness" driver.
 *
 * This header has no Monado dependencies so it can be copied verbatim into
 * other clients (C#, GDScript, Python re-implement the same layout).
 *
 * Transport: a TCP stream (default 127.0.0.1:4343). Every message is a
 * 12-byte @ref ikh_msg_header followed by @c payload_size bytes of payload.
 * All integers are little-endian, all floats are IEEE-754 binary32, structs
 * are packed (no padding).
 *
 * Coordinate convention: OpenXR / Godot style. Right handed, +Y up, -Z
 * forward, meters. Quaternions are (x, y, z, w). All poses are expressed in
 * the runtime's STAGE space (floor-level, room scale origin). The driver
 * publishes them unchanged: no filtering, no prediction, no extrapolation.
 * A device holds its last received pose until a new frame replaces it.
 *
 * Handshake: right after a client connects the server sends IKH_MSG_HELLO
 * describing every device (index, kind, role, serial). The client then sends
 * IKH_MSG_FRAME messages; every applied frame is answered with IKH_MSG_ACK,
 * which lets an orchestrator know the poses are live before it waits on the
 * application under test.
 *
 * @ingroup drv_ikharness
 */

#pragma once

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

//! Bytes "IKH1" when written little-endian.
#define IKH_PROTOCOL_MAGIC 0x31484B49u
#define IKH_PROTOCOL_VERSION 1u
#define IKH_DEFAULT_PORT 4343
#define IKH_ROLE_NAME_LEN 32
#define IKH_SERIAL_LEN 32
#define IKH_MAX_DEVICES 64
//! Largest payload the server will accept, protects against garbage input.
#define IKH_MAX_PAYLOAD_SIZE (64u * 1024u)

enum ikh_msg_type
{
	IKH_MSG_HELLO = 1, //!< server -> client: device table (@ref ikh_msg_hello)
	IKH_MSG_FRAME = 2, //!< client -> server: poses (@ref ikh_msg_frame)
	IKH_MSG_ACK = 3,   //!< server -> client: frame applied (@ref ikh_msg_ack)
	IKH_MSG_PING = 4,  //!< client -> server: empty payload
	IKH_MSG_PONG = 5,  //!< server -> client: @ref ikh_msg_ack with the last applied frame
	IKH_MSG_QUERY = 6, //!< client -> server: empty payload, ask for HELLO again
};

enum ikh_device_kind
{
	IKH_DEVICE_HMD = 1,
	IKH_DEVICE_CONTROLLER_LEFT = 2,
	IKH_DEVICE_CONTROLLER_RIGHT = 3,
	IKH_DEVICE_TRACKER = 4,
};

/*!
 * Flags for @ref ikh_pose::flags. They map 1:1 onto OpenXR space location
 * flags, plus CONNECTED which drives the device's "active" state so a test can
 * simulate a tracker dropping out.
 */
enum ikh_pose_flags
{
	IKH_POSE_ORIENTATION_VALID = 1u << 0,
	IKH_POSE_POSITION_VALID = 1u << 1,
	IKH_POSE_TRACKED = 1u << 2,
	IKH_POSE_LINEAR_VELOCITY_VALID = 1u << 3,
	IKH_POSE_ANGULAR_VELOCITY_VALID = 1u << 4,
	IKH_POSE_CONNECTED = 1u << 5,

	IKH_POSE_DEFAULT = IKH_POSE_ORIENTATION_VALID | IKH_POSE_POSITION_VALID | IKH_POSE_TRACKED | IKH_POSE_CONNECTED,
};

#pragma pack(push, 1)

//! 12 bytes.
struct ikh_msg_header
{
	uint32_t magic;        //!< IKH_PROTOCOL_MAGIC
	uint16_t version;      //!< IKH_PROTOCOL_VERSION
	uint16_t type;         //!< enum ikh_msg_type
	uint32_t payload_size; //!< bytes following this header
};

//! 72 bytes.
struct ikh_device_desc
{
	uint32_t index;                //!< index used in @ref ikh_pose::index
	uint32_t kind;                 //!< enum ikh_device_kind
	char role[IKH_ROLE_NAME_LEN];  //!< NUL terminated, "" for HMD/controllers
	char serial[IKH_SERIAL_LEN];   //!< NUL terminated, the device serial as exposed to apps
};

//! 8 bytes, followed by @c device_count @ref ikh_device_desc.
struct ikh_msg_hello
{
	uint32_t device_count;
	uint32_t reserved;
};

//! 60 bytes.
struct ikh_pose
{
	uint32_t index;            //!< device index from HELLO
	uint32_t flags;            //!< enum ikh_pose_flags
	float position[3];         //!< meters, stage space
	float orientation[4];      //!< x, y, z, w
	float linear_velocity[3];  //!< m/s, stage space
	float angular_velocity[3]; //!< rad/s, stage space
};

//! 24 bytes, followed by @c pose_count @ref ikh_pose. Devices not listed keep their previous pose.
struct ikh_msg_frame
{
	uint64_t frame_id;    //!< echoed back in ACK; orchestrator chooses the numbering
	int64_t timestamp_ns; //!< 0 = stamp with the driver's monotonic clock on receipt
	uint32_t pose_count;
	uint32_t reserved;
};

//! 16 bytes.
struct ikh_msg_ack
{
	uint64_t frame_id;     //!< last applied frame (0 if none yet)
	int64_t applied_at_ns; //!< driver monotonic clock when applied
};

#pragma pack(pop)

#ifdef __cplusplus
}
#endif
