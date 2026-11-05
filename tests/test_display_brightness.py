"""
Screen brightness (SETTING__DISPLAY_BRIGHTNESS): the backlight dimmed with PWM
for use in low light. The rest of the suite mocks the renderer (tests/base.py),
so the real modules are loaded from their source files here.
"""
import importlib.util
import sys
import types
from dataclasses import dataclass
from pathlib import Path

from seedsigner.models.settings import Settings
from seedsigner.models.settings_definition import SettingsConstants, SettingsDefinition


SRC = Path(__file__).resolve().parents[1] / "src" / "seedsigner"


def _load(name, rel_path):
    spec = importlib.util.spec_from_file_location(name, SRC / rel_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakePWM:
    def __init__(self, log, pin, freq):
        self.log = log
        log.append(("PWM", pin, freq))

    def start(self, duty):
        self.log.append(("start", duty))

    def ChangeDutyCycle(self, duty):
        self.log.append(("duty", duty))

    def stop(self):
        self.log.append(("stop",))


def _fake_gpio(log):
    rpi = types.ModuleType("RPi")
    gpio = types.ModuleType("RPi.GPIO")
    gpio.HIGH, gpio.LOW = 1, 0
    gpio.PWM = lambda pin, freq: _FakePWM(log, pin, freq)
    gpio.output = lambda pin, value: log.append(("output", pin, value))
    rpi.GPIO = gpio
    return rpi, gpio


def _driver(pin=18):
    base = _load("_real_display_driver", "hardware/displays/display_driver.py")

    @dataclass
    class Driver(base.BaseDisplayDriver):
        def _backlight_pin(self):
            return pin

    return base, Driver(_width=240, _height=240)


def _with_gpio(log, fn):
    rpi, gpio = _fake_gpio(log)
    saved = {k: sys.modules.get(k) for k in ("RPi", "RPi.GPIO")}
    sys.modules["RPi"], sys.modules["RPi.GPIO"] = rpi, gpio
    try:
        fn()
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def test_setting_defaults_to_full_brightness():
    entry = SettingsDefinition.get_settings_entry(SettingsConstants.SETTING__DISPLAY_BRIGHTNESS)
    assert entry.default_value == 100
    assert entry.visibility == SettingsConstants.VISIBILITY__HARDWARE
    assert [v for v, _ in entry.selection_options] == [100, 75, 50, 25, 10]


def test_dimming_uses_pwm_and_full_brightness_stops_it():
    log = []
    _, disp = _driver()

    def run():
        disp.set_brightness(50)
        disp.set_brightness(25)
        disp.set_brightness(100)
    _with_gpio(log, run)

    assert log == [("PWM", 18, 1000), ("start", 50), ("duty", 25), ("stop",), ("output", 18, 1)]


def test_never_fully_dark():
    log = []
    _, disp = _driver()
    _with_gpio(log, lambda: disp.set_brightness(0))
    assert ("start", 10) in log


def test_cleanup_stops_pwm():
    log = []
    _, disp = _driver()

    def run():
        disp.set_brightness(50)
        disp.cleanup()
    _with_gpio(log, run)
    assert log[-1] == ("stop",)


def test_driver_without_a_backlight_pin_does_nothing():
    log = []
    base = _load("_real_display_driver", "hardware/displays/display_driver.py")
    disp = base.BaseDisplayDriver(_width=240, _height=240)
    _with_gpio(log, lambda: disp.set_brightness(50))
    assert log == []


def test_renderer_tells_the_driver_only_when_the_level_changes():
    renderer = _load("_real_renderer_b", "gui/renderer.py")
    calls = []
    r = renderer.Renderer.__new__(renderer.Renderer)
    r.disp = types.SimpleNamespace(set_brightness=calls.append)
    r._brightness = None

    settings = Settings.get_instance()
    settings.set_value(SettingsConstants.SETTING__DISPLAY_BRIGHTNESS, 50)
    r._apply_brightness()
    r._apply_brightness()
    settings.set_value(SettingsConstants.SETTING__DISPLAY_BRIGHTNESS, "25")   # as a SettingsQR may carry it
    r._apply_brightness()
    settings.set_value(SettingsConstants.SETTING__DISPLAY_BRIGHTNESS, 100)
    r._apply_brightness()

    assert calls == [50, 25, 100]
