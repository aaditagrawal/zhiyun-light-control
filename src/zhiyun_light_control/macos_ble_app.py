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


@dataclass(frozen=True)
class MacosBleAppRun:
    ok: bool
    payload: dict[str, object]
    error: str | None = None
    returncode: int | None = None
    command: tuple[str, ...] = ()


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
    swift_path = shutil.which("swift")
    if open_path is None:
        return MacosBleAppRun(
            ok=False,
            payload={},
            error="macOS open command not found",
        )
    if swift_path is None:
        return MacosBleAppRun(ok=False, payload={}, error="Swift runtime not found")
    app_path = ensure_macos_ble_app(bundle_name=bundle_name, swift_path=swift_path)
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
    swift_path: str = "/usr/bin/swift",
) -> Path:
    app_path = _bundle_root(bundle_name)
    contents = app_path / "Contents"
    macos = contents / "MacOS"
    resources = contents / "Resources"
    macos.mkdir(parents=True, exist_ok=True)
    resources.mkdir(parents=True, exist_ok=True)
    _write_if_changed(contents / "Info.plist", _info_plist(bundle_name))
    helper = resources / "helper.swift"
    _write_if_changed(helper, _swift_source().encode("utf-8"))
    launcher = macos / bundle_name
    launcher_bytes = (
        "#!/bin/sh\n"
        'APP_DIR="$(dirname "$0")"\n'
        f"exec {swift_path!r} "
        '"$APP_DIR/../Resources/helper.swift" "$@"\n'
    ).encode()
    _write_if_changed(launcher, launcher_bytes)
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
            "LSBackgroundOnly": True,
            "NSBluetoothAlwaysUsageDescription": BLUETOOTH_USAGE,
            "NSBluetoothPeripheralUsageDescription": BLUETOOTH_USAGE,
        },
        sort_keys=True,
    )


def _write_if_changed(path: Path, data: bytes) -> None:
    if path.exists() and path.read_bytes() == data:
        return
    path.write_bytes(data)


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

struct ExchangeOutput: Encodable {{
    let address: String?
    let rx_hex: String?
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
    private var devices: [String: JsonDevice] = [:]
    private var peripheral: CBPeripheral?
    private var writeCharacteristic: CBCharacteristic?
    private var rx = Data()
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
        super.init()
        self.central = CBCentralManager(delegate: self, queue: DispatchQueue.main)
        DispatchQueue.main.asyncAfter(deadline: .now() + self.timeout) {{
            self.finish(error: nil)
        }}
    }}

    func centralManagerDidUpdateState(_ central: CBCentralManager) {{
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
        guard mode == "exchange-raw" && peripheralMatches(device) else {{
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

    func peripheral(_ peripheral: CBPeripheral, didDiscoverServices error: Error?) {{
        if let error = error {{
            finish(error: error.localizedDescription)
            return
        }}
        let service = peripheral.services?.first(where: {{ $0.uuid == serviceUuid }})
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
              let tx = tx,
              let write = writeCharacteristic else {{
            return
        }}
        let writeType: CBCharacteristicWriteType =
            write.properties.contains(.writeWithoutResponse)
            ? .withoutResponse
            : .withResponse
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

    private func finish(error: String?) {{
        if finished {{
            return
        }}
        finished = true
        central?.stopScan()
        if let peripheral = peripheral {{
            central?.cancelPeripheralConnection(peripheral)
        }}
        if mode == "scan" {{
            writeJson(ScanOutput(
                devices: Array(devices.values).sorted {{ $0.address < $1.address }},
                error: error
            ))
        }} else {{
            writeJson(ExchangeOutput(
                address: peripheral?.identifier.uuidString,
                rx_hex: rx.isEmpty ? nil : rx.hexString,
                error: error
            ))
        }}
        exit(error == nil ? 0 : 1)
    }}
}}

let mode = CommandLine.arguments.dropFirst().first ?? "scan"
if mode == "exchange-raw" && argument("--tx-hex").flatMap({{ hexData($0) }}) == nil {{
    writeJson(ExchangeOutput(address: nil, rx_hex: nil, error: "invalid tx hex"))
    exit(1)
}}
let tool = BleTool(mode: mode)
withExtendedLifetime(tool) {{
    RunLoop.main.run()
}}
"""
