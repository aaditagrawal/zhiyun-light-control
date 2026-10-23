"""OBS integration tests: real local HTTP, stub OBS, no light or Bluetooth."""

import importlib.util
import json
import secrets
import socket
import sys
import tempfile
import threading
import time
import types
import unittest
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "integrations" / "obs" / "lighttime_obs.py"


class ObsStub(types.ModuleType):
    """Only documented Python signatures; record ownership and thread misuse."""

    def __init__(self):
        super().__init__("obspython")
        self.main_thread = threading.get_ident()
        self.background_calls = []
        self.logs = []
        self.hotkeys = {}
        self.unregistered = []
        self.loaded = []
        self.released_arrays = []
        self.timers = {}
        self.events = []
        self.scene_name = "Interview"
        self.released_sources = []
        for index, name in enumerate(
            (
                "LOG_INFO",
                "LOG_WARNING",
                "OBS_TEXT_DEFAULT",
                "OBS_TEXT_PASSWORD",
                "OBS_TEXT_MULTILINE",
                "OBS_TEXT_INFO",
                "OBS_PATH_FILE",
                "OBS_FRONTEND_EVENT_SCENE_CHANGED",
                "OBS_FRONTEND_EVENT_SCENE_COLLECTION_CHANGING",
                "OBS_FRONTEND_EVENT_SCRIPTING_SHUTDOWN",
            )
        ):
            setattr(self, name, index)

    def _check(self):
        if threading.get_ident() != self.main_thread:
            self.background_calls.append(threading.current_thread().name)
            raise AssertionError("OBS called from a background thread")

    def script_log(self, level, text):
        self._check()
        self.logs.append((level, text))

    def obs_properties_create(self):
        self._check()
        return {}

    def _property(self, props, name, description, kind, args):
        self._check()
        props[name] = {"description": description, "kind": kind, "args": args}
        return props[name]

    def obs_properties_add_text(self, props, name, description, text_type):
        return self._property(props, name, description, "text", (text_type,))

    def obs_properties_add_path(self, props, name, description, kind, file_filter, default_path):
        return self._property(props, name, description, "path", (kind, file_filter, default_path))

    def obs_properties_add_int(self, props, name, description, low, high, step):
        return self._property(props, name, description, "int", (low, high, step))

    def obs_properties_add_int_slider(self, props, name, description, low, high, step):
        return self._property(props, name, description, "slider", (low, high, step))

    def obs_properties_add_bool(self, props, name, description):
        return self._property(props, name, description, "bool", ())

    def obs_properties_add_button(self, props, name, description, callback):
        return self._property(props, name, description, "button", (callback,))

    def obs_properties_get(self, props, name):
        self._check()
        return props[name]

    def obs_property_set_description(self, prop, description):
        self._check()
        prop["description"] = description

    def obs_data_set_default_string(self, settings, name, value):
        self._check()
        settings.setdefault(name, value)

    obs_data_set_default_int = obs_data_set_default_string
    obs_data_set_default_bool = obs_data_set_default_string

    def obs_data_get_string(self, settings, name):
        self._check()
        return settings.get(name, "")

    def obs_data_get_int(self, settings, name):
        self._check()
        return settings.get(name, 0)

    obs_data_get_bool = obs_data_get_int

    def obs_hotkey_register_frontend(self, name, description, callback):
        self._check()
        hotkey = len(self.hotkeys) + 1
        self.hotkeys[hotkey] = (name, description, callback)
        return hotkey

    def obs_hotkey_unregister(self, callback):
        self._check()
        assert callable(callback), "Python OBS unregister takes a callback, not an ID"
        self.unregistered.append(callback)

    def obs_data_get_array(self, settings, name):
        self._check()
        return settings.get(name)

    def obs_data_array_release(self, array):
        self._check()
        self.released_arrays.append(array)

    def obs_hotkey_load(self, hotkey, array):
        self._check()
        self.loaded.append((hotkey, array))

    def obs_hotkey_save(self, hotkey):
        self._check()
        return [{"key": f"OBS_KEY_F{hotkey}"}]

    def obs_data_set_array(self, settings, name, array):
        self._check()
        settings[name] = array

    def obs_frontend_add_event_callback(self, callback):
        self._check()
        self.events.append(callback)

    def obs_frontend_remove_event_callback(self, callback):
        self._check()
        self.events.remove(callback)

    def timer_add(self, callback, milliseconds):
        self._check()
        self.timers[callback] = milliseconds

    def timer_remove(self, callback):
        self._check()
        self.timers.pop(callback)

    def obs_frontend_get_current_scene(self):
        self._check()
        return self.scene_name

    def obs_source_get_name(self, source):
        self._check()
        return source

    def obs_source_release(self, source):
        self._check()
        self.released_sources.append(source)


