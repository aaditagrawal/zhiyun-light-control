"""Control helpers for Zhiyun MOLUS lights."""

from .artnet import (
    ArtDmxPacket,
    DmxMapping,
    decode_artdmx,
    encode_artdmx,
    scene_from_dmx,
)
from .async_client import AsyncProbeResult, AsyncZhiyunLight
from .bridge import LightConnectionConfig, PersistentLightFactory, make_light_factory
from .client import ProbeResult, ZhiyunLight
from .discovery import (
    DiscoveryAttempt,
    UsbDiscoveryReport,
    discover_usb_primitives,
)
from .models import CommandResult, Scene
from .presets import PresetError, ScenePresetLibrary, merge_scene, scene_from_mapping
from .protocol import (
    ParsedFrame,
    build_frame,
    build_runtime_frame,
    build_updater_frame,
    iter_frames,
)
from .sacn import SacnPacket, decode_sacn, encode_sacn, sacn_multicast_address
from .state import SceneState, SceneStateTracker
from .transitions import SceneTransition, interpolate_scene, scene_transition
from .transports.ble import BleExchangeResult, BleWorkerError, CrashIsolatedBleTransport
from .validation import (
    HardwareValidationReport,
    PrimitiveCheck,
    validate_async_light,
    validate_sync_light,
)

__all__ = [
    "AsyncZhiyunLight",
    "AsyncProbeResult",
    "ArtDmxPacket",
    "BleExchangeResult",
    "BleWorkerError",
    "CommandResult",
    "CrashIsolatedBleTransport",
    "DmxMapping",
    "DiscoveryAttempt",
    "LightConnectionConfig",
    "HardwareValidationReport",
    "PersistentLightFactory",
    "PrimitiveCheck",
    "PresetError",
    "ParsedFrame",
    "ProbeResult",
    "Scene",
    "SceneState",
    "SceneStateTracker",
    "SceneTransition",
    "ScenePresetLibrary",
    "SacnPacket",
    "UsbDiscoveryReport",
    "ZhiyunLight",
    "build_frame",
    "build_runtime_frame",
    "build_updater_frame",
    "decode_artdmx",
    "decode_sacn",
    "encode_artdmx",
    "encode_sacn",
    "iter_frames",
    "sacn_multicast_address",
    "make_light_factory",
    "merge_scene",
    "scene_from_mapping",
    "scene_from_dmx",
    "scene_transition",
    "interpolate_scene",
    "discover_usb_primitives",
    "validate_async_light",
    "validate_sync_light",
]
