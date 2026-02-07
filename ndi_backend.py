from __future__ import annotations

import os
import ctypes
import ctypes.util
from dataclasses import dataclass
from typing import List, Optional, Generator, Tuple

import numpy as np

from config import AES67_SAMPLE_RATE, AES67_CHANNELS, AES67_SAMPLES_PER_PACKET

class NDIBackendError(RuntimeError):
    pass

# ------------------------------
# NDI SDK ctypes wrapper (minimal)
# ------------------------------

def _load_ndi_lib() -> ctypes.CDLL:
    env = os.getenv("NDI_LIB")
    if env and os.path.exists(env):
        return ctypes.CDLL(env)

    # Try system loader
    found = ctypes.util.find_library("ndi")
    if found:
        return ctypes.CDLL(found)

    # Common fallback names
    for name in ("libndi.so", "/usr/local/lib/libndi.so", "/usr/lib/libndi.so"):
        try:
            return ctypes.CDLL(name)
        except OSError:
            continue

    raise NDIBackendError("Could not load libndi. Set NDI_LIB=/path/to/libndi.so")

_lib = _load_ndi_lib()

# NDI structs based on NDI SDK headers (simplified)
class NDIlib_source_t(ctypes.Structure):
    _fields_ = [
        ("p_ndi_name", ctypes.c_char_p),
        ("p_url_address", ctypes.c_char_p),
    ]

class NDIlib_find_create_t(ctypes.Structure):
    _fields_ = [
        ("show_local_sources", ctypes.c_bool),
        ("p_groups", ctypes.c_char_p),
        ("p_extra_ips", ctypes.c_char_p),
    ]

class NDIlib_recv_color_format_e(ctypes.c_int):
    pass

class NDIlib_recv_bandwidth_e(ctypes.c_int):
    pass

class NDIlib_recv_create_v3_t(ctypes.Structure):
    _fields_ = [
        ("source_to_connect_to", NDIlib_source_t),
        ("color_format", NDIlib_recv_color_format_e),
        ("bandwidth", NDIlib_recv_bandwidth_e),
        ("allow_video_fields", ctypes.c_bool),
        ("p_ndi_recv_name", ctypes.c_char_p),
    ]

class NDIlib_frame_type_e(ctypes.c_int):
    pass

# frame types (from SDK)
NDIlib_frame_type_none = 0
NDIlib_frame_type_video = 1
NDIlib_frame_type_audio = 2
NDIlib_frame_type_metadata = 3
NDIlib_frame_type_error = 4
NDIlib_frame_type_status_change = 100

class NDIlib_audio_frame_v2_t(ctypes.Structure):
    _fields_ = [
        ("sample_rate", ctypes.c_int),
        ("no_channels", ctypes.c_int),
        ("no_samples", ctypes.c_int),
        ("timecode", ctypes.c_longlong),
        ("p_data", ctypes.POINTER(ctypes.c_float)),  # planar float32
        ("channel_stride_in_bytes", ctypes.c_int),
        ("p_metadata", ctypes.c_char_p),
        ("timestamp", ctypes.c_longlong),
    ]

# function prototypes (subset)
_lib.NDIlib_initialize.restype = ctypes.c_bool

_lib.NDIlib_find_create_v2.argtypes = [ctypes.POINTER(NDIlib_find_create_t)]
_lib.NDIlib_find_create_v2.restype = ctypes.c_void_p

_lib.NDIlib_find_destroy.argtypes = [ctypes.c_void_p]
_lib.NDIlib_find_destroy.restype = None

_lib.NDIlib_find_wait_for_sources.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
_lib.NDIlib_find_wait_for_sources.restype = ctypes.c_bool

_lib.NDIlib_find_get_current_sources.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
_lib.NDIlib_find_get_current_sources.restype = ctypes.POINTER(NDIlib_source_t)

_lib.NDIlib_recv_create_v3.argtypes = [ctypes.POINTER(NDIlib_recv_create_v3_t)]
_lib.NDIlib_recv_create_v3.restype = ctypes.c_void_p