def load_script():
    stub = ObsStub()
    spec = importlib.util.spec_from_file_location("lighttime_obs_test_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"obspython": stub, spec.name: module}):
        spec.loader.exec_module(module)
    return module, stub


class FakeBridge:
    def __init__(self):
        self.token = secrets.token_urlsafe(32)
        self.records = []
        self.condition = threading.Condition()
        self.overrides = deque()
        self.hook = None
        self.last_brightness = 42.0
        self.state: dict[str, Any] = {
            "connected": True,
            "transport": "ble",
            "address": "test-device",
            "device": {"model": "PL103", "serial": "test-serial", "id": 0},
            "brightness": 42.0,
            "cct": 5000,
            "power": True,
            "updatedAt": "2026-10-23T00:00:00+00:00",
        }
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                pass

            def do_GET(self):
                self.handle_request()

            do_POST = do_GET
            do_PATCH = do_GET

            def handle_request(self):
                data = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                record: dict[str, Any] = {
                    "method": self.command,
                    "path": self.path,
                    "auth": self.headers.get("Authorization"),
                    "body": json.loads(data) if data else None,
                }
                with bridge.condition:
                    bridge.records.append(record)
                    bridge.condition.notify_all()
                if bridge.hook:
                    bridge.hook(record)
                status, body, headers = 200, None, {}
                if record["auth"] != ("Bearer " + bridge.token if bridge.token else None):
                    status, body = 401, {"error": {"code": "unauthorized", "message": bridge.token}}
                elif bridge.overrides:
                    status, body, headers = bridge.overrides.popleft()
                elif self.path == "/v1/connect" and self.command == "POST":
                    connect_body = record["body"]
                    assert isinstance(connect_body, dict)
                    bridge.state.update(
                        connected=True, transport="ble", address=connect_body["address"]
                    )
                elif self.path == "/v1/disconnect" and self.command == "POST":
                    bridge.state.update(connected=False, brightness=None, cct=None, power=None)
                elif self.path == "/v1/state" and self.command == "PATCH":
                    values = record["body"]
                    assert isinstance(values, dict)
                    if "cct" in values:
                        bridge.state["cct"] = values["cct"]
                    brightness = values.get("brightness", bridge.state["brightness"])
                    if values.get("power") is False:
                        brightness = 0
                    elif values.get("power") is True and "brightness" not in values:
                        brightness = bridge.last_brightness
                    if brightness:
                        bridge.last_brightness = brightness
                    bridge.state.update(brightness=brightness, power=brightness > 0)
                elif self.path != "/v1/state" or self.command != "GET":
                    status, body = 404, {"error": {"code": "not_found", "message": "Unknown route"}}
                raw = body if isinstance(body, bytes) else json.dumps(body or bridge.state).encode()
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(raw)))
                    for key, value in headers.items():
                        self.send_header(key, value)
                    self.end_headers()
                    if headers.get("X-Test-Trickle"):
                        for byte in raw:
                            self.wfile.write(bytes([byte]))
                            self.wfile.flush()
                            time.sleep(0.02)
                    else:
                        self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        self.thread.start()

    def wait_requests(self, count, timeout=3):
        with self.condition:
            return self.condition.wait_for(lambda: len(self.records) >= count, timeout)

    @property
    def writes(self):
        return [record for record in self.records if record["method"] == "PATCH"]

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)


