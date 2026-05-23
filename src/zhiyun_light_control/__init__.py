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
    DEFAULT_DISCOVERY_CONTROL_FIRST_WORDS,
    DEFAULT_DISCOVERY_CONTROL_MODES,
    DEFAULT_DISCOVERY_FIRST_WORDS,
    DEFAULT_DISCOVERY_OBJECT_IDS,
    DiscoveryAttempt,
    UsbDiscoveryReport,
    discover_usb_primitives,
)
from .models import CommandResult, Scene
from .presets import PresetError, ScenePresetLibrary, merge_scene, scene_from_mapping
from .protocol import (
    DEFAULT_CONTROL_MODE,
    LEGACY_CONTROL_MODE,
    ParsedFrame,
    build_frame,
    build_runtime_frame,
    build_updater_frame,
    iter_frames,
)
from .sacn import SacnPacket, decode_sacn, encode_sacn, sacn_multicast_address
from .state import SceneState, SceneStateTracker
from .status import LightStatusReport, read_async_status, read_sync_status
from .transitions import SceneTransition, interpolate_scene, scene_transition
from .transports.ble import (
    BLE_PROFILE_NAMES,
    BLE_PROFILES,
    DEFAULT_BLE_PROFILE,
    BleExchangeResult,
    BleProfile,
    BleWorkerError,
    CrashIsolatedBleTransport,
    MacosBleAppTransport,
    resolve_ble_profile,
    scan_zhiyun_devices_macos_app,
)
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
    "BLE_PROFILE_NAMES",
    "BLE_PROFILES",
    "BleExchangeResult",
    "BleProfile",
    "BleWorkerError",
    "CommandResult",
    "CrashIsolatedBleTransport",
    "DEFAULT_BLE_PROFILE",
    "DEFAULT_CONTROL_MODE",
    "DEFAULT_DISCOVERY_CONTROL_MODES",
    "DEFAULT_DISCOVERY_CONTROL_FIRST_WORDS",
    "DEFAULT_DISCOVERY_FIRST_WORDS",
    "DEFAULT_DISCOVERY_OBJECT_IDS",
    "LEGACY_CONTROL_MODE",
    "DmxMapping",
    "DiscoveryAttempt",
    "LightConnectionConfig",
    "LightStatusReport",
    "HardwareValidationReport",
    "MacosBleAppTransport",
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
    "read_async_status",
    "read_sync_status",
    "resolve_ble_profile",
    "scan_zhiyun_devices_macos_app",
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
