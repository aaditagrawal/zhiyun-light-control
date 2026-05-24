"""macOS CoreBluetooth app-bundle helper.

CoreBluetooth can terminate non-bundled command-line processes at the TCC
privacy boundary before Python can catch an exception. This module builds a
small temporary .app wrapper with an NSBluetoothAlwaysUsageDescription entry and
runs a Swift helper inside that bundle.
"""

from __future__ import annotations

import json
import plistlib
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

APP_BUNDLE_NAME = "ZhiyunBleScan"
APP_BUNDLE_ID = "local.zhiyun-light-control.ble-scan"
BLUETOOTH_USAGE = "Scan nearby Zhiyun lights for local control."
BLUETOOTH_SETTINGS_URL = (
    "x-apple.systempreferences:com.apple.preference.security?Privacy_Bluetooth"
)


@dataclass(frozen=True)
class MacosBleAppRun:
    ok: bool
    payload: dict[str, object]
    error: str | None = None
    returncode: int | None = None
    command: tuple[str, ...] = ()


def macos_ble_app_info(
    *,
    bundle_name: str = APP_BUNDLE_NAME,
    ensure: bool = False,
    swift_path: str | None = None,
) -> dict[str, object]:
    app_path = _bundle_root(bundle_name)
    error: str | None = None
    if ensure:
        if sys.platform != "darwin":
            error = "macOS BLE app helper can only be built on macOS"
        else:
            resolved_swiftc = swift_path or _find_swiftc()
            if resolved_swiftc is None:
                error = "Swift compiler not found"
            else:
                try:
                    app_path = ensure_macos_ble_app(
                        bundle_name=bundle_name,
                        swift_path=resolved_swiftc,
                    )
                except RuntimeError as exc:
                    error = str(exc)
    return {
        "ok": error is None,
        "available": sys.platform == "darwin",
        "bundle_name": bundle_name,
        "bundle_id": APP_BUNDLE_ID,
        "app_path": str(app_path),
        "exists": app_path.exists(),
        "usage_description": BLUETOOTH_USAGE,
        "settings_url": BLUETOOTH_SETTINGS_URL,
        "settings_hint": (
            f"Allow {bundle_name} in macOS Privacy & Security > Bluetooth, "
            "then rerun the BLE scan."
        ),
        "status_command": "zlight ble-helper --status --json",
        "authorize_command": "zlight ble-helper --ensure --open-settings",
        "error": error,
    }


def macos_ble_app_status(
    *,
    timeout: float = 3.0,
    bundle_name: str = APP_BUNDLE_NAME,
) -> dict[str, object]:
    run = run_macos_ble_app(["status"], timeout=timeout, bundle_name=bundle_name)
    status = dict(run.payload)
    if "ok" not in status:
        status["ok"] = bool(run.ok)
    if run.error is not None and status.get("error") is None:
        status["error"] = run.error
    status.update(
        {
            "bundle_name": bundle_name,
            "bundle_id": APP_BUNDLE_ID,
            "app_path": str(_bundle_root(bundle_name)),
            "returncode": run.returncode,
            "settings_url": BLUETOOTH_SETTINGS_URL,
            "settings_hint": (
                f"Allow {bundle_name} in macOS Privacy & Security > Bluetooth, "
                "then rerun the BLE scan."
            ),
        }
    )
    if run.command:
        status["command"] = list(run.command)
    return status


def open_macos_bluetooth_settings() -> dict[str, object]:
    if sys.platform != "darwin":
        return {"ok": False, "error": "Bluetooth settings helper requires macOS"}
    open_path = shutil.which("open")
    if open_path is None:
        return {"ok": False, "error": "macOS open command not found"}
    proc = subprocess.run(
        [open_path, BLUETOOTH_SETTINGS_URL],
        capture_output=True,
        text=True,
        check=False,
    )
    error = proc.stderr.strip() or proc.stdout.strip() or None
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "settings_url": BLUETOOTH_SETTINGS_URL,
        "error": None if proc.returncode == 0 else error,
    }