_lib.NDIlib_recv_destroy.argtypes = [ctypes.c_void_p]
_lib.NDIlib_recv_destroy.restype = None

_lib.NDIlib_recv_connect.argtypes = [ctypes.c_void_p, ctypes.POINTER(NDIlib_source_t)]
_lib.NDIlib_recv_connect.restype = None

_lib.NDIlib_recv_capture_v2.argtypes = [
    ctypes.c_void_p,
    ctypes.c_void_p,  # video frame (unused)
    ctypes.POINTER(NDIlib_audio_frame_v2_t),
    ctypes.c_void_p,  # metadata frame (unused)
    ctypes.c_uint32
]
_lib.NDIlib_recv_capture_v2.restype = ctypes.c_int  # robust across SDK/ctypes versions

_lib.NDIlib_recv_free_audio_v2.argtypes = [ctypes.c_void_p, ctypes.POINTER(NDIlib_audio_frame_v2_t)]
_lib.NDIlib_recv_free_audio_v2.restype = None

# Initialize once
if not _lib.NDIlib_initialize():
    raise NDIBackendError("NDIlib_initialize() failed")

def list_sources(timeout_ms: int = 250) -> List[str]:
    """Discover available NDI sources on the network."""
    settings = NDIlib_find_create_t()
    settings.show_local_sources = True
    settings.p_groups = None
    settings.p_extra_ips = None

    finder = _lib.NDIlib_find_create_v2(ctypes.byref(settings))
    if not finder:
        raise NDIBackendError("NDIlib_find_create_v2 failed")

    try:
        _lib.NDIlib_find_wait_for_sources(finder, ctypes.c_uint32(timeout_ms))
        no_sources = ctypes.c_uint32(0)
        p_sources = _lib.NDIlib_find_get_current_sources(finder, ctypes.byref(no_sources))
        out: List[str] = []
        for i in range(int(no_sources.value)):
            name = p_sources[i].p_ndi_name.decode("utf-8", errors="ignore")
            out.append(name)
        return sorted(set(out))
    finally:
        _lib.NDIlib_find_destroy(finder)

def _lookup_source_url(name: str, timeout_ms: int = 500) -> Optional[str]:
    """Return url_address for a source if available (best-effort)."""
    settings = NDIlib_find_create_t()
    settings.show_local_sources = True
    settings.p_groups = None
    settings.p_extra_ips = None

    finder = _lib.NDIlib_find_create_v2(ctypes.byref(settings))
    if not finder:
        raise NDIBackendError("NDIlib_find_create_v2 failed")

    try:
        _lib.NDIlib_find_wait_for_sources(finder, ctypes.c_uint32(timeout_ms))
        no_sources = ctypes.c_uint32(0)
        p_sources = _lib.NDIlib_find_get_current_sources(finder, ctypes.byref(no_sources))
        for i in range(int(no_sources.value)):
            nm = p_sources[i].p_ndi_name.decode("utf-8", errors="ignore") if p_sources[i].p_ndi_name else ""
            if nm == name:
                if p_sources[i].p_url_address:
                    return p_sources[i].p_url_address.decode("utf-8", errors="ignore")
                return None
        return None
    finally:
        _lib.NDIlib_find_destroy(finder)

