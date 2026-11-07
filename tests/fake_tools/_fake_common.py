"""Shared data for the fake tools (simulated iPhone for tests/demo).

Controlled via environment variables:
  FAKE_UDIDS        comma-separated UDIDs (default: one fake UDID)
  FAKE_UNPAIRED     "1" -> device is treated as not paired
  FAKE_STATE_DIR    folder for state (encryption on/off, password)
  FAKE_FAIL         e.g. "findmy" (restore fails), "backup" (backup fails)
  FAKE_DELAY        Sekunden pro Fortschrittsschritt (Standard 0.005)
"""
import json
import os

DEFAULT_UDID = "00008110-000A1B2C3D4E5F60"


def udids():
    raw = os.environ.get("FAKE_UDIDS", DEFAULT_UDID)
    return [u for u in raw.split(",") if u]


def state_path():
    d = os.environ.get("FAKE_STATE_DIR") or os.path.join(os.path.dirname(__file__), ".state")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "state.json")


def load_state():
    try:
        with open(state_path()) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"encrypted": False, "password": ""}


def save_state(st):
    with open(state_path(), "w") as fh:
        json.dump(st, fh)


def device(udid):
    return {
        "DeviceName": "Joshua’s iPhone",
        "ProductType": "iPhone15,2",
        "ProductVersion": "18.6.2",
        "DeviceClass": "iPhone",
        "SerialNumber": "DNPXK2Q1P6KJ",
        "UniqueDeviceID": udid,
    }
