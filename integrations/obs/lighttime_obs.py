"""OBS Python script for the local Lighttime bridge; no third-party dependencies.

Load this file in Tools > Scripts. HTTP and token-file reads run exclusively on
BridgeWorker. OBS functions are called exclusively from OBS script callbacks.
Compatible with Python 3.8+ (use the Python version supported by your OBS build).
"""

import http.client
import ipaddress
import json
import math
import os
import socket
import stat
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlsplit

import obspython as obs  # ty: ignore[unresolved-import]  # Supplied by OBS, not PyPI.

DEFAULT_URL = "http://127.0.0.1:8877"
MAX_BODY = 65536


class BridgeError(Exception):
    """A deliberately sanitized, user-visible error (never raw server text)."""


@dataclass(frozen=True)
class BridgeConfig:
    url: str = DEFAULT_URL
    token: str = field(default="", repr=False)
    token_file: str = ""
    timeout: float = 60.0


@dataclass(frozen=True)
class Result:
    operation: str
    ok: bool
    message: str
    state: Optional[dict] = None  # noqa: UP045 - OBS's Python can predate 3.10.


def _endpoint(url):
    """Only local HTTP; no credentials, queries, redirects, proxies, or DNS."""
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        if host == "localhost":
            host = "127.0.0.1"
        if (
            parsed.scheme != "http"
            or not host
            or not ipaddress.ip_address(host).is_loopback
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError
        port = parsed.port if parsed.port is not None else 80
        if port == 0:
            raise ValueError
        return host, port
    except ValueError:
        raise BridgeError(
            "Bridge URL must be a loopback HTTP origin, e.g. http://127.0.0.1:8877."
        ) from None


def _token(config):
    token = config.token.strip()
    if config.token_file.strip():
        try:
            # Nonblocking open also rejects FIFOs without hanging the worker.
            fd = os.open(
                os.path.expanduser(config.token_file), os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
            )
            with os.fdopen(fd, "r", encoding="utf-8") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError
                contents = stream.read(8193)
            if len(contents) > 8192:
                raise ValueError
            contents = contents.strip()
            token = json.loads(contents)["token"] if contents.startswith("{") else contents
        except (OSError, ValueError, KeyError, TypeError):
            raise BridgeError(
                "Cannot read token file; use a UTF-8 token or JSON object with a token field."
            ) from None
    if (
        not isinstance(token, str)
        or not token
        or len(token) > 8192
        or any(not 33 <= ord(c) <= 126 for c in token)
    ):
        raise BridgeError("Set a valid bearer token or token file before connecting.")
    return token


def _patch(values):
    if not isinstance(values, dict) or not values or set(values) - {"brightness", "cct", "power"}:
        raise BridgeError("Controls must contain brightness, cct, or power only.")
    for key, low, high in (("brightness", 0, 100), ("cct", 2700, 6500)):
        if key in values:
            value = values[key]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not low <= value <= high
                or (key == "brightness" and value % 1 != 0)
                or (key == "cct" and (not isinstance(value, int) or value % 100 != 0))
            ):
                raise BridgeError(
                    "Brightness must be a whole percent from 0–100; CCT must be 2700–6500 K in 100 K steps."
                )
    if "power" in values and not isinstance(values["power"], bool):
        raise BridgeError("Power must be true or false.")
    if (
        "power" in values
        and "brightness" in values
        and values["power"] != (values["brightness"] > 0)
    ):
        raise BridgeError("Power and brightness must agree.")
    return dict(values)


def _state(body):
    fields = {
        "connected",
        "transport",
        "address",
        "device",
        "brightness",
        "cct",
        "power",
        "updatedAt",
    }
    if not isinstance(body, dict) or not fields <= body.keys():
        raise BridgeError("Bridge returned an invalid State object.")
    if (
        not isinstance(body["connected"], bool)
        or body["transport"] not in (None, "ble", "serial", "mock")
        or any(
            body[key] is not None and not isinstance(body[key], str)
            for key in ("address", "updatedAt")
        )
        or (body["power"] is not None and not isinstance(body["power"], bool))
    ):
        raise BridgeError("Bridge returned invalid State field types.")
    values = {key: body[key] for key in ("brightness", "cct", "power") if body[key] is not None}
    if values:
        _patch(values)
    device = body["device"]
    if device is not None and (
        not isinstance(device, dict)
        or not isinstance(device.get("model"), str)
        or not isinstance(device.get("serial"), str)
        or type(device.get("id")) is not int
    ):
        raise BridgeError("Bridge returned invalid device information.")
    return body