class NDIReceiver:
    """Receives audio from an NDI source and yields int32le interleaved stereo blocks of 48 samples/channel."""

    def __init__(self, source_name: str):
        self.source_name = source_name
        self._recv = None
        self._running = False

        # buffering to create exact 48-sample blocks
        self._buf_i32 = np.zeros((0, AES67_CHANNELS), dtype=np.int32)

    def connect(self) -> None:
        if self._recv:
            return

        # IMPORTANT:
        # Do NOT reuse NDIlib_source_t returned from NDIlib_find_get_current_sources after destroying the finder.
        # The SDK owns those pointers; they may become invalid. We build our own source struct with owned bytes.
        self._src_name_b = self.source_name.encode("utf-8")
        url = _lookup_source_url(self.source_name)
        self._src_url_b = url.encode("utf-8") if url else None

        src = NDIlib_source_t()
        src.p_ndi_name = ctypes.c_char_p(self._src_name_b)
        src.p_url_address = ctypes.c_char_p(self._src_url_b) if self._src_url_b else None

        create = NDIlib_recv_create_v3_t()
        create.source_to_connect_to = src

        # Enum values vary slightly by SDK version; 0 is typically a safe default.
        create.color_format = NDIlib_recv_color_format_e(0)
        create.bandwidth = NDIlib_recv_bandwidth_e(0)
        create.allow_video_fields = False
        create.p_ndi_recv_name = f"pi-aes67-{self.source_name}".encode("utf-8")

        self._recv = _lib.NDIlib_recv_create_v3(ctypes.byref(create))
        if not self._recv:
            raise NDIBackendError("NDIlib_recv_create_v3 failed")

        _lib.NDIlib_recv_connect(self._recv, ctypes.byref(src))
        self._running = True

    def close(self) -> None:
        self._running = False
        if self._recv:
            try:
                _lib.NDIlib_recv_destroy(self._recv)
            finally:
                self._recv = None

    def read_audio(self) -> Generator[Tuple[bytes, int], None, None]:
        """
        Yields (pcm_i32_le_bytes, samples_per_channel) where samples_per_channel is always 48.

        NDI audio v2 is typically planar float32. We:
          - validate 48kHz and >=2 channels
          - take first two channels
          - convert float32 [-1..1] to int32
          - interleave stereo
          - output in exact 48-sample blocks
        """
        if not self._recv:
            raise NDIBackendError("Receiver not connected")

        audio = NDIlib_audio_frame_v2_t()

        while self._running:
            frame_type = _lib.NDIlib_recv_capture_v2(self._recv, None, ctypes.byref(audio), None, 500)
            ft = int.from_bytes(frame_type[:4], 'little', signed=False) if isinstance(frame_type,(bytes,bytearray)) else int(frame_type)

            if ft == NDIlib_frame_type_audio:
                try:
                    if audio.sample_rate != AES67_SAMPLE_RATE:
                        # For now, refuse (production: resample)
                        continue
                    if audio.no_channels < AES67_CHANNELS:
                        continue
                    if audio.no_samples <= 0 or not audio.p_data:
                        continue

                    # Build numpy view of planar float32: shape (channels, samples)
                    stride_floats = audio.channel_stride_in_bytes // 4
                    total_floats = stride_floats * audio.no_channels
                    buf = np.ctypeslib.as_array(audio.p_data, shape=(total_floats,))
                    planar = buf.reshape((audio.no_channels, stride_floats))[:, :audio.no_samples]

                    stereo = planar[:2, :].T  # (samples, 2)
                    # float32 -> int32 (full-scale)
                    i32 = np.clip(stereo * 2147483647.0, -2147483648.0, 2147483647.0).astype(np.int32)

                    # append to buffer
                    if self._buf_i32.size == 0:
                        self._buf_i32 = i32
                    else:
                        self._buf_i32 = np.vstack([self._buf_i32, i32])

                    # emit in 48-sample blocks
                    while self._buf_i32.shape[0] >= AES67_SAMPLES_PER_PACKET:
                        block = self._buf_i32[:AES67_SAMPLES_PER_PACKET, :]
                        self._buf_i32 = self._buf_i32[AES67_SAMPLES_PER_PACKET:, :]

                        # interleaved int32 LE bytes
                        pcm_i32_le = block.astype('<i4', copy=False).tobytes(order='C')
                        yield (pcm_i32_le, AES67_SAMPLES_PER_PACKET)

                finally:
                    _lib.NDIlib_recv_free_audio_v2(self._recv, ctypes.byref(audio))

            elif ft in (NDIlib_frame_type_none, NDIlib_frame_type_metadata, NDIlib_frame_type_status_change):
                continue
            elif ft == NDIlib_frame_type_error:
                break
