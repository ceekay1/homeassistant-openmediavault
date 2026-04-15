"""OpenMediaVault Controller (adapted for OMV7)."""

import asyncio
import pytz
from datetime import datetime, timedelta
import logging
import re

from homeassistant.const import (
    CONF_HOST,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_SSL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
)
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    DOMAIN,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    CONF_SMART_DISABLE,
    DEFAULT_SMART_DISABLE,
)
from .apiparser import parse_api
from .omv_api import OpenMediaVaultAPI

_LOGGER = logging.getLogger(__name__)


def utc_from_timestamp(timestamp: float) -> datetime:
    """Return a UTC time from a timestamp."""
    return pytz.utc.localize(datetime.utcfromtimestamp(timestamp))


class OMVControllerData:
    """OMVControllerData class (adapted for OMV7)."""

    def __init__(self, hass, config_entry):
        self.hass = hass
        self.config_entry = config_entry
        self.name = config_entry.data[CONF_NAME]
        self.host = config_entry.data[CONF_HOST]
        self._gpu_load_counter = 0

        self.data = {
            "hwinfo": {},
            "plugin": {},
            "disk": {},
            "fs": {},
            "service": {},
            "network": {},
            "kvm": {},
            "compose": {},
            "temperature": {},
            "gpuinfo": {},
            "raid": {},
        }

        self.listeners = []
        self.lock = asyncio.Lock()

        self.api = OpenMediaVaultAPI(
            hass,
            config_entry.data[CONF_HOST],
            config_entry.data[CONF_USERNAME],
            config_entry.data[CONF_PASSWORD],
            config_entry.data[CONF_SSL],
            config_entry.data[CONF_VERIFY_SSL],
        )

        self._force_update_callback = None
        self._force_hwinfo_update_callback = None

    async def async_init(self) -> None:
        self._force_update_callback = async_track_time_interval(
            self.hass, self.force_update, self.option_scan_interval
        )
        self._force_hwinfo_update_callback = async_track_time_interval(
            self.hass, self.force_hwinfo_update, timedelta(seconds=3600)
        )

    @property
    def option_scan_interval(self):
        return timedelta(
            seconds=self.config_entry.options.get(
                CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
            )
        )

    @property
    def option_smart_disable(self):
        return self.config_entry.options.get(CONF_SMART_DISABLE, DEFAULT_SMART_DISABLE)

    @property
    def signal_update(self):
        return f"{DOMAIN}-update-{self.name}"

    async def async_reset(self) -> bool:
        for unsub_dispatcher in self.listeners:
            unsub_dispatcher()
        self.listeners = []
        return True

    def connected(self):
        return self.api.connected()

    @callback
    async def force_hwinfo_update(self, _now=None):
        await self.async_hwinfo_update()

    async def async_hwinfo_update(self):
        try:
            await asyncio.wait_for(self.lock.acquire(), timeout=30)
        except Exception:
            return

        await self.hass.async_add_executor_job(self.get_hwinfo)
        await self.hass.async_add_executor_job(self.get_cpu_temperature)
        await self.hass.async_add_executor_job(self.get_disk)
        await self.hass.async_add_executor_job(self.get_services)
        await self.hass.async_add_executor_job(self.get_fs)
        await self.hass.async_add_executor_job(self.get_smart)
        await self.hass.async_add_executor_job(self.get_gpuinfo)
        await self.hass.async_add_executor_job(self.get_raid)

        self.lock.release()

    @callback
    async def force_update(self, _now=None):
        await self.async_update()

    async def async_update(self):
        if self.api.has_reconnected():
            await self.async_hwinfo_update()

        try:
            await asyncio.wait_for(self.lock.acquire(), timeout=10)
        except Exception:
            return

        await self.hass.async_add_executor_job(self.get_cpu_temperature)
        if self.api.connected():
            await self.hass.async_add_executor_job(self.get_disk)
            await self.hass.async_add_executor_job(self.get_fs)
            if not self.option_smart_disable:
                await self.hass.async_add_executor_job(self.get_smart)
            await self.hass.async_add_executor_job(self.get_services)

        await self.hass.async_add_executor_job(self.get_gpuinfo)
        await self.hass.async_add_executor_job(self.get_raid)

        async_dispatcher_send(self.hass, self.signal_update)
        self.lock.release()

    # ---------------------------
    #   CPU temp
    # ---------------------------
    def get_cpu_temperature(self):
        # RPC: api.query("cputemp", "get")
        # Response example: {"cputemp": 42.0}
        # Maps to: self.data["hwinfo"]["cputemp"]
        response = self.api.query("cputemp", "get")
        if response and "cputemp" in response:
            self.data["hwinfo"]["cputemp"] = response["cputemp"]
        else:
            _LOGGER.warning("CPU temperature not available")

    # ---------------------------
    #   HW info (new for OMV 7)
    # ---------------------------
    #    def get_hwinfo(self):
    #        response = self.api.query("system", "getInformation")
    #        if response:
    #            self.data["hwinfo"]["hostname"] = response.get("hostname")
    #            self.data["hwinfo"]["version"] = response.get("version")
    #            self.data["hwinfo"]["uptime"] = response.get("uptime")
    #
    #            # OMV 7 liefert cpuUtilization als Float (e.g. 0.49 for 49%)
    #            cpu_util = response.get("cpuUtilization")
    #            if cpu_util is not None:
    #                self.data["hwinfo"]["cpu"] = round(float(cpu_util) * 100, 1)
    #
    #            # Memory Berechnung
    #            total = int(response.get("memTotal", 1))
    #            used = int(response.get("memUsed", 0))
    #            self.data["hwinfo"]["mem"] = round((used / total) * 100, 1)
    def get_hwinfo(self):
        """Get hardware info from OMV."""
        # RPC: api.query("System", "getInformation")
        # Typical returned keys and how they're mapped below:
        # - hostname -> self.data['hwinfo']['hostname']
        # - version -> self.data['hwinfo']['version']
        # - cpuUtilization -> self.data['hwinfo']['cpuUtilization'] (rounded)
        # - memTotal -> self.data['hwinfo']['mem_total']
        # - memUsed -> self.data['hwinfo']['mem_used']
        # - memUtilization -> self.data['hwinfo']['memUsage'] (converted to percent)
        # - configDirty -> self.data['hwinfo']['configDirty'] (bool)
        # - rebootRequired -> self.data['hwinfo']['rebootRequired'] (bool)
        # - availablePkgUpdates -> self.data['hwinfo']['availablePkgUpdates'] (number)
        #   and pkgUpdatesAvailable -> boolean (availablePkgUpdates > 0)
        # - uptime -> self.data['hwinfo']['uptime']
        # - kernel -> self.data['hwinfo']['kernel']
        hwinfo = self.api.query("System", "getInformation")
        if hwinfo is None:
            return

        self.data["hwinfo"]["hostname"] = hwinfo.get("hostname")
        self.data["hwinfo"]["version"] = hwinfo.get("version")

        # CPU - must be named "cpuUtilization" according to sensor_types
        self.data["hwinfo"]["cpuUtilization"] = round(
            hwinfo.get("cpuUtilization", 0), 1
        )

        # Memory
        self.data["hwinfo"]["mem_total"] = hwinfo.get("memTotal")
        self.data["hwinfo"]["mem_used"] = hwinfo.get("memUsed")
        mem_util = hwinfo.get("memUtilization", "0")
        # expose as memUsage for compatibility with existing sensor_types
        self.data["hwinfo"]["memUsage"] = round(float(mem_util) * 100, 1)

        # Status flags - must be named exactly as in binary_sensor_types
        # OMV7 returns True/False, perfect match for data_is_on
        self.data["hwinfo"]["configDirty"] = hwinfo.get("configDirty", False)
        self.data["hwinfo"]["rebootRequired"] = hwinfo.get("rebootRequired", False)

        # Update - the file expects "pkgUpdatesAvailable"
        # OMV7 returns availablePkgUpdates (numeric), we make a True/False for the binary sensor
        updates_count = hwinfo.get("availablePkgUpdates", 0)
        self.data["hwinfo"]["pkgUpdatesAvailable"] = updates_count > 0
        # save the number as attribute, so it can be shown
        self.data["hwinfo"]["availablePkgUpdates"] = updates_count

        # Uptime & Kernel
        self.data["hwinfo"]["uptime"] = hwinfo.get("uptime")
        self.data["hwinfo"]["kernel"] = hwinfo.get("kernel")

    # ---------------------------
    #   Services
    # ---------------------------
    def get_services(self):
        # RPC: api.query("services", "getStatus")
        # Response: {"data": [ {"name":..., "title":..., "enabled":..., "running":...}, ... ]}
        # parse_api maps each service object into self.data['service'] keyed by 'name'
        response = self.api.query("services", "getStatus")
        if response and "data" in response:
            self.data["service"] = parse_api(
                data=self.data["service"],
                source=response["data"],
                key="name",
                vals=[
                    {"name": "name"},
                    {"name": "title", "default": "unknown"},
                    {"name": "enabled", "type": "bool", "default": False},
                    {"name": "running", "type": "bool", "default": False},
                ],
            )

    # ---------------------------
    #   Disk
    # ---------------------------
    def get_disk(self):
        # RPC: api.query("diskmgmt", "enumerateDevices")
        # Response is a list/dict of disk entries. Fields mapped here:
        # - devicename -> identifiers used as keys in self.data['disk']
        # - canonicaldevicefile -> for attribute queries (used by SMART)
        # - size, vendor, model, description, serialnumber
        # - israid, isroot, isreadonly (booleans)
        response = self.api.query("diskmgmt", "enumerateDevices")
        if response:
            self.data["disk"] = parse_api(
                data=self.data["disk"],
                source=response,
                key="devicename",
                vals=[
                    {"name": "devicename"},
                    {"name": "canonicaldevicefile"},
                    {"name": "size", "default": "unknown"},
                    {"name": "vendor", "default": "unknown"},
                    {"name": "model", "default": "unknown"},
                    {"name": "description", "default": "unknown"},
                    {"name": "serialnumber", "default": "unknown"},
                    {"name": "israid", "type": "bool", "default": False},
                    {"name": "isroot", "type": "bool", "default": False},
                    {"name": "isreadonly", "type": "bool", "default": False},
                ],
            )

    # ---------------------------
    #   SMART
    # ---------------------------
    def get_smart(self):
        # RPC: api.query("smart", "getList", {start:0, limit:-1})
        # Response: {"data": [ {"devicename":..., "temperature":..., "overallstatus":...}, ... ]}
        # Maps temperature and overallstatus into existing self.data['disk'] entries keyed by devicename
        tmp_smart_list = self.api.query("smart", "getList", {"start": 0, "limit": -1})
        if tmp_smart_list and "data" in tmp_smart_list:
            tmp_smart_list = tmp_smart_list["data"]
        self.data["disk"] = parse_api(
            data=self.data["disk"],
            source=tmp_smart_list,
            key="devicename",
            vals=[
                {"name": "temperature", "default": 0},
                {"name": "overallstatus", "default": "unknown"},
            ],
        )
        for uid in self.data["disk"]:
            if self.data["disk"][uid]["devicename"].startswith(
                ("mmcblk", "sr", "bcache")
            ):
                continue
            attrs = self.api.query(
                "smart",
                "getAttributes",
                {"devicefile": self.data["disk"][uid]["canonicaldevicefile"]},
            )
            if not attrs:
                continue
            # RPC: api.query("smart", "getAttributes", {devicefile: ...})
            # Response: list/dict of SMART attributes with keys: attrname, threshold, rawvalue
            # We parse them into tmp_data keyed by attrname so specific attributes can be copied into
            # self.data['disk'][uid][<ATTR_NAME>] = rawvalue
            tmp_data = parse_api(
                data={},
                source=attrs,
                key="attrname",
                vals=[
                    {"name": "attrname"},
                    {"name": "threshold", "default": 0},
                    {"name": "rawvalue", "default": 0},
                ],
            )
            for val in [
                "Raw_Read_Error_Rate",
                "Spin_Up_Time",
                "Start_Stop_Count",
                "Reallocated_Sector_Ct",
                "Seek_Error_Rate",
                "Load_Cycle_Count",
                "UDMA_CRC_Error_Count",
                "Multi_Zone_Error_Rate",
            ]:
                if val in tmp_data:
                    raw = tmp_data[val]["rawvalue"]
                    if isinstance(raw, str) and " " in raw:
                        raw = raw.split(" ")[0]
                    self.data["disk"][uid][val] = raw

    # ---------------------------
    #   Filesystem
    # ---------------------------
    def get_fs(self):
        response = self.api.query("filesystemmgmt", "enumerateFilesystems")
        if response:
            self.data["fs"] = parse_api(
                data=self.data["fs"],
                source=response,
                key="uuid",
                vals=[
                    {"name": "uuid"},
                    {"name": "parentdevicefile", "default": "unknown"},
                    {"name": "label", "default": "unknown"},
                    {"name": "type", "default": "unknown"},
                    {"name": "mounted", "type": "bool", "default": False},
                    {"name": "devicename", "default": "unknown"},
                    {"name": "available", "default": 0},
                    {"name": "size", "default": 0},
                    {"name": "percentage", "default": 0},
                ],
            )

    # ---------------------------
    #   GPU Info
    # ---------------------------
    def get_gpuinfo(self):
        PATH_GPU_CUR_FREQ = "/sys/class/drm/card0/gt_cur_freq_mhz"
        PATH_GPU_MAX_FREQ = "/sys/class/drm/card0/gt_max_freq_mhz"
        CONFIRMATION_COUNT = 2

        cur_freq = None
        try:
            with open(PATH_GPU_CUR_FREQ, "r") as f:
                cur_freq = int(f.read().strip())
        except Exception:
            self.data["gpuinfo"] = {}
            return

        max_freq = None
        try:
            with open(PATH_GPU_MAX_FREQ, "r") as f:
                max_freq = int(f.read().strip())
        except Exception:
            max_freq = self.data["gpuinfo"].get("max_freq")

        load_percent = 0
        if cur_freq and max_freq and max_freq > 0:
            load_percent = round((cur_freq / max_freq) * 100, 1)

        if load_percent > 0:
            self._gpu_load_counter += 1
        else:
            self._gpu_load_counter = 0

        should_update = (load_percent == 0) or (
            self._gpu_load_counter >= CONFIRMATION_COUNT
        )
        if not should_update:
            return

        self.data["gpuinfo"] = {
            "vendor": "intel",
            "model": "Intel Graphics (from sysfs)",
            "load_percent": load_percent,
            "cur_freq": cur_freq,
            "max_freq": max_freq,
        }

    # ---------------------------
    #   RAID
    # ---------------------------
    def get_raid(self):
        PATH_MDSTAT = "/proc/mdstat"
        self.data["raid"] = {}
        try:
            with open(PATH_MDSTAT, "r") as f:
                lines = f.readlines()
        except FileNotFoundError:
            return

        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith("md"):
                parts = line.split()
                device = parts[0]
                state = parts[2]
                raid_level = parts[3]
                i += 1
                health_line = lines[i].strip()
                health_indicator = health_line.split("[")[-1].split("]")[0]
                status = "clean"
                if i + 1 < len(lines) and (
                    "resync" in lines[i + 1]
                    or "check" in lines[i + 1]
                    or "recover" in lines[i + 1]
                ):
                    i += 1
                    action_line = lines[i].strip()
                    match = re.search(r"(\w+)\s*=\s*([\d\.]+)%", action_line)
                    if match:
                        status = match.group(1)
                elif "_" in health_indicator:
                    status = "degraded"
                self.data["raid"][device] = {
                    "device": device,
                    "state": state,
                    "level": raid_level,
                    "health": status,
                    "health_indicator": health_indicator,
                }
            i += 1
