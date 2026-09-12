"""ctypes bindings for Monado's ``XR_MNDX_xdev_space`` extension.

The extension exposes every device Monado knows about (including generic
trackers that have no standard OpenXR interaction profile) and lets an
application create an ``XrSpace`` that follows one of them. pyopenxr does not
ship these bindings, so they are declared here on top of
``xr.get_instance_proc_addr``.
"""

from __future__ import annotations

import ctypes as C
from dataclasses import dataclass
from typing import List

import xr

EXTENSION_NAME = "XR_MNDX_xdev_space"

XR_TYPE_CREATE_XDEV_LIST_INFO_MNDX = 1000444002
XR_TYPE_GET_XDEV_INFO_MNDX = 1000444003
XR_TYPE_XDEV_PROPERTIES_MNDX = 1000444004
XR_TYPE_CREATE_XDEV_SPACE_INFO_MNDX = 1000444005

XrXDevIdMNDX = C.c_uint64


class _XDevList_T(C.Structure):
    pass


XrXDevListMNDX = C.POINTER(_XDevList_T)


class XrCreateXDevListInfoMNDX(C.Structure):
    _fields_ = [("type", C.c_int), ("next", C.c_void_p)]


class XrGetXDevInfoMNDX(C.Structure):
    _fields_ = [("type", C.c_int), ("next", C.c_void_p), ("id", XrXDevIdMNDX)]


class XrXDevPropertiesMNDX(C.Structure):
    _fields_ = [
        ("type", C.c_int),
        ("next", C.c_void_p),
        ("name", C.c_char * 256),
        ("serial", C.c_char * 256),
        ("can_create_space", C.c_uint32),
    ]


class XrCreateXDevSpaceInfoMNDX(C.Structure):
    _fields_ = [
        ("type", C.c_int),
        ("next", C.c_void_p),
        ("xdev_list", XrXDevListMNDX),
        ("id", XrXDevIdMNDX),
        ("offset", xr.Posef),
    ]


_PFN_CreateList = C.CFUNCTYPE(C.c_int, xr.Session, C.POINTER(XrCreateXDevListInfoMNDX), C.POINTER(XrXDevListMNDX))
_PFN_GetGeneration = C.CFUNCTYPE(C.c_int, XrXDevListMNDX, C.POINTER(C.c_uint64))
_PFN_Enumerate = C.CFUNCTYPE(C.c_int, XrXDevListMNDX, C.c_uint32, C.POINTER(C.c_uint32), C.POINTER(XrXDevIdMNDX))
_PFN_GetProperties = C.CFUNCTYPE(C.c_int, XrXDevListMNDX, C.POINTER(XrGetXDevInfoMNDX), C.POINTER(XrXDevPropertiesMNDX))
_PFN_DestroyList = C.CFUNCTYPE(C.c_int, XrXDevListMNDX)
_PFN_CreateSpace = C.CFUNCTYPE(C.c_int, xr.Session, C.POINTER(XrCreateXDevSpaceInfoMNDX), C.POINTER(xr.Space))


def _check(result: int, what: str) -> None:
    exc = xr.check_result(xr.Result(result), what)
    if exc.is_exception():
        raise exc


@dataclass(frozen=True)
class XDev:
    id: int
    name: str
    serial: str
    can_create_space: bool


class XDevList:
    """A snapshot of Monado's device list for a session.

    Use as a context manager; spaces created from it stay valid for the
    session's lifetime.
    """

    def __init__(self, session: xr.Session):
        self.session = session
        instance = session.instance
        gpa = xr.get_instance_proc_addr
        self._create_list = C.cast(gpa(instance, "xrCreateXDevListMNDX"), _PFN_CreateList)
        self._get_generation = C.cast(gpa(instance, "xrGetXDevListGenerationNumberMNDX"), _PFN_GetGeneration)
        self._enumerate = C.cast(gpa(instance, "xrEnumerateXDevsMNDX"), _PFN_Enumerate)
        self._get_properties = C.cast(gpa(instance, "xrGetXDevPropertiesMNDX"), _PFN_GetProperties)
        self._destroy_list = C.cast(gpa(instance, "xrDestroyXDevListMNDX"), _PFN_DestroyList)
        self._create_space = C.cast(gpa(instance, "xrCreateXDevSpaceMNDX"), _PFN_CreateSpace)

        info = XrCreateXDevListInfoMNDX(type=XR_TYPE_CREATE_XDEV_LIST_INFO_MNDX, next=None)
        self.handle = XrXDevListMNDX()
        _check(self._create_list(session, C.byref(info), C.byref(self.handle)), "xrCreateXDevListMNDX")

    def __enter__(self) -> "XDevList":
        return self

    def __exit__(self, *exc) -> None:
        self.destroy()

    def destroy(self) -> None:
        if self.handle:
            _check(self._destroy_list(self.handle), "xrDestroyXDevListMNDX")
            self.handle = XrXDevListMNDX()

    @property
    def generation(self) -> int:
        gen = C.c_uint64(0)
        _check(self._get_generation(self.handle, C.byref(gen)), "xrGetXDevListGenerationNumberMNDX")
        return gen.value

    def devices(self) -> List[XDev]:
        count = C.c_uint32(0)
        _check(self._enumerate(self.handle, 0, C.byref(count), None), "xrEnumerateXDevsMNDX")
        ids = (XrXDevIdMNDX * max(count.value, 1))()
        _check(self._enumerate(self.handle, count.value, C.byref(count), ids), "xrEnumerateXDevsMNDX")
        out = []
        for i in range(count.value):
            info = XrGetXDevInfoMNDX(type=XR_TYPE_GET_XDEV_INFO_MNDX, next=None, id=ids[i])
            props = XrXDevPropertiesMNDX(type=XR_TYPE_XDEV_PROPERTIES_MNDX, next=None)
            _check(self._get_properties(self.handle, C.byref(info), C.byref(props)), "xrGetXDevPropertiesMNDX")
            out.append(
                XDev(
                    id=ids[i],
                    name=props.name.decode(errors="replace"),
                    serial=props.serial.decode(errors="replace"),
                    can_create_space=bool(props.can_create_space),
                )
            )
        return out

    def find_by_serial(self, serial: str) -> XDev:
        for d in self.devices():
            if d.serial == serial:
                return d
        raise KeyError(f"no xdev with serial {serial!r}")

    def create_space(self, device: XDev, offset: xr.Posef = None) -> xr.Space:
        if offset is None:
            offset = xr.Posef(orientation=xr.Quaternionf(0, 0, 0, 1), position=xr.Vector3f(0, 0, 0))
        info = XrCreateXDevSpaceInfoMNDX(
            type=XR_TYPE_CREATE_XDEV_SPACE_INFO_MNDX,
            next=None,
            xdev_list=self.handle,
            id=device.id,
            offset=offset,
        )
        space = xr.Space()
        _check(self._create_space(self.session, C.byref(info), C.byref(space)), "xrCreateXDevSpaceMNDX")
        space.instance = self.session.instance
        return space