def parse_presets(text):
    try:
        if len(text) > MAX_BODY:
            raise ValueError
        mapping = json.loads(text or "{}")
        if not isinstance(mapping, dict) or any(not name for name in mapping):
            raise ValueError
        return {name: _patch(values) for name, values in mapping.items()}
    except (ValueError, TypeError, BridgeError):
        raise BridgeError(
            "Invalid scene presets: use a JSON object mapping exact scene names to valid controls."
        ) from None


class BridgeWorker:
    """One bounded action slot and one coalesced control slot; no OBS calls.

    Changing configuration invalidates queued jobs and old results. A scene
    change replaces its pending preset, including changes to unmapped scenes.
    An already dispatched request cannot be rolled back by a scene change.
    """

    def __init__(self, debounce=0.15):
        self._condition = threading.Condition()
        self._config = None
        self._generation = 0
        self._action = None
        self._pending = {}
        self._origin = None
        self._due = 0.0
        self._debounce = debounce
        self._stopped = False
        self._cancel = None
        self._results = deque(maxlen=32)
        self.thread = threading.Thread(target=self._run, name="Lighttime OBS HTTP", daemon=True)
        self.thread.start()

    def configure(self, config):
        with self._condition:
            if self._stopped or config == self._config:
                return
            self._config = config
            self._generation += 1
            self._results.clear()
            self._pending.clear()
            self._origin = None
            self._action = ("refresh", None)
            if self._cancel:
                self._cancel.set()
            self._condition.notify()

    def action(self, operation, address=None):
        if operation not in ("connect", "disconnect", "refresh"):
            raise ValueError("Unknown bridge action")
        with self._condition:
            if self._stopped:
                return
            if operation != "refresh":
                self._pending.clear()
                self._origin = None
            self._action = (operation, address)
            self._condition.notify()

    def patch(self, values):
        values = _patch(values)
        with self._condition:
            if self._stopped:
                return
            if self._origin == "scene":
                self._pending.clear()
            # Latest power/brightness intent wins without conflicting fields.
            if "power" in values:
                self._pending.pop("brightness", None)
            if "brightness" in values:
                self._pending.pop("power", None)
            self._pending.update(values)
            self._origin = "manual"
            self._due = time.monotonic() + self._debounce
            self._condition.notify()

    def scene(self, values=None):
        values = _patch(values) if values is not None else None
        with self._condition:
            if self._stopped:
                return
            if values is not None or self._origin == "scene":
                self._pending = values or {}
                self._origin = "scene" if values else None
                self._due = time.monotonic() + self._debounce
                self._condition.notify()

    def drain(self):
        with self._condition:
            results = list(self._results)
            self._results.clear()
            return results

    def stop(self):
        """Signal only; never join or touch sockets from an OBS callback."""
        with self._condition:
            self._stopped = True
            self._pending.clear()
            self._action = None
            self._results.clear()
            if self._cancel:
                self._cancel.set()
            self._condition.notify()

    def _request(self, config, generation, method, path, data):
        host, port = _endpoint(config.url)
        token = _token(config)
        if not math.isfinite(config.timeout) or not 0.25 <= config.timeout <= 60:
            raise BridgeError("HTTP timeout must be between 0.25 and 60 seconds.")
        connection = http.client.HTTPConnection(host, port, timeout=config.timeout)
        finished = threading.Event()
        cancel = threading.Event()
        expired = threading.Event()
        connected_socket = [None]
        with self._condition:
            if self._stopped or generation != self._generation:
                raise BridgeError("Request cancelled.")
            self._cancel = cancel

        def deadline():
            cancel.wait(config.timeout)
            if not finished.is_set():
                expired.set()
                sock = connected_socket[0]
                if sock is not None:
                    try:
                        sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass

        guard = threading.Thread(target=deadline, name="Lighttime HTTP deadline", daemon=True)
        guard.start()
        try:
            connection.connect()
            connected_socket[0] = connection.sock
            if expired.is_set():
                raise TimeoutError
            payload = json.dumps(data).encode("utf-8") if data is not None else None
            connection.request(
                method,
                path,
                payload,
                {
                    "Authorization": "Bearer " + token,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
            response = connection.getresponse()
            if 300 <= response.status < 400:
                raise BridgeError(
                    f"HTTP {response.status}: bridge redirects are refused; check the local URL."
                )
            if response.status in (401, 403):
                raise BridgeError(
                    f"HTTP {response.status}: authentication rejected; check the bridge token."
                )
            if response.status >= 400:
                raise BridgeError(
                    f"HTTP {response.status}: bridge request failed; check the bridge log and refresh."
                )
            raw = response.read(MAX_BODY + 1)
            if expired.is_set():
                raise TimeoutError
            if len(raw) > MAX_BODY:
                raise BridgeError("Bridge response exceeded the 64 KiB limit.")
            try:
                return _state(json.loads(raw))
            except (ValueError, UnicodeError):
                raise BridgeError("Bridge returned invalid JSON.") from None
        except (TimeoutError, socket.timeout):  # noqa: UP041 - distinct classes on Python 3.8–3.9.
            raise BridgeError(
                "Bridge request timed out; the light state is unknown. Refresh before retrying."
            ) from None
        except (OSError, http.client.HTTPException):
            if expired.is_set():
                raise BridgeError(
                    "Bridge request timed out or was cancelled; refresh the light state."
                ) from None
            raise BridgeError(
                "Cannot reach the bridge; check that it is running and refresh the light state."
            ) from None
        finally:
            finished.set()
            cancel.set()
            connection.close()
            guard.join()
            with self._condition:
                if self._cancel is cancel:
                    self._cancel = None

    def _run(self):
        while True:
            with self._condition:
                while True:
                    if self._stopped:
                        return
                    if self._config is not None and self._action is not None:
                        operation, data = self._action
                        self._action = None
                        break
                    if self._config is not None and self._pending:
                        remaining = self._due - time.monotonic()
                        if remaining <= 0:
                            operation, data = "set", self._pending
                            self._pending = {}
                            self._origin = None
                            break
                        self._condition.wait(remaining)
                    else:
                        self._condition.wait()
                config, generation = self._config, self._generation
            try:
                if operation == "connect":
                    if not isinstance(data, str) or not data.strip():
                        raise BridgeError(
                            "Enter the light's BLE address or platform identifier before connecting."
                        )
                    method, path, data = (
                        "POST",
                        "/v1/connect",
                        {"transport": "ble", "address": data.strip()},
                    )
                elif operation == "disconnect":
                    method, path, data = "POST", "/v1/disconnect", {}
                elif operation == "refresh":
                    method, path, data = "GET", "/v1/state", None
                else:
                    method, path = "PATCH", "/v1/state"
                state = self._request(config, generation, method, path, data)
                if (
                    state["connected"]
                    and state["brightness"] is not None
                    and state["cct"] is not None
                ):
                    message = (
                        f"Verified bridge state: brightness {state['brightness']}%, "
                        f"CCT {state['cct']} K, power {state['power']}."
                    )
                elif state["connected"]:
                    message = "Bridge connected; readback unavailable. Refresh the light state."
                else:
                    message = (
                        "Bridge reachable; light disconnected. Enter a BLE address and Connect."
                    )
                result = Result(operation, True, message, state)
            except BridgeError as error:
                result = Result(operation, False, str(error))
            except Exception:
                # Never echo URLs, tokens, response bodies, or exception text.
                result = Result(
                    operation,
                    False,
                    "Unexpected bridge worker failure; check bridge configuration.",
                )
            with self._condition:
                if not self._stopped and generation == self._generation:
                    self._results.append(result)


_worker = None
_config = None
_last_controls = None
_presets = {}
_scene_enabled = False
_address = ""
_hotkeys = []
_status = "Configure the local bridge and token. Status changes also appear in the OBS log."


def _report(message, error=False):
    global _status
    _status = message
    obs.script_log(obs.LOG_WARNING if error else obs.LOG_INFO, "Lighttime: " + message)


def _drain():
    if _worker:
        for result in _worker.drain():
            _report(result.message, not result.ok)


def _connect(props, prop):
    if _worker:
        _worker.action("connect", _address)
    return False


def _disconnect(props, prop):
    if _worker:
        _worker.action("disconnect")
    return False


def _refresh(props, prop):
    if _worker:
        _worker.action("refresh")
    return False


def _on(pressed):
    if pressed and _worker:
        _worker.patch({"power": True})


def _off(pressed):
    if pressed and _worker:
        _worker.patch({"power": False})


def _apply(pressed):
    if pressed and _worker and _last_controls is not None:
        try:
            _worker.patch({"brightness": _last_controls[0], "cct": _last_controls[1]})
        except BridgeError as error:
            _report(str(error), True)


def _on_button(props, prop):
    _on(True)
    return False


def _off_button(props, prop):
    _off(True)
    return False


def _apply_button(props, prop):
    _apply(True)
    return False


def _show_status(props, prop):
    _drain()
    obs.obs_property_set_description(obs.obs_properties_get(props, "status"), _status)
    return True


def _program_scene():
    if not _worker:
        return
    if not _scene_enabled:
        _worker.scene()
        return
    scene = obs.obs_frontend_get_current_scene()
    try:
        name = obs.obs_source_get_name(scene) if scene else ""
    finally:
        if scene:
            obs.obs_source_release(scene)
    _worker.scene(_presets.get(name))


def _frontend_event(event):
    if event == obs.OBS_FRONTEND_EVENT_SCENE_CHANGED:
        _program_scene()
    elif event == obs.OBS_FRONTEND_EVENT_SCENE_COLLECTION_CHANGING:
        if _worker:
            _worker.scene()
    elif event == obs.OBS_FRONTEND_EVENT_SCRIPTING_SHUTDOWN:
        script_unload()


def script_description():
    return (
        "Lighttime MOLUS G60 via the local authenticated bridge. "
        "Soft off sets brightness to zero; On restores the bridge's last nonzero brightness. "
        "See docs/obs.md. Status changes appear in Help > Log Files > View Current Log."
    )


def script_defaults(settings):
    obs.obs_data_set_default_string(settings, "bridge_url", DEFAULT_URL)
    obs.obs_data_set_default_string(settings, "scene_presets", "{}")
    obs.obs_data_set_default_int(settings, "timeout", 60)
    obs.obs_data_set_default_int(settings, "brightness", 5)
    obs.obs_data_set_default_int(settings, "cct", 5000)
    obs.obs_data_set_default_bool(settings, "scene_enabled", False)


def script_properties():
    props = obs.obs_properties_create()
    obs.obs_properties_add_text(props, "bridge_url", "Local bridge URL", obs.OBS_TEXT_DEFAULT)
    obs.obs_properties_add_path(
        props, "token_file", "Token file (preferred; overrides input)", obs.OBS_PATH_FILE, "", None
    )
    obs.obs_properties_add_text(
        props, "token", "Bearer token (saved in OBS settings)", obs.OBS_TEXT_PASSWORD
    )
    obs.obs_properties_add_int(props, "timeout", "HTTP deadline (seconds)", 1, 60, 1)
    obs.obs_properties_add_text(
        props, "address", "BLE address / platform identifier", obs.OBS_TEXT_DEFAULT
    )
    obs.obs_properties_add_button(props, "connect", "Connect BLE via bridge", _connect)
    obs.obs_properties_add_button(props, "disconnect", "Disconnect bridge's light", _disconnect)
    obs.obs_properties_add_button(props, "refresh", "Refresh verified state", _refresh)
    obs.obs_properties_add_int_slider(props, "brightness", "Brightness (%)", 0, 100, 1)
    obs.obs_properties_add_int_slider(props, "cct", "CCT (K; 100 K steps)", 2700, 6500, 100)
    obs.obs_properties_add_button(props, "apply", "Apply both sliders", _apply_button)
    obs.obs_properties_add_button(props, "off", "Soft off", _off_button)
    obs.obs_properties_add_button(props, "on", "On / restore brightness", _on_button)
    obs.obs_properties_add_bool(props, "scene_enabled", "Enable program-scene presets")
    obs.obs_properties_add_text(
        props, "scene_presets", "Scene presets (JSON)", obs.OBS_TEXT_MULTILINE
    )
    obs.obs_properties_add_text(props, "status", _status, obs.OBS_TEXT_INFO)
    obs.obs_properties_add_button(
        props, "show_status", "Show latest status (also logged live)", _show_status
    )
    return props


def script_update(settings):
    global _config, _last_controls, _address, _presets, _scene_enabled
    if _worker is None:
        return
    config = BridgeConfig(
        obs.obs_data_get_string(settings, "bridge_url"),
        obs.obs_data_get_string(settings, "token"),
        obs.obs_data_get_string(settings, "token_file"),
        obs.obs_data_get_int(settings, "timeout"),
    )
    controls = (obs.obs_data_get_int(settings, "brightness"), obs.obs_data_get_int(settings, "cct"))
    _address = obs.obs_data_get_string(settings, "address")
    changed_config = config != _config
    if changed_config:
        _config = config
        _worker.configure(config)
    if not changed_config and _last_controls is not None:
        patch = {
            key: controls[index]
            for index, key in enumerate(("brightness", "cct"))
            if _last_controls[index] != controls[index]
        }
        if patch:
            try:
                _worker.patch(patch)
            except BridgeError as error:
                _report(str(error), True)
    _last_controls = controls
    try:
        presets = parse_presets(obs.obs_data_get_string(settings, "scene_presets"))
        enabled = obs.obs_data_get_bool(settings, "scene_enabled")
    except BridgeError as error:
        presets, enabled = {}, False
        _report(str(error), True)
    changed_presets = (presets, enabled) != (_presets, _scene_enabled)
    _presets, _scene_enabled = presets, enabled
    if changed_presets:
        _program_scene()


def script_load(settings):
    global _worker, _config, _last_controls
    if _worker is not None:
        return
    _config, _last_controls = None, None
    _worker = BridgeWorker()
    script_update(settings)
    for name, label, callback in (
        ("lighttime.on", "Lighttime: On / restore brightness", _on),
        ("lighttime.off", "Lighttime: Soft off", _off),
        ("lighttime.apply", "Lighttime: Apply brightness and CCT sliders", _apply),
    ):
        hotkey = obs.obs_hotkey_register_frontend(name, label, callback)
        _hotkeys.append((name, hotkey, callback))
        bindings = obs.obs_data_get_array(settings, name)
        if bindings is not None:
            try:
                obs.obs_hotkey_load(hotkey, bindings)
            finally:
                obs.obs_data_array_release(bindings)
    obs.obs_frontend_add_event_callback(_frontend_event)
    obs.timer_add(_drain, 100)


def script_save(settings):
    for name, hotkey, _callback in _hotkeys:
        bindings = obs.obs_hotkey_save(hotkey)
        try:
            obs.obs_data_set_array(settings, name, bindings)
        finally:
            obs.obs_data_array_release(bindings)


def script_unload():
    global _worker, _config, _last_controls, _presets, _scene_enabled
    if _worker is None:
        return
    obs.timer_remove(_drain)
    obs.obs_frontend_remove_event_callback(_frontend_event)
    for _name, _hotkey, callback in _hotkeys:
        # Python scripting API takes the callback, NOT the C API hotkey ID.
        obs.obs_hotkey_unregister(callback)
    _hotkeys.clear()
    _worker.stop()
    _worker, _config, _last_controls = None, None, None
    _presets, _scene_enabled = {}, False
