// Copyright 2026, IK Harness contributors
// SPDX-License-Identifier: BSD-2-Clause
/*!
 * @file
 * @brief  A stand-in for SteamVR's vrserver, enough to load Monado's SteamVR plugin
 *         (driver_monado.so) and see what it would tell SteamVR: which devices it adds,
 *         their properties and poses, and the HMD's projection.
 *
 * SteamVR is closed and large; this lets the plugin path be tested without it. Usage:
 *
 *     steamvr_mock_host /path/to/driver_monado.so [frames]
 *
 * prints `{"event": "ready", ...}` once the driver is initialised and every device is
 * activated, waits for one line on stdin (so a test can feed poses to the ikharness driver
 * first), runs `frames` frames and prints one JSON document with everything it recorded.
 *
 * Build: g++ -std=c++17 -I <monado>/src/external/openvr_includes steamvr_mock_host.cpp -ldl -lpthread
 */

#include "openvr_driver.h"

#include <dlfcn.h>
#include <unistd.h>

#include <cstdio>
#include <cstring>
#include <map>
#include <mutex>
#include <sstream>
#include <string>
#include <vector>

using namespace vr;

namespace {

std::mutex g_lock;

struct Device
{
	std::string serial;
	ETrackedDeviceClass device_class;
	ITrackedDeviceServerDriver *driver;
	bool activated = false;
	EVRInitError activate_error = VRInitError_None;
	bool has_pose = false;
	DriverPose_t pose = {};
	uint32_t pose_updates = 0;
	std::map<int, std::string> props;
	uint32_t input_components = 0;
};

std::vector<Device> g_devices;
std::map<std::string, std::string> g_settings;
std::vector<std::string> g_log;

std::string
json_escape(const std::string &in)
{
	std::string out;
	for (char c : in) {
		if (c == '"' || c == '\\') {
			out += '\\';
			out += c;
		} else if (c == '\n') {
			out += "\\n";
		} else if ((unsigned char)c < 0x20) {
			out += ' ';
		} else {
			out += c;
		}
	}
	return out;
}

class MockLog : public IVRDriverLog
{
public:
	void
	Log(const char *pchLogMessage) override
	{
		std::lock_guard<std::mutex> guard(g_lock);
		g_log.push_back(pchLogMessage);
	}
};

class MockHost : public IVRServerDriverHost
{
public:
	bool
	TrackedDeviceAdded(const char *serial, ETrackedDeviceClass device_class, ITrackedDeviceServerDriver *driver) override
	{
		std::lock_guard<std::mutex> guard(g_lock);
		Device d;
		d.serial = serial;
		d.device_class = device_class;
		d.driver = driver;
		g_devices.push_back(d);
		return true;
	}
	void
	TrackedDevicePoseUpdated(uint32_t which, const DriverPose_t &pose, uint32_t size) override
	{
		std::lock_guard<std::mutex> guard(g_lock);
		if (which < g_devices.size() && size == sizeof(DriverPose_t)) {
			g_devices[which].pose = pose;
			g_devices[which].has_pose = true;
			g_devices[which].pose_updates++;
		}
	}
	void VsyncEvent(double) override {}
	void VendorSpecificEvent(uint32_t, EVREventType, const VREvent_Data_t &, double) override {}
	bool IsExiting() override { return false; }
	bool PollNextEvent(VREvent_t *, uint32_t) override { return false; }
	void GetRawTrackedDevicePoses(float, TrackedDevicePose_t *poses, uint32_t count) override
	{
		memset(poses, 0, sizeof(TrackedDevicePose_t) * count);
	}
	void RequestRestart(const char *, const char *, const char *, const char *) override {}
	uint32_t GetFrameTimings(Compositor_FrameTiming *, uint32_t) override { return 0; }
	void SetDisplayEyeToHead(uint32_t, const HmdMatrix34_t &, const HmdMatrix34_t &) override {}
	void SetDisplayProjectionRaw(uint32_t, const HmdRect2_t &, const HmdRect2_t &) override {}
	void SetRecommendedRenderTargetSize(uint32_t, uint32_t, uint32_t) override {}
};

class MockProperties : public IVRProperties
{
public:
	ETrackedPropertyError
	ReadPropertyBatch(PropertyContainerHandle_t, PropertyRead_t *batch, uint32_t count) override
	{
		for (uint32_t i = 0; i < count; i++) {
			batch[i].unRequiredBufferSize = 0;
			batch[i].eError = TrackedProp_UnknownProperty;
		}
		return TrackedProp_Success;
	}
	ETrackedPropertyError
	WritePropertyBatch(PropertyContainerHandle_t container, PropertyWrite_t *batch, uint32_t count) override
	{
		std::lock_guard<std::mutex> guard(g_lock);
		for (uint32_t i = 0; i < count; i++) {
			batch[i].eError = TrackedProp_Success;
			if (container == 0 || container > g_devices.size() || batch[i].writeType != PropertyWrite_Set) {
				continue;
			}
			std::ostringstream value;
			const void *buf = batch[i].pvBuffer;
			switch (batch[i].unTag) {
			case k_unStringPropertyTag: value << std::string((const char *)buf); break;
			case k_unInt32PropertyTag: value << *(const int32_t *)buf; break;
			case k_unUint64PropertyTag: value << *(const uint64_t *)buf; break;
			case k_unBoolPropertyTag: value << (*(const bool *)buf ? "true" : "false"); break;
			case k_unFloatPropertyTag: value << *(const float *)buf; break;
			default: value << "<tag " << batch[i].unTag << ">"; break;
			}
			g_devices[container - 1].props[(int)batch[i].prop] = value.str();
		}
		return TrackedProp_Success;
	}
	const char *GetPropErrorNameFromEnum(ETrackedPropertyError) override { return "mock"; }
	PropertyContainerHandle_t TrackedDeviceToPropertyContainer(TrackedDeviceIndex_t device) override { return device + 1; }
};

class MockSettings : public IVRSettings
{
public:
	const char *GetSettingsErrorNameFromEnum(EVRSettingsError) override { return "mock"; }
	void SetBool(const char *s, const char *k, bool v, EVRSettingsError *e) override { set(s, k, v ? "true" : "false", e); }
	void SetInt32(const char *s, const char *k, int32_t v, EVRSettingsError *e) override { set(s, k, std::to_string(v), e); }
	void SetFloat(const char *s, const char *k, float v, EVRSettingsError *e) override { set(s, k, std::to_string(v), e); }
	void SetString(const char *s, const char *k, const char *v, EVRSettingsError *e) override { set(s, k, v, e); }
	bool GetBool(const char *, const char *, EVRSettingsError *e) override { return unset(e), false; }
	int32_t GetInt32(const char *, const char *, EVRSettingsError *e) override { return unset(e), 0; }
	float GetFloat(const char *, const char *, EVRSettingsError *e) override { return unset(e), 0.0f; }
	void GetString(const char *, const char *, char *value, uint32_t len, EVRSettingsError *e) override
	{
		if (len > 0) {
			value[0] = 0;
		}
		unset(e);
	}
	void RemoveSection(const char *, EVRSettingsError *e) override { ok(e); }
	void RemoveKeyInSection(const char *, const char *, EVRSettingsError *e) override { ok(e); }

private:
	static void ok(EVRSettingsError *e) { if (e) { *e = VRSettingsError_None; } }
	static void unset(EVRSettingsError *e) { if (e) { *e = VRSettingsError_UnsetSettingHasNoDefault; } }
	static void set(const char *section, const char *key, const std::string &value, EVRSettingsError *e)
	{
		std::lock_guard<std::mutex> guard(g_lock);
		g_settings[std::string(section) + "|" + key] = value;
		ok(e);
	}
};

class MockInput : public IVRDriverInput
{
public:
	EVRInputError CreateBooleanComponent(PropertyContainerHandle_t c, const char *, VRInputComponentHandle_t *h) override { return create(c, h); }
	EVRInputError UpdateBooleanComponent(VRInputComponentHandle_t, bool, double) override { return VRInputError_None; }
	EVRInputError CreateScalarComponent(PropertyContainerHandle_t c, const char *, VRInputComponentHandle_t *h, EVRScalarType, EVRScalarUnits) override { return create(c, h); }
	EVRInputError UpdateScalarComponent(VRInputComponentHandle_t, float, double) override { return VRInputError_None; }
	EVRInputError CreateHapticComponent(PropertyContainerHandle_t c, const char *, VRInputComponentHandle_t *h) override { return create(c, h); }
	EVRInputError CreateSkeletonComponent(PropertyContainerHandle_t c, const char *, const char *, const char *, EVRSkeletalTrackingLevel, const VRBoneTransform_t *, uint32_t, VRInputComponentHandle_t *h) override { return create(c, h); }
	EVRInputError UpdateSkeletonComponent(VRInputComponentHandle_t, EVRSkeletalMotionRange, const VRBoneTransform_t *, uint32_t) override { return VRInputError_None; }
	EVRInputError CreatePoseComponent(PropertyContainerHandle_t c, const char *, VRInputComponentHandle_t *h) override { return create(c, h); }
	EVRInputError UpdatePoseComponent(VRInputComponentHandle_t, const HmdMatrix34_t *, double) override { return VRInputError_None; }
	EVRInputError CreateEyeTrackingComponent(PropertyContainerHandle_t c, const char *, VRInputComponentHandle_t *h) override { return create(c, h); }
	EVRInputError UpdateEyeTrackingComponent(VRInputComponentHandle_t, const VREyeTrackingData_t *, double) override { return VRInputError_None; }

private:
	uint64_t m_next = 1;
	EVRInputError
	create(PropertyContainerHandle_t container, VRInputComponentHandle_t *handle)
	{
		std::lock_guard<std::mutex> guard(g_lock);
		*handle = m_next++;
		if (container >= 1 && container <= g_devices.size()) {
			g_devices[container - 1].input_components++;
		}
		return VRInputError_None;
	}
};

// Required by the driver context's start-up check, never called by the plugin.
class MockDriverManager : public IVRDriverManager
{
public:
	uint32_t GetDriverCount() const override { return 1; }
	uint32_t
	GetDriverName(DriverId_t, char *value, uint32_t size) override
	{
		snprintf(value, size, "monado");
		return 7;
	}
	DriverHandle_t GetDriverHandle(const char *) override { return 1; }
	bool IsEnabled(DriverId_t) const override { return true; }
};

class MockResources : public IVRResources
{
public:
	uint32_t LoadSharedResource(const char *, char *, uint32_t) override { return 0; }
	uint32_t GetResourceFullPath(const char *, const char *, char *buffer, uint32_t size) override
	{
		if (size > 0) {
			buffer[0] = 0;
		}
		return 0;
	}
};

MockDriverManager g_mock_manager;
MockResources g_mock_resources;
MockLog g_mock_log;
MockHost g_mock_host;
MockProperties g_mock_properties;
MockSettings g_mock_settings;
MockInput g_mock_input;

class MockContext : public IVRDriverContext
{
public:
	void *
	GetGenericInterface(const char *version, EVRInitError *error) override
	{
		void *found = nullptr;
		if (strcmp(version, IVRServerDriverHost_Version) == 0) {
			found = static_cast<IVRServerDriverHost *>(&g_mock_host);
		} else if (strcmp(version, IVRProperties_Version) == 0) {
			found = static_cast<IVRProperties *>(&g_mock_properties);
		} else if (strcmp(version, IVRSettings_Version) == 0) {
			found = static_cast<IVRSettings *>(&g_mock_settings);
		} else if (strcmp(version, IVRDriverInput_Version) == 0) {
			found = static_cast<IVRDriverInput *>(&g_mock_input);
		} else if (strcmp(version, IVRDriverLog_Version) == 0) {
			found = static_cast<IVRDriverLog *>(&g_mock_log);
		} else if (strcmp(version, IVRDriverManager_Version) == 0) {
			found = static_cast<IVRDriverManager *>(&g_mock_manager);
		} else if (strcmp(version, IVRResources_Version) == 0) {
			found = static_cast<IVRResources *>(&g_mock_resources);
		}
		if (error != nullptr) {
			*error = found != nullptr ? VRInitError_None : VRInitError_Init_InterfaceNotFound;
		}
		return found;
	}
	DriverHandle_t GetDriverHandle() override { return 1; }
};

const char *
class_name(ETrackedDeviceClass c)
{
	switch (c) {
	case TrackedDeviceClass_HMD: return "hmd";
	case TrackedDeviceClass_Controller: return "controller";
	case TrackedDeviceClass_GenericTracker: return "generic_tracker";
	case TrackedDeviceClass_TrackingReference: return "tracking_reference";
	default: return "other";
	}
}

std::string
prop(const Device &d, int id)
{
	auto it = d.props.find(id);
	return it != d.props.end() ? it->second : "";
}

} // namespace

