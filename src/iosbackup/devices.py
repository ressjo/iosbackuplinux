"""Detecting connected iPhones/iPads and reading device information."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from PySide6.QtCore import QThread, Signal

from .tools import run_plist, run_tool

PAIRED = "paired"
PENDING_TRUST = "pending"
LOCKED = "locked"
DENIED = "denied"
UNPAIRED = "unpaired"

PAIRING_TEXT = {
    PAIRED: "Paired",
    PENDING_TRUST: "Tap “Trust” on the device",
    LOCKED: "Unlock the device",
    DENIED: "Pairing declined",
    UNPAIRED: "Not paired",
}

# Common models; unknown ones are shown with their product type.
MODEL_NAMES = {
    "iPhone12,1": "iPhone 11", "iPhone12,3": "iPhone 11 Pro", "iPhone12,5": "iPhone 11 Pro Max",
    "iPhone12,8": "iPhone SE (2nd gen)",
    "iPhone13,1": "iPhone 12 mini", "iPhone13,2": "iPhone 12", "iPhone13,3": "iPhone 12 Pro",
    "iPhone13,4": "iPhone 12 Pro Max",
    "iPhone14,4": "iPhone 13 mini", "iPhone14,5": "iPhone 13", "iPhone14,2": "iPhone 13 Pro",
    "iPhone14,3": "iPhone 13 Pro Max", "iPhone14,6": "iPhone SE (3rd gen)",
    "iPhone14,7": "iPhone 14", "iPhone14,8": "iPhone 14 Plus", "iPhone15,2": "iPhone 14 Pro",
    "iPhone15,3": "iPhone 14 Pro Max",
    "iPhone15,4": "iPhone 15", "iPhone15,5": "iPhone 15 Plus", "iPhone16,1": "iPhone 15 Pro",
    "iPhone16,2": "iPhone 15 Pro Max",
    "iPhone17,3": "iPhone 16", "iPhone17,4": "iPhone 16 Plus", "iPhone17,1": "iPhone 16 Pro",
    "iPhone17,2": "iPhone 16 Pro Max", "iPhone17,5": "iPhone 16e",
    "iPad12,1": "iPad (9th gen)", "iPad12,2": "iPad (9th gen)",
    "iPad13,18": "iPad (10th gen)", "iPad13,19": "iPad (10th gen)",
    "iPad13,1": "iPad Air (4th gen)", "iPad13,2": "iPad Air (4th gen)",
    "iPad13,16": "iPad Air (5th gen)", "iPad13,17": "iPad Air (5th gen)",
    "iPad14,1": "iPad mini (6th gen)", "iPad14,2": "iPad mini (6th gen)",
}


def model_name(product_type: str, device_class: str = "") -> str:
    if product_type in MODEL_NAMES:
        return MODEL_NAMES[product_type]
    if device_class and product_type:
        return f"{device_class} ({product_type})"
    return product_type or device_class or "Unknown device"


@dataclass
class DeviceInfo:
    udid: str
    pairing: str = UNPAIRED
    pairing_message: str = ""
    name: str = ""
    product_type: str = ""
    product_version: str = ""
    device_class: str = ""
    serial: str = ""
    data_capacity: int | None = None
    data_available: int | None = None
    will_encrypt: bool | None = None
    fetched_at: float = field(default=0.0, compare=False)

    @property
    def paired(self) -> bool:
        return self.pairing == PAIRED

    @property
    def display_name(self) -> str:
        return self.name or model_name(self.product_type, self.device_class)

    @property
    def model(self) -> str:
        return model_name(self.product_type, self.device_class)

    @property
    def is_ipad(self) -> bool:
        return self.device_class == "iPad" or self.product_type.startswith("iPad")

    @property
    def used_bytes(self) -> int | None:
        if self.data_capacity is None or self.data_available is None:
            return None
        return max(0, self.data_capacity - self.data_available)


def list_devices() -> tuple[list[str], str | None]:
    """UDIDs of all USB-connected devices (and an error message, if any)."""
    res = run_tool(["idevice_id", "-l"], timeout=10)
    if not res.ok:
        low = res.output.lower()
        if "unable to retrieve device list" in low or "usbmuxd" in low:
            return [], "usbmuxd is not running. Run: sudo systemctl start usbmuxd"
        if res.returncode in (124, 127):
            return [], res.output.strip()
        return [], None
    udids = []
    for line in res.output.splitlines():
        parts = line.split()
        if parts and not parts[0].upper().startswith("ERROR"):
            if parts[0] not in udids:
                udids.append(parts[0])
    return udids, None


def classify_pairing(returncode: int, output: str) -> str:
    low = output.lower()
    if returncode == 0 and "success" in low:
        return PAIRED
    if "denied" in low:
        return DENIED
    if "trust dialog" in low or "pairing dialog response pending" in low:
        return PENDING_TRUST
    if "passcode" in low or "password protected" in low:
        return LOCKED
    return UNPAIRED


def fetch_device_info(udid: str) -> DeviceInfo:
    info = DeviceInfo(udid=udid, fetched_at=time.monotonic())
    res = run_tool(["idevicepair", "-u", udid, "validate"])
    info.pairing = classify_pairing(res.returncode, res.output)
    info.pairing_message = res.output.strip()

    if info.paired:
        base = run_plist(["ideviceinfo", "-u", udid, "-x"])
    else:
        # simple connection without pairing – basic information only
        base = run_plist(["ideviceinfo", "-u", udid, "-s", "-x"])
    info.name = str(base.get("DeviceName", ""))
    info.product_type = str(base.get("ProductType", ""))
    info.product_version = str(base.get("ProductVersion", ""))
    info.device_class = str(base.get("DeviceClass", ""))
    info.serial = str(base.get("SerialNumber", ""))

    if info.paired:
        disk = run_plist(["ideviceinfo", "-u", udid, "-q", "com.apple.disk_usage", "-x"])
        cap = disk.get("TotalDataCapacity", disk.get("TotalDiskCapacity"))
        avail = disk.get("TotalDataAvailable", disk.get("AmountDataAvailable"))
        info.data_capacity = int(cap) if isinstance(cap, int) else None
        info.data_available = int(avail) if isinstance(avail, int) else None
        bk = run_plist(["ideviceinfo", "-u", udid, "-q", "com.apple.mobile.backup", "-x"])
        if "WillEncrypt" in bk:
            info.will_encrypt = bool(bk["WillEncrypt"])
        else:
            info.will_encrypt = False
    return info


class DeviceMonitor(QThread):
    """Polls connected devices in the background."""

    devicesChanged = Signal(list)   # list[DeviceInfo]
    listError = Signal(str)         # "" = no error

    def __init__(self, interval: float = 2.0, parent=None):
        super().__init__(parent)
        self.interval = interval
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._refresh: set[str] = set()
        self._busy: set[str] = set()
        self._infos: dict[str, DeviceInfo] = {}

    # --- control from the GUI thread ------------------------------------
    def request_refresh(self, udid: str | None = None) -> None:
        with self._lock:
            if udid is None:
                self._refresh.update(self._infos.keys())
            else:
                self._refresh.add(udid)
        self._wake.set()

    def set_busy(self, udid: str, busy: bool) -> None:
        """Do not query this device while a backup/restore is running."""
        with self._lock:
            if busy:
                self._busy.add(udid)
            else:
                self._busy.discard(udid)
                self._refresh.add(udid)
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        self.wait(10000)

    # --- Thread ---------------------------------------------------------
    def run(self) -> None:
        last_err: str | None = "__init__"
        first = True
        while not self._stop.is_set():
            udids, err = list_devices()
            if err != last_err:
                self.listError.emit(err or "")
                last_err = err
            changed = first
            first = False

            for gone in set(self._infos) - set(udids):
                with self._lock:
                    if gone in self._busy:
                        continue  # e.g. reboot during restore
                del self._infos[gone]
                changed = True

            with self._lock:
                refresh = set(self._refresh)
                self._refresh.clear()
                busy = set(self._busy)

            now = time.monotonic()
            for udid in udids:
                if self._stop.is_set():
                    return
                old = self._infos.get(udid)
                if udid in busy and old is not None:
                    continue
                need = (
                    old is None
                    or udid in refresh
                    or (not old.paired and now - old.fetched_at > 3)
                )
                if not need:
                    continue
                new = fetch_device_info(udid)
                if new != old:
                    changed = True
                self._infos[udid] = new

            if changed:
                self.devicesChanged.emit(list(self._infos.values()))
            self._wake.wait(self.interval)
            self._wake.clear()