def run_macos_ble_app(
    args: list[str],
    *,
    timeout: float,
    bundle_name: str = APP_BUNDLE_NAME,
) -> MacosBleAppRun:
    if sys.platform != "darwin":
        return MacosBleAppRun(
            ok=False,
            payload={},
            error="macOS BLE app backend requires macOS",
        )
    open_path = shutil.which("open")
    swiftc_path = _find_swiftc()
    if open_path is None:
        return MacosBleAppRun(
            ok=False,
            payload={},
            error="macOS open command not found",
        )
    if swiftc_path is None:
        return MacosBleAppRun(ok=False, payload={}, error="Swift compiler not found")
    try:
        app_path = ensure_macos_ble_app(
            bundle_name=bundle_name,
            swift_path=swiftc_path,
        )
    except RuntimeError as exc:
        return MacosBleAppRun(ok=False, payload={}, error=str(exc))
    with tempfile.TemporaryDirectory(prefix="zhiyun-ble-") as tmp:
        output = Path(tmp) / "result.json"
        command = [
            open_path,
            "-W",
            "-n",
            str(app_path),
            "--args",
            *args,
            "--output",
            str(output),
        ]
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=max(timeout + 15.0, 20.0),
                check=False,
            )
        except subprocess.TimeoutExpired:
            _terminate_helper(bundle_name)
            return MacosBleAppRun(
                ok=False,
                payload={},
                error=f"macOS BLE app timed out after {max(timeout + 15.0, 20.0):g}s",
                returncode=None,
                command=tuple(command),
            )
        if not output.exists():
            error = proc.stderr.strip() or proc.stdout.strip() or "no JSON output"
            return MacosBleAppRun(
                ok=False,
                payload={},
                error=error,
                returncode=proc.returncode,
                command=tuple(command),
            )
        try:
            payload = json.loads(output.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return MacosBleAppRun(
                ok=False,
                payload={},
                error=f"could not parse macOS BLE app output: {exc}",
                returncode=proc.returncode,
                command=tuple(command),
            )
    if not isinstance(payload, dict):
        return MacosBleAppRun(
            ok=False,
            payload={},
            error="macOS BLE app output was not a JSON object",
            returncode=proc.returncode,
            command=tuple(command),
        )
    error_value = payload.get("error")
    return MacosBleAppRun(
        ok=proc.returncode == 0 and error_value is None,
        payload=payload,
        error=str(error_value) if error_value is not None else None,
        returncode=proc.returncode,
        command=tuple(command),
    )


def ensure_macos_ble_app(
    *,
    bundle_name: str = APP_BUNDLE_NAME,
    swift_path: str = "/usr/bin/swiftc",
) -> Path:
    app_path = _bundle_root(bundle_name)
    contents = app_path / "Contents"
    macos = contents / "MacOS"
    resources = contents / "Resources"
    macos.mkdir(parents=True, exist_ok=True)
    resources.mkdir(parents=True, exist_ok=True)
    _write_if_changed(contents / "Info.plist", _info_plist(bundle_name))
    helper = resources / "helper.swift"
    source_changed = _write_if_changed(helper, _swift_source().encode("utf-8"))
    launcher = macos / bundle_name
    if source_changed or not launcher.exists():
        proc = subprocess.run(
            [swift_path, str(helper), "-o", str(launcher)],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            error = proc.stderr.strip() or proc.stdout.strip()
            raise RuntimeError(f"could not compile macOS BLE helper: {error}")
    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR)
    return app_path


def _bundle_root(bundle_name: str) -> Path:
    cache = Path.home() / "Library" / "Caches" / "zhiyun-light-control"
    return cache / f"{bundle_name}.app"


def _info_plist(bundle_name: str) -> bytes:
    return plistlib.dumps(
        {
            "CFBundleDevelopmentRegion": "en",
            "CFBundleExecutable": bundle_name,
            "CFBundleIdentifier": APP_BUNDLE_ID,
            "CFBundleInfoDictionaryVersion": "6.0",
            "CFBundleName": bundle_name,
            "CFBundlePackageType": "APPL",
            "CFBundleShortVersionString": "0.1.0",
            "CFBundleVersion": "1",
            "NSBluetoothAlwaysUsageDescription": BLUETOOTH_USAGE,
            "NSBluetoothPeripheralUsageDescription": BLUETOOTH_USAGE,
        },
        sort_keys=True,
    )


def _write_if_changed(path: Path, data: bytes) -> bool:
    if path.exists() and path.read_bytes() == data:
        return False
    path.write_bytes(data)
    return True


def _find_swiftc() -> str | None:
    direct = shutil.which("swiftc")
    if direct is not None:
        return direct
    xcrun = shutil.which("xcrun")
    if xcrun is None:
        return None
    proc = subprocess.run(
        [xcrun, "--find", "swiftc"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    value = proc.stdout.strip()
    return value or None


def _terminate_helper(bundle_name: str) -> None:
    pkill_path = shutil.which("pkill")
    if pkill_path is None:
        return
    pattern = f"{bundle_name}.app/Contents/.+helper.swift"
    subprocess.run(
        [pkill_path, "-f", pattern],
        capture_output=True,
        text=True,
        check=False,
    )


def _swift_source() -> str:
    advertisement_data_type = "[String : " + "A" + "ny]"
    return f"""import CoreBluetooth
import Darwin
import Foundation

struct JsonDevice: Encodable {{
    let address: String
    let name: String?
    let rssi: Int?
    let services: [String]
}}

struct ScanOutput: Encodable {{
    let devices: [JsonDevice]
    let error: String?
}}

struct JsonCharacteristic: Encodable {{
    let uuid: String
    let properties: [String]
}}

struct JsonService: Encodable {{
    let uuid: String
    let characteristics: [JsonCharacteristic]
}}

struct InspectOutput: Encodable {{
    let address: String?
    let services: [JsonService]
    let error: String?
}}

struct ExchangeOutput: Encodable {{
    let address: String?
    let rx_hex: String?
    let rx_hexes: [String]?
    let error: String?
}}

struct StatusOutput: Encodable {{
    let ok: Bool
    let state: String?
    let state_raw: Int?
    let authorization: String?
    let authorization_raw: Int?
    let error: String?
}}

func argument(_ name: String, default defaultValue: String? = nil) -> String? {{
    let args = CommandLine.arguments
    var index = 0
    while index < args.count {{
        if args[index] == name && index + 1 < args.count {{
            return args[index + 1]
        }}
        index += 1
    }}
    return defaultValue
}}

func writeJson<T: Encodable>(_ value: T) {{
    guard let output = argument("--output") else {{
        exit(2)
    }}
    do {{
        let data = try JSONEncoder().encode(value)
        try data.write(to: URL(fileURLWithPath: output))
    }} catch {{
        fputs("failed to write JSON: \\(error)\\n", stderr)
        exit(2)
    }}
}}

func hexData(_ value: String) -> Data? {{
    var data = Data()
    var text = value.trimmingCharacters(in: .whitespacesAndNewlines)
    if text.hasPrefix("0x") || text.hasPrefix("0X") {{
        text = String(text.dropFirst(2))
    }}
    if text.count % 2 != 0 {{
        return nil
    }}
    var index = text.startIndex
    while index < text.endIndex {{
        let next = text.index(index, offsetBy: 2)
        guard let byte = UInt8(text[index..<next], radix: 16) else {{
            return nil
        }}
        data.append(byte)
        index = next
    }}
    return data
}}

func hexDataList(_ value: String) -> [Data]? {{
    let parts = value.split(separator: ",").map {{
        String($0).trimmingCharacters(in: .whitespacesAndNewlines)
    }}
    if parts.isEmpty {{
        return nil
    }}
    var values: [Data] = []
    for part in parts {{
        guard let data = hexData(part) else {{
            return nil
        }}
        values.append(data)
    }}
    return values
}}

extension Data {{
    var hexString: String {{
        map {{ String(format: "%02x", $0) }}.joined()
    }}
}}

func serviceStrings(_ advertisementData: {advertisement_data_type}) -> [String] {{
    let uuids = advertisementData[CBAdvertisementDataServiceUUIDsKey] as? [CBUUID] ?? []
    return uuids.map {{ $0.uuidString.lowercased() }}
}}

func centralStateDescription(_ state: CBManagerState) -> String {{
    switch state {{
    case .unknown:
        return "unknown"
    case .resetting:
        return "resetting"
    case .unsupported:
        return "unsupported"
    case .unauthorized:
        return "unauthorized"
    case .poweredOff:
        return "powered off"
    case .poweredOn:
        return "powered on"
    @unknown default:
        return "unrecognized"
    }}
}}

func bluetoothAuthorizationStatus() -> (String, Int)? {{
    if #available(macOS 10.15, *) {{
        let value = CBCentralManager.authorization
        let label: String
        switch value {{
        case .notDetermined:
            label = "not_determined"
        case .restricted:
            label = "restricted"
        case .denied:
            label = "denied"
        case .allowedAlways:
            label = "allowed"
        @unknown default:
            label = "unrecognized"
        }}
        return (label, Int(value.rawValue))
    }}
    return nil
}}

final class BleTool: NSObject, CBCentralManagerDelegate, CBPeripheralDelegate {{
    private var central: CBCentralManager!
    private let mode: String
    private let timeout: TimeInterval
    private let nameContains: String?
    private let address: String?
    private let serviceUuid: CBUUID?
    private let writeUuid: CBUUID?
    private let notifyUuid: CBUUID?
    private let tx: Data?
    private let txSequence: [Data]
    private var devices: [String: JsonDevice] = [:]
    private var peripheral: CBPeripheral?
    private var writeCharacteristic: CBCharacteristic?
    private var rx = Data()
    private var currentRx = Data()
    private var rxChunks: [String] = []
    private var txIndex = 0
    private var inspectedServices: [JsonService] = []
    private var pendingServiceUuids: Set<CBUUID> = []
    private var finished = false
    private var settleUntil: Date?

    init(mode: String) {{
        self.mode = mode
        self.timeout = Double(argument("--timeout", default: "5.0") ?? "5.0") ?? 5.0
        self.nameContains = argument("--name-contains")?.lowercased()
        self.address = argument("--address")?.lowercased()
        self.serviceUuid = argument("--service-uuid").map {{ CBUUID(string: $0) }}
        self.writeUuid = argument("--write-uuid").map {{ CBUUID(string: $0) }}
        self.notifyUuid = argument("--notify-uuid").map {{ CBUUID(string: $0) }}
        self.tx = argument("--tx-hex").flatMap {{ hexData($0) }}
        self.txSequence = argument("--tx-hexes").flatMap {{ hexDataList($0) }} ?? []
        super.init()
        self.central = CBCentralManager(delegate: self, queue: DispatchQueue.main)
        DispatchQueue.main.asyncAfter(deadline: .now() + self.timeout) {{
            self.finish(error: self.timeoutError())
        }}
    }}

    func centralManagerDidUpdateState(_ central: CBCentralManager) {{
        if mode == "status" {{
            if central.state == .unknown || central.state == .resetting {{
                return
            }}
            let state = centralStateDescription(central.state)
            let error = central.state == .poweredOn
                ? nil
                : "Bluetooth state \\(state): \\(central.state.rawValue)"
            finish(error: error)
            return
        }}
        guard central.state == .poweredOn else {{
            if central.state != .unknown && central.state != .resetting {{
                let state = centralStateDescription(central.state)
                finish(error: "Bluetooth state \\(state): \\(central.state.rawValue)")
            }}
            return
        }}
        central.scanForPeripherals(withServices: nil, options: nil)
    }}

    func centralManager(
        _ central: CBCentralManager,
        didDiscover peripheral: CBPeripheral,
        advertisementData: {advertisement_data_type},
        rssi RSSI: NSNumber
    ) {{
        let name = peripheral.name ??
            advertisementData[CBAdvertisementDataLocalNameKey] as? String
        let services = serviceStrings(advertisementData)
        let device = JsonDevice(
            address: peripheral.identifier.uuidString,
            name: name,
            rssi: RSSI.intValue,
            services: services
        )
        if isLikelyZhiyun(device) {{
            devices[device.address] = device
        }}
        guard (mode == "exchange-raw" || mode == "exchange-sequence" ||
              mode == "inspect") &&
              peripheralMatches(device) else {{
            return
        }}
        self.peripheral = peripheral
        peripheral.delegate = self
        central.stopScan()
        central.connect(peripheral, options: nil)
    }}

    func centralManager(
        _ central: CBCentralManager,
        didConnect peripheral: CBPeripheral
    ) {{
        if mode == "inspect" {{
            peripheral.discoverServices(nil)
            return
        }}
        guard let serviceUuid = serviceUuid else {{
            finish(error: "service UUID is required")
            return
        }}
        peripheral.discoverServices([serviceUuid])
    }}

    func centralManager(
        _ central: CBCentralManager,
        didFailToConnect peripheral: CBPeripheral,
        error: Error?
    ) {{
        finish(error: error?.localizedDescription ?? "failed to connect")
    }}

    func peripheral(
        _ peripheral: CBPeripheral,
        didDiscoverServices error: Error?
    ) {{
        if let error = error {{
            finish(error: error.localizedDescription)
            return
        }}
        if mode == "inspect" {{
            let services = peripheral.services ?? []
            if services.isEmpty {{
                finish(error: "services not found")
                return
            }}
            pendingServiceUuids = Set(services.map {{ $0.uuid }})
            for service in services {{
                peripheral.discoverCharacteristics(nil, for: service)
            }}
            return
        }}
        let service = peripheral.services?.first(where: {{
            $0.uuid == serviceUuid
        }})
        guard let service = service else {{
            finish(error: "service not found")
            return
        }}
        let characteristics = [writeUuid, notifyUuid].compactMap {{ $0 }}
        peripheral.discoverCharacteristics(characteristics, for: service)
    }}

    func peripheral(
        _ peripheral: CBPeripheral,
        didDiscoverCharacteristicsFor service: CBService,
        error: Error?
    ) {{
        if let error = error {{
            finish(error: error.localizedDescription)
            return
        }}
        if mode == "inspect" {{
            let characteristics = service.characteristics ?? []
            inspectedServices.append(JsonService(
                uuid: service.uuid.uuidString.lowercased(),
                characteristics: characteristics.map {{ characteristic in
                    JsonCharacteristic(
                        uuid: characteristic.uuid.uuidString.lowercased(),
                        properties: propertyStrings(characteristic.properties)
                    )
                }}
            ))
            pendingServiceUuids.remove(service.uuid)
            if pendingServiceUuids.isEmpty {{
                finish(error: nil)
            }}
            return
        }}
        guard let characteristics = service.characteristics else {{
            finish(error: "characteristics not found")
            return
        }}
        guard let writeUuid = writeUuid,
              let notifyUuid = notifyUuid else {{
            finish(error: "write or notify UUID missing")
            return
        }}
        let write = characteristics.first(where: {{ $0.uuid == writeUuid }})
        let notify = characteristics.first(where: {{ $0.uuid == notifyUuid }})
        guard let write = write, let notify = notify else {{
            finish(error: "write or notify characteristic not found")
            return
        }}
        writeCharacteristic = write
        peripheral.setNotifyValue(true, for: notify)
    }}

    func peripheral(
        _ peripheral: CBPeripheral,
        didUpdateNotificationStateFor characteristic: CBCharacteristic,
        error: Error?
    ) {{
        if let error = error {{
            finish(error: error.localizedDescription)
            return
        }}
        guard characteristic.uuid == notifyUuid,
              let write = writeCharacteristic else {{
            return
        }}
        if mode == "exchange-sequence" {{
            sendSequenceWrite(peripheral, write)
            return
        }}
        guard let tx = tx else {{
            return
        }}
        sendWrite(tx, peripheral, write)
    }}

    private func sendSequenceWrite(
        _ peripheral: CBPeripheral,
        _ write: CBCharacteristic
    ) {{
        guard txIndex < txSequence.count else {{
            finish(error: nil)
            return
        }}
        sendWrite(txSequence[txIndex], peripheral, write)
    }}

    private func sendWrite(
        _ tx: Data,
        _ peripheral: CBPeripheral,
        _ write: CBCharacteristic
    ) {{
        let writeType: CBCharacteristicWriteType =
            write.properties.contains(.writeWithoutResponse)
            ? .withoutResponse
            : .withResponse
        currentRx.removeAll()
        peripheral.writeValue(tx, for: write, type: writeType)
        settleUntil = Date().addingTimeInterval(0.35)
        scheduleSettleCheck()
    }}

    func peripheral(
        _ peripheral: CBPeripheral,
        didUpdateValueFor characteristic: CBCharacteristic,
        error: Error?
    ) {{
        if let error = error {{
            finish(error: error.localizedDescription)
            return
        }}
        if characteristic.uuid == notifyUuid, let data = characteristic.value {{
            rx.append(data)
            currentRx.append(data)
            settleUntil = Date().addingTimeInterval(0.35)
            scheduleSettleCheck()
        }}
    }}

    private func scheduleSettleCheck() {{
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.36) {{
            guard let settleUntil = self.settleUntil else {{
                return
            }}
            if Date() >= settleUntil {{
                if self.mode == "exchange-sequence" {{
                    self.rxChunks.append(self.currentRx.hexString)
                    self.txIndex += 1
                    if self.txIndex < self.txSequence.count,
                       let peripheral = self.peripheral,
                       let write = self.writeCharacteristic {{
                        self.sendSequenceWrite(peripheral, write)
                        return
                    }}
                }}
                self.finish(error: nil)
            }}
        }}
    }}

    private func isLikelyZhiyun(_ device: JsonDevice) -> Bool {{
        let lowerName = device.name?.lowercased() ?? ""
        if lowerName.contains("zhiyun") || lowerName.contains("molus") ||
            lowerName.contains("g60") || lowerName.contains("pl103") {{
            return true
        }}
        let knownServices: Set<String> = [
            "6e400001-b5a3-f393-e0a9-e50e24dcca9e",
            "0000fee9-0000-1000-8000-00805f9b34fb",
            "0000ffe0-0000-1000-8000-00805f9b34fb",
            "00001827-0000-1000-8000-00805f9b34fb",
            "00001828-0000-1000-8000-00805f9b34fb"
        ]
        return !knownServices.intersection(Set(device.services)).isEmpty
    }}

    private func peripheralMatches(_ device: JsonDevice) -> Bool {{
        if let address = address, device.address.lowercased() != address {{
            return false
        }}
        if let nameContains = nameContains {{
            guard let name = device.name?.lowercased(),
                  name.contains(nameContains) else {{
                return false
            }}
        }}
        if address == nil && nameContains == nil {{
            return isLikelyZhiyun(device)
        }}
        return true
    }}

    private func propertyStrings(
        _ properties: CBCharacteristicProperties
    ) -> [String] {{
        var values: [String] = []
        if properties.contains(.broadcast) {{
            values.append("broadcast")
        }}
        if properties.contains(.read) {{
            values.append("read")
        }}
        if properties.contains(.writeWithoutResponse) {{
            values.append("write-without-response")
        }}
        if properties.contains(.write) {{
            values.append("write")
        }}
        if properties.contains(.notify) {{
            values.append("notify")
        }}
        if properties.contains(.indicate) {{
            values.append("indicate")
        }}
        return values
    }}

    private func timeoutError() -> String? {{
        if mode == "status" {{
            return "Bluetooth status timed out"
        }}
        if mode == "inspect" {{
            if peripheral == nil {{
                return "no matching BLE device found"
            }}
            if !pendingServiceUuids.isEmpty {{
                return "BLE inspect timed out"
            }}
        }}
        if mode == "exchange-raw" || mode == "exchange-sequence" {{
            if peripheral == nil {{
                return "no matching BLE device found"
            }}
            return "BLE exchange timed out"
        }}
        return nil
    }}

    private func finish(error: String?) {{
        if finished {{
            return
        }}
        finished = true
        central?.stopScan()
        if let peripheral = peripheral {{
            central?.cancelPeripheralConnection(peripheral)
        }}
        if mode == "status" {{
            let authorization = bluetoothAuthorizationStatus()
            let state = centralStateDescription(central.state)
            writeJson(StatusOutput(
                ok: error == nil && central.state == .poweredOn,
                state: state,
                state_raw: central.state.rawValue,
                authorization: authorization?.0,
                authorization_raw: authorization?.1,
                error: error
            ))
        }} else if mode == "scan" {{
            writeJson(ScanOutput(
                devices: Array(devices.values).sorted {{ $0.address < $1.address }},
                error: error
            ))
        }} else if mode == "inspect" {{
            writeJson(InspectOutput(
                address: peripheral?.identifier.uuidString,
                services: inspectedServices.sorted {{ $0.uuid < $1.uuid }},
                error: error
            ))
        }} else {{
            writeJson(ExchangeOutput(
                address: peripheral?.identifier.uuidString,
                rx_hex: rx.isEmpty ? nil : rx.hexString,
                rx_hexes: mode == "exchange-sequence" ? rxChunks : nil,
                error: error
            ))
        }}
        exit(error == nil ? 0 : 1)
    }}
}}

let mode = CommandLine.arguments.dropFirst().first ?? "scan"
if mode == "exchange-raw" && argument("--tx-hex").flatMap({{ hexData($0) }}) == nil {{
    writeJson(ExchangeOutput(
        address: nil,
        rx_hex: nil,
        rx_hexes: nil,
        error: "invalid tx hex"
    ))
    exit(1)
}}
if mode == "exchange-sequence" &&
   argument("--tx-hexes").flatMap({{ hexDataList($0) }}) == nil {{
    writeJson(ExchangeOutput(
        address: nil,
        rx_hex: nil,
        rx_hexes: nil,
        error: "invalid tx hex sequence"
    ))
    exit(1)
}}
let tool = BleTool(mode: mode)
withExtendedLifetime(tool) {{
    RunLoop.main.run()
}}
"""