def wait_result(worker, timeout=3):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        results = worker.drain()
        if results:
            return results[-1]
        time.sleep(0.005)
    raise AssertionError("Worker did not publish a result before the test deadline")


class ObsTests(unittest.TestCase):
    def setUp(self):
        self.script, self.obs = load_script()
        self.bridge = FakeBridge()
        self.addCleanup(self.bridge.close)
        self.workers = []
        self.addCleanup(self.shutdown_workers)

    def shutdown_workers(self):
        self.script.script_unload()
        for worker in self.workers:
            worker.stop()
            worker.thread.join(2)
            self.assertFalse(worker.thread.is_alive(), "HTTP worker leaked")
        self.assertEqual(self.obs.background_calls, [])

    def worker(self, ready=True, **config):
        worker = self.script.BridgeWorker(debounce=0.08)
        self.workers.append(worker)
        defaults = {"url": self.bridge.url, "token": self.bridge.token, "timeout": 1.0}
        defaults.update(config)
        worker.configure(self.script.BridgeConfig(**defaults))
        if ready:
            self.assertTrue(wait_result(worker).ok)
        return worker

    def load_obs(self, **extra):
        settings = {"bridge_url": self.bridge.url, "token": self.bridge.token, **extra}
        self.script.script_defaults(settings)
        self.script.script_load(settings)
        worker = self.script._worker
        self.workers.append(worker)
        self.assertTrue(wait_result(worker).ok)
        return settings, worker

    def test_import_has_no_worker_or_network_side_effects(self):
        before = {thread.ident for thread in threading.enumerate()}
        module, stub = load_script()
        self.assertIsNone(module._worker)
        self.assertEqual({thread.ident for thread in threading.enumerate()}, before)
        self.assertEqual(stub.logs, [])

    def test_properties_ranges_and_password_input(self):
        props = self.script.script_properties()
        self.assertEqual(props["brightness"]["args"], (0, 100, 1))
        self.assertEqual(props["cct"]["args"], (2700, 6500, 100))
        self.assertEqual(props["token"]["args"], (self.obs.OBS_TEXT_PASSWORD,))
        self.assertIn("off", props)
        self.assertIn("on", props)

    def test_initial_load_only_reads_state(self):
        self.load_obs()
        self.assertEqual(
            [(r["method"], r["path"]) for r in self.bridge.records], [("GET", "/v1/state")]
        )

    def test_slider_writes_coalesce_and_merge(self):
        worker = self.worker()
        for brightness in range(100):
            worker.patch({"brightness": brightness})
        worker.patch({"cct": 6500})
        result = wait_result(worker)
        self.assertTrue(result.ok)
        self.assertEqual(len(self.bridge.writes), 1)
        self.assertEqual(self.bridge.writes[0]["body"], {"brightness": 99, "cct": 6500})

    def test_coalescing_while_an_http_request_is_in_flight(self):
        worker = self.worker()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def hook(record):
            if record["method"] == "PATCH" and not entered.is_set():
                entered.set()
                release.wait(2)

        self.bridge.hook = hook
        worker.patch({"brightness": 10})
        self.assertTrue(entered.wait(2))
        start = time.monotonic()
        for value in range(11, 101):
            worker.patch({"brightness": value})
        self.assertLess(time.monotonic() - start, 0.15)
        release.set()
        self.assertTrue(self.bridge.wait_requests(3))
        self.assertEqual(
            [r["body"] for r in self.bridge.writes], [{"brightness": 10}, {"brightness": 100}]
        )

    def test_soft_power_uses_bridge_restore_and_drops_conflicting_pending_slider(self):
        worker = self.worker()
        worker.patch({"brightness": 80})
        worker.patch({"power": False})
        self.assertEqual(wait_result(worker).state["brightness"], 0)
        worker.patch({"brightness": 0})
        worker.patch({"power": True})
        self.assertEqual(wait_result(worker).state["brightness"], 42)
        self.assertEqual(
            [r["body"] for r in self.bridge.writes], [{"power": False}, {"power": True}]
        )

    def test_latest_brightness_overrides_pending_power(self):
        worker = self.worker()
        worker.patch({"power": False})
        worker.patch({"brightness": 60})
        self.assertEqual(wait_result(worker).state["brightness"], 60)
        self.assertEqual(self.bridge.writes[0]["body"], {"brightness": 60})

    def test_unsupported_resolution_rejected_before_writes(self):
        worker = self.worker()
        for values in (
            {"brightness": 5.5},
            {"brightness": 6.7},
            {"cct": 4501},
            {"cct": 4550},
            {"cct": 4599},
        ):
            with self.subTest(values=values), self.assertRaises(self.script.BridgeError):
                worker.patch(values)
        worker.action("refresh")
        self.assertTrue(wait_result(worker).ok)
        self.assertEqual(self.bridge.writes, [])
        worker.patch({"brightness": 5.0, "cct": 4500})
        self.assertTrue(wait_result(worker).ok)
        self.assertEqual(self.bridge.writes[0]["body"], {"brightness": 5.0, "cct": 4500})

    def test_obs_rejects_saved_unsupported_cct_before_write_and_logs_failure(self):
        settings, worker = self.load_obs()
        settings["cct"] = 4550
        self.script.script_update(settings)
        self.assertEqual(self.obs.logs[-1][0], self.obs.LOG_WARNING)
        self.assertIn("100 K steps", self.obs.logs[-1][1])
        self.script._apply(True)
        self.assertEqual(self.obs.logs[-1][0], self.obs.LOG_WARNING)
        worker.action("refresh")
        self.assertTrue(wait_result(worker).ok)
        self.assertEqual(self.bridge.writes, [])

    def test_connect_disconnect_use_contract_endpoints_and_auth_header(self):
        worker = self.worker()
        worker.action("connect", " platform-BLE-id ")
        self.assertTrue(wait_result(worker).ok)
        worker.action("disconnect")
        self.assertFalse(wait_result(worker).state["connected"])
        self.assertEqual(
            self.bridge.records[1]["body"], {"transport": "ble", "address": "platform-BLE-id"}
        )
        self.assertEqual(self.bridge.records[2]["body"], {})
        self.assertEqual(
            [r["path"] for r in self.bridge.records], ["/v1/state", "/v1/connect", "/v1/disconnect"]
        )
        self.assertTrue(
            all(r["auth"] == "Bearer " + self.bridge.token for r in self.bridge.records)
        )
        self.assertTrue(all(self.bridge.token not in r["path"] for r in self.bridge.records))

    def test_missing_address_is_visible_without_network(self):
        worker = self.worker()
        worker.action("connect", "")
        result = wait_result(worker)
        self.assertFalse(result.ok)
        self.assertIn("BLE address", result.message)
        self.assertEqual(len(self.bridge.records), 1)

    def test_unauthorized_and_server_errors_never_echo_token_or_body(self):
        worker = self.worker(ready=False, token=secrets.token_urlsafe(32))
        result = wait_result(worker)
        self.assertFalse(result.ok)
        self.assertIn("HTTP 401", result.message)
        self.assertNotIn(self.bridge.token, result.message)
        worker.configure(self.script.BridgeConfig(self.bridge.url, self.bridge.token, timeout=1))
        self.assertTrue(wait_result(worker).ok)
        for code in (403, 409, 500):
            with self.subTest(status=code):
                self.bridge.overrides.append(
                    (code, {"error": {"code": self.bridge.token, "message": self.bridge.token}}, {})
                )
                worker.action("refresh")
                result = wait_result(worker)
                self.assertFalse(result.ok)
                self.assertIn(f"HTTP {code}", result.message)
                self.assertNotIn(self.bridge.token, result.message)

    def test_redirect_refused_without_forwarding_credentials(self):
        other = FakeBridge()
        self.addCleanup(other.close)
        worker = self.worker()
        self.bridge.overrides.append((302, b"", {"Location": other.url + "/v1/state"}))
        worker.action("refresh")
        result = wait_result(worker)
        self.assertFalse(result.ok)
        self.assertIn("HTTP 302", result.message)
        self.assertEqual(other.records, [])

    def test_token_file_plain_json_precedence_and_rotation(self):
        with tempfile.TemporaryDirectory() as folder:
            token_file = Path(folder) / "bridge-token"
            token_file.write_text(self.bridge.token + "\n", encoding="utf-8")
            worker = self.worker(token=secrets.token_urlsafe(32), token_file=str(token_file))
            self.bridge.token = secrets.token_urlsafe(32)
            token_file.write_text(json.dumps({"token": self.bridge.token}), encoding="utf-8")
            worker.action("refresh")
            self.assertTrue(wait_result(worker).ok)
            self.assertEqual(self.bridge.records[-1]["auth"], "Bearer " + self.bridge.token)
            token_file.write_text("{}", encoding="utf-8")
            worker.action("refresh")
            result = wait_result(worker)
            self.assertFalse(result.ok)
            self.assertIn("token file", result.message)

    def test_header_injection_rejected_before_http(self):
        for token in ("bad\r\nheader", "bad\x00header"):
            with self.subTest(token_present=bool(token)):
                worker = self.worker(ready=False, token=token)
                self.assertFalse(wait_result(worker).ok)
        self.assertEqual(self.bridge.records, [])

    def test_personal_bridge_omits_auth_and_accepts_verified_usb_ble_state(self):
        self.bridge.token = ""
        self.bridge.state["transport"] = "usb+ble"
        worker = self.worker(token="")
        worker.action("refresh")
        self.assertTrue(wait_result(worker).ok)
        self.assertTrue(all(record["auth"] is None for record in self.bridge.records))

    def test_urls_reject_nonlocal_hosts_credentials_and_query_secrets(self):
        for url in (
            "https://127.0.0.1:8877",
            "http://192.0.2.1:8877",
            "http://example.invalid",
            "http://user:password@127.0.0.1:8877",
            "http://127.0.0.1:8877/?token=value",
            "http://127.0.0.1:8877/v1",
            "http://127.0.0.1:8877/#secret",
        ):
            with self.subTest(url=url):
                worker = self.worker(ready=False, url=url)
                self.assertFalse(wait_result(worker).ok)
        self.assertEqual(self.bridge.records, [])
        self.assertEqual(self.script._endpoint("http://[::1]:8877"), ("::1", 8877))

    def test_invalid_json_typed_state_and_oversized_response(self):
        worker = self.worker()
        invalid = dict(self.bridge.state, brightness="42")
        for body in (
            b"not json",
            {},
            invalid,
            dict(self.bridge.state, brightness=5.5),
            dict(self.bridge.state, cct=4550),
            b"x" * 65537,
        ):
            with self.subTest(body_type=type(body).__name__):
                self.bridge.overrides.append((200, body if body != {} else b"{}", {}))
                worker.action("refresh")
                self.assertFalse(wait_result(worker).ok)

    def test_total_deadline_includes_slow_trickling_response(self):
        worker = self.worker(timeout=0.25)
        self.bridge.overrides.append((200, b" " * 100, {"X-Test-Trickle": "1"}))
        start = time.monotonic()
        worker.action("refresh")
        result = wait_result(worker)
        self.assertFalse(result.ok)
        self.assertIn("timed out", result.message)
        self.assertLess(time.monotonic() - start, 1.0)

    def test_unreachable_bridge_is_a_sanitized_error(self):
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            url = f"http://127.0.0.1:{reserved.getsockname()[1]}"
            worker = self.worker(ready=False, url=url, timeout=0.25)
            result = wait_result(worker)
        self.assertFalse(result.ok)
        self.assertIn("Cannot reach", result.message)
        self.assertNotIn(self.bridge.token, result.message)

    def test_stop_is_nonblocking_and_cancels_inflight_and_pending_work(self):
        worker = self.worker(timeout=5)
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def hook(record):
            entered.set()
            release.wait(2)

        self.bridge.hook = hook
        worker.action("refresh")
        self.assertTrue(entered.wait(1))
        worker.patch({"brightness": 100})
        start = time.monotonic()
        worker.stop()
        self.assertLess(time.monotonic() - start, 0.15)
        worker.thread.join(1)
        self.assertFalse(worker.thread.is_alive())
        worker.patch({"brightness": 10})
        worker.action("refresh")
        self.assertEqual(worker.drain(), [])
        self.assertEqual(self.bridge.writes, [])

    def test_reconfiguration_discards_pending_controls_and_old_results(self):
        worker = self.worker(timeout=5)
        other = FakeBridge()
        other.state.update(brightness=15.0)
        self.addCleanup(other.close)
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def hook(record):
            entered.set()
            release.wait(2)

        self.bridge.hook = hook
        worker.action("refresh")
        self.assertTrue(entered.wait(1))
        worker.patch({"brightness": 100})
        worker.configure(self.script.BridgeConfig(other.url, other.token, timeout=1))
        result = wait_result(worker)
        self.assertTrue(result.ok)
        self.assertEqual(result.state["brightness"], 15)
        self.assertEqual(other.writes, [])
        self.assertEqual(self.bridge.writes, [])

    def test_scene_latest_wins_and_unmapped_scene_cancels_pending_preset(self):
        worker = self.worker()
        worker.scene({"brightness": 10, "cct": 3000})
        worker.scene({"brightness": 20})
        self.assertEqual(wait_result(worker).state["brightness"], 20)
        self.assertEqual(self.bridge.writes[0]["body"], {"brightness": 20})
        worker.scene({"power": False})
        worker.scene()
        worker.action("refresh")
        self.assertTrue(wait_result(worker).ok)
        time.sleep(0.12)
        self.assertEqual(len(self.bridge.writes), 1)

    def test_manual_control_replaces_pending_scene_and_scene_cancel_preserves_manual(self):
        worker = self.worker()
        worker.scene({"brightness": 10, "cct": 3000})
        worker.patch({"brightness": 70})
        worker.scene()
        self.assertTrue(wait_result(worker).ok)
        self.assertEqual(self.bridge.writes[0]["body"], {"brightness": 70})

    def test_presets_reject_invalid_ranges_types_and_power_conflicts(self):
        for mapping in (
            "[]",
            "broken",
            '{"": {"power": false}}',
            '{"A": {"brightness": true}}',
            '{"A": {"brightness": 101}}',
            '{"A": {"brightness": 5.5}}',
            '{"A": {"cct": 2700.0}}',
            '{"A": {"cct": 4501}}',
            '{"A": {"cct": 4550}}',
            '{"A": {"power": false, "brightness": 10}}',
            '{"A": {"unknown": 1}}',
        ):
            with self.subTest(mapping=mapping), self.assertRaises(self.script.BridgeError):
                self.script.parse_presets(mapping)

    def test_obs_lifecycle_hotkeys_save_release_and_cleanup(self):
        settings, worker = self.load_obs(**{"lighttime.on": [{"key": "OBS_KEY_F1"}]})
        self.script.script_load(settings)
        self.assertEqual(len(self.obs.hotkeys), 3)
        self.assertEqual(len(self.obs.loaded), 1)
        self.assertEqual(len(self.obs.released_arrays), 1)
        self.assertEqual(len(self.obs.events), 1)
        self.assertEqual(self.obs.timers, {self.script._drain: 100})
        self.script._off(False)
        self.assertEqual(self.bridge.writes, [])
        self.script._off(True)
        self.assertEqual(wait_result(worker).state["brightness"], 0)
        self.script._on(True)
        self.assertEqual(wait_result(worker).state["brightness"], 42)
        self.script.script_save(settings)
        self.assertEqual(len(self.obs.released_arrays), 4)
        self.assertTrue(
            all(name in settings for name in ("lighttime.on", "lighttime.off", "lighttime.apply"))
        )
        self.script.script_unload()
        self.script.script_unload()
        self.assertEqual(
            self.obs.unregistered, [self.script._on, self.script._off, self.script._apply]
        )
        self.assertEqual(self.obs.events, [])
        self.assertEqual(self.obs.timers, {})

    def test_obs_updates_only_changed_sliders_and_does_not_replay_on_unrelated_edit(self):
        settings, worker = self.load_obs()
        settings["brightness"] = 33
        self.script.script_update(settings)
        self.assertTrue(wait_result(worker).ok)
        self.assertEqual(self.bridge.writes[0]["body"], {"brightness": 33})
        settings["address"] = "another-address"
        self.script.script_update(settings)
        self.script._refresh(None, None)
        self.assertTrue(wait_result(worker).ok)
        self.assertEqual(len(self.bridge.writes), 1)
        settings["cct"] = 2700
        self.script.script_update(settings)
        self.assertTrue(wait_result(worker).ok)
        self.assertEqual(self.bridge.writes[-1]["body"], {"cct": 2700})

    def test_frontend_presets_use_program_scene_release_sources_and_cancel_on_collection_change(
        self,
    ):
        settings, worker = self.load_obs()
        settings.update(
            scene_enabled=True,
            scene_presets='{"Interview":{"brightness":30},"Break":{"power":false}}',
        )
        self.script.script_update(settings)
        self.assertEqual(wait_result(worker).state["brightness"], 30)
        self.assertEqual(self.obs.released_sources, ["Interview"])
        self.obs.scene_name = "Break"
        self.script._frontend_event(self.obs.OBS_FRONTEND_EVENT_SCENE_CHANGED)
        self.script._frontend_event(self.obs.OBS_FRONTEND_EVENT_SCENE_COLLECTION_CHANGING)
        self.script._refresh(None, None)
        self.assertTrue(wait_result(worker).ok)
        time.sleep(0.2)
        self.assertEqual(len(self.bridge.writes), 1)
        self.script._frontend_event(self.obs.OBS_FRONTEND_EVENT_SCRIPTING_SHUTDOWN)
        self.assertIsNone(self.script._worker)

    def test_failures_are_visible_in_obs_log_and_status_without_leaking_token(self):
        settings, worker = self.load_obs()
        self.bridge.overrides.append((500, {"error": {"message": self.bridge.token}}, {}))
        self.script._refresh(None, None)
        self.assertTrue(self.bridge.wait_requests(2))
        until = time.monotonic() + 2
        while not self.obs.logs and time.monotonic() < until:
            self.script._drain()
            time.sleep(0.005)
        self.assertEqual(self.obs.logs[-1][0], self.obs.LOG_WARNING)
        props = self.script.script_properties()
        self.assertTrue(self.script._show_status(props, None))
        self.assertIn("HTTP 500", props["status"]["description"])
        self.assertNotIn(self.bridge.token, repr(self.obs.logs))
        self.assertNotIn(self.bridge.token, repr(self.script._config))
        self.assertEqual(self.script._last_controls, (settings["brightness"], settings["cct"]))


if __name__ == "__main__":
    unittest.main()