int
main(int argc, char **argv)
{
	if (argc < 2) {
		fprintf(stderr, "usage: %s /path/to/driver_monado.so [frames]\n", argv[0]);
		return 2;
	}
	int frames = argc > 2 ? atoi(argv[2]) : 20;

	void *lib = dlopen(argv[1], RTLD_NOW | RTLD_GLOBAL);
	if (lib == nullptr) {
		fprintf(stderr, "dlopen failed: %s\n", dlerror());
		return 1;
	}
	typedef void *(*factory_fn)(const char *, int *);
	factory_fn factory = (factory_fn)dlsym(lib, "HmdDriverFactory");
	if (factory == nullptr) {
		fprintf(stderr, "no HmdDriverFactory in %s\n", argv[1]);
		return 1;
	}
	int rc = 0;
	IServerTrackedDeviceProvider *provider = (IServerTrackedDeviceProvider *)factory(IServerTrackedDeviceProvider_Version, &rc);
	if (provider == nullptr) {
		fprintf(stderr, "driver has no %s (code %d)\n", IServerTrackedDeviceProvider_Version, rc);
		return 1;
	}

	MockContext context;
	EVRInitError init = provider->Init(&context);
	if (init != VRInitError_None) {
		printf("{\"event\": \"init_failed\", \"error\": %d}\n", (int)init);
		return 1;
	}

	// vrserver activates each device after it was added.
	size_t count;
	{
		std::lock_guard<std::mutex> guard(g_lock);
		count = g_devices.size();
	}
	for (size_t i = 0; i < count; i++) {
		ITrackedDeviceServerDriver *driver = g_devices[i].driver;
		EVRInitError err = driver->Activate((uint32_t)i);
		std::lock_guard<std::mutex> guard(g_lock);
		g_devices[i].activate_error = err;
		g_devices[i].activated = err == VRInitError_None;
	}

	printf("{\"event\": \"ready\", \"devices\": %zu}\n", count);
	fflush(stdout);
	char line[64];
	if (fgets(line, sizeof(line), stdin) == nullptr) {
		// no stdin: just go on
	}

	for (int f = 0; f < frames; f++) {
		provider->RunFrame();
		usleep(20 * 1000);
	}

	std::ostringstream out;
	out << "{\"event\": \"report\", \"frames\": " << frames << ", \"devices\": [";
	{
		std::lock_guard<std::mutex> guard(g_lock);
		for (size_t i = 0; i < g_devices.size(); i++) {
			const Device &d = g_devices[i];
			out << (i ? ", " : "") << "{\"index\": " << i << ", \"serial\": \"" << json_escape(d.serial) << "\", \"class\": \""
			    << class_name(d.device_class) << "\", \"activated\": " << (d.activated ? "true" : "false")
			    << ", \"activate_error\": " << (int)d.activate_error << ", \"input_components\": " << d.input_components
			    << ", \"controller_type\": \"" << json_escape(prop(d, Prop_ControllerType_String)) << "\""
			    << ", \"model\": \"" << json_escape(prop(d, Prop_ModelNumber_String)) << "\""
			    << ", \"render_model\": \"" << json_escape(prop(d, Prop_RenderModelName_String)) << "\""
			    << ", \"device_class_prop\": \"" << json_escape(prop(d, Prop_DeviceClass_Int32)) << "\""
			    << ", \"pose_updates\": " << d.pose_updates;
			if (d.has_pose) {
				out << ", \"pose\": {\"position\": [" << d.pose.vecPosition[0] << ", " << d.pose.vecPosition[1] << ", "
				    << d.pose.vecPosition[2] << "], \"rotation_xyzw\": [" << d.pose.qRotation.x << ", " << d.pose.qRotation.y
				    << ", " << d.pose.qRotation.z << ", " << d.pose.qRotation.w << "], \"valid\": "
				    << (d.pose.poseIsValid ? "true" : "false") << ", \"connected\": "
				    << (d.pose.deviceIsConnected ? "true" : "false") << ", \"result\": " << (int)d.pose.result << "}";
			}
			out << "}";
		}
	}
	out << "]";

	// What the HMD tells the compositor about its optics.
	for (size_t i = 0; i < count; i++) {
		if (g_devices[i].device_class != TrackedDeviceClass_HMD) {
			continue;
		}
		IVRDisplayComponent *display = (IVRDisplayComponent *)g_devices[i].driver->GetComponent(IVRDisplayComponent_Version);
		if (display == nullptr) {
			continue;
		}
		uint32_t w = 0, h = 0, x = 0, y = 0;
		int32_t wx = 0, wy = 0;
		display->GetRecommendedRenderTargetSize(&w, &h);
		out << ", \"hmd\": {\"recommended_render_target\": [" << w << ", " << h << "]";
		display->GetWindowBounds(&wx, &wy, &w, &h);
		out << ", \"window_bounds\": [" << wx << ", " << wy << ", " << w << ", " << h << "]";
		out << ", \"eyes\": [";
		for (int eye = 0; eye < 2; eye++) {
			float l = 0, r = 0, t = 0, b = 0;
			display->GetProjectionRaw((EVREye)eye, &l, &r, &t, &b);
			display->GetEyeOutputViewport((EVREye)eye, &x, &y, &w, &h);
			DistortionCoordinates_t centre = display->ComputeDistortion((EVREye)eye, 0.5f, 0.5f);
			out << (eye ? ", " : "") << "{\"projection_raw\": [" << l << ", " << r << ", " << t << ", " << b << "], \"viewport\": ["
			    << x << ", " << y << ", " << w << ", " << h << "], \"distortion_centre_green\": [" << centre.rfGreen[0] << ", "
			    << centre.rfGreen[1] << "]}";
		}
		out << "]}";
	}

	out << ", \"settings\": {";
	{
		std::lock_guard<std::mutex> guard(g_lock);
		bool first = true;
		for (const auto &kv : g_settings) {
			out << (first ? "" : ", ") << "\"" << json_escape(kv.first) << "\": \"" << json_escape(kv.second) << "\"";
			first = false;
		}
		out << "}, \"log_lines\": " << g_log.size();
	}
	out << "}";
	printf("%s\n", out.str().c_str());
	fflush(stdout);

	for (size_t i = 0; i < count; i++) {
		if (g_devices[i].activated) {
			g_devices[i].driver->Deactivate();
		}
	}
	// The plugin's Cleanup tears the whole runtime down; results are out already.
	provider->Cleanup();
	return 0;
}
