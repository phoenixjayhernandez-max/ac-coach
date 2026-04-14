"""
Reads live telemetry data from Assetto Corsa via Windows shared memory.
AC writes physics, graphics, and static data into named memory blocks
that any app on the same PC can read in real time.
"""

import ctypes
import platform
import sys
from dataclasses import dataclass
from typing import Optional


# ---------------------------------------------------------------------------
# AC status codes
# ---------------------------------------------------------------------------
AC_OFF    = 0
AC_REPLAY = 1
AC_LIVE   = 2
AC_PAUSE  = 3


# ---------------------------------------------------------------------------
# Shared memory structures (mirrors the AC C++ SDK header exactly)
# ---------------------------------------------------------------------------

class SPageFilePhysics(ctypes.Structure):
    _fields_ = [
        ("packetId",            ctypes.c_int),
        ("gas",                 ctypes.c_float),
        ("brake",               ctypes.c_float),
        ("fuel",                ctypes.c_float),
        ("gear",                ctypes.c_int),
        ("rpm",                 ctypes.c_int),
        ("steerAngle",          ctypes.c_float),
        ("speedKmh",            ctypes.c_float),
        ("velocity",            ctypes.c_float * 3),
        ("accG",                ctypes.c_float * 3),
        ("wheelSlip",           ctypes.c_float * 4),
        ("wheelLoad",           ctypes.c_float * 4),
        ("wheelsPressure",      ctypes.c_float * 4),
        ("wheelAngularSpeed",   ctypes.c_float * 4),
        ("tyreWear",            ctypes.c_float * 4),
        ("tyreDirtyLevel",      ctypes.c_float * 4),
        ("tyreCoreTempI",       ctypes.c_float * 4),
        ("camberRAD",           ctypes.c_float * 4),
        ("suspensionTravel",    ctypes.c_float * 4),
        ("drs",                 ctypes.c_float),
        ("tc",                  ctypes.c_float),
        ("heading",             ctypes.c_float),
        ("pitch",               ctypes.c_float),
        ("roll",                ctypes.c_float),
        ("cgHeight",            ctypes.c_float),
        ("carDamage",           ctypes.c_float * 5),
        ("numberOfTyresOut",    ctypes.c_int),
        ("pitLimiterOn",        ctypes.c_int),
        ("abs",                 ctypes.c_float),
        ("kersCharge",          ctypes.c_float),
        ("kersInput",           ctypes.c_float),
        ("autoShifterOn",       ctypes.c_int),
        ("rideHeight",          ctypes.c_float * 2),
        ("turboBoost",          ctypes.c_float),
        ("ballast",             ctypes.c_float),
        ("airDensity",          ctypes.c_float),
        ("airTemp",             ctypes.c_float),
        ("roadTemp",            ctypes.c_float),
        ("localAngularVel",     ctypes.c_float * 3),
        ("finalFF",             ctypes.c_float),
        ("performanceMeter",    ctypes.c_float),
        ("engineBrake",         ctypes.c_int),
        ("ersRecoveryLevel",    ctypes.c_int),
        ("ersPowerLevel",       ctypes.c_int),
        ("ersHeatCharging",     ctypes.c_int),
        ("ersIsCharging",       ctypes.c_int),
        ("kersCurrentKJ",       ctypes.c_float),
        ("drsAvailable",        ctypes.c_int),
        ("drsEnabled",          ctypes.c_int),
        ("brakeTemp",           ctypes.c_float * 4),
        ("clutch",              ctypes.c_float),
        ("tyreTempI",           ctypes.c_float * 4),
        ("tyreTempM",           ctypes.c_float * 4),
        ("tyreTempO",           ctypes.c_float * 4),
        ("isAIControlled",      ctypes.c_int),
        ("tyreContactPoint",    (ctypes.c_float * 3) * 4),
        ("tyreContactNormal",   (ctypes.c_float * 3) * 4),
        ("tyreContactHeading",  (ctypes.c_float * 3) * 4),
        ("brakeBias",           ctypes.c_float),
        ("localVelocity",       ctypes.c_float * 3),
    ]


class SPageFileGraphics(ctypes.Structure):
    _fields_ = [
        ("packetId",                    ctypes.c_int),
        ("status",                      ctypes.c_int),   # AC_OFF/LIVE/PAUSE/REPLAY
        ("session",                     ctypes.c_int),
        ("currentTime",                 ctypes.c_wchar * 15),
        ("lastTime",                    ctypes.c_wchar * 15),
        ("bestTime",                    ctypes.c_wchar * 15),
        ("split",                       ctypes.c_wchar * 15),
        ("completedLaps",               ctypes.c_int),
        ("position",                    ctypes.c_int),
        ("iCurrentTime",                ctypes.c_int),   # milliseconds
        ("iLastTime",                   ctypes.c_int),   # milliseconds
        ("iBestTime",                   ctypes.c_int),   # milliseconds
        ("sessionTimeLeft",             ctypes.c_float),
        ("distanceTraveled",            ctypes.c_float),
        ("isInPit",                     ctypes.c_int),
        ("currentSectorIndex",          ctypes.c_int),
        ("lastSectorTime",              ctypes.c_int),
        ("numberOfLaps",                ctypes.c_int),
        ("tyreCompound",                ctypes.c_wchar * 33),
        ("replayTimeMultiplier",        ctypes.c_float),
        ("normalizedCarPosition",       ctypes.c_float),
        ("carCoordinates",              ctypes.c_float * 3),
        ("penaltyTime",                 ctypes.c_float),
        ("flag",                        ctypes.c_int),
        ("idealLineOn",                 ctypes.c_int),
        ("isInPitLane",                 ctypes.c_int),
        ("surfaceGrip",                 ctypes.c_float),
        ("mandatoryPitDone",            ctypes.c_int),
        ("windSpeed",                   ctypes.c_float),
        ("windDirection",               ctypes.c_float),
        ("isSetupMenuVisible",          ctypes.c_int),
        ("mainDisplayIndex",            ctypes.c_int),
        ("secondaryDisplayIndex",       ctypes.c_int),
        ("tc",                          ctypes.c_int),
        ("tcCut",                       ctypes.c_int),
        ("engineMap",                   ctypes.c_int),
        ("abs",                         ctypes.c_int),
        ("fuelXLap",                    ctypes.c_float),
        ("rainLights",                  ctypes.c_int),
        ("flashingLights",              ctypes.c_int),
        ("lightsStage",                 ctypes.c_int),
        ("exhaustTemperature",          ctypes.c_float),
        ("wiperLV",                     ctypes.c_int),
        ("driverStintTotalTimeLeft",    ctypes.c_int),
        ("driverStintTimeLeft",         ctypes.c_int),
        ("rainTyres",                   ctypes.c_int),
    ]


class SPageFileStatic(ctypes.Structure):
    _fields_ = [
        ("smVersion",               ctypes.c_wchar * 15),
        ("acVersion",               ctypes.c_wchar * 15),
        ("numberOfSessions",        ctypes.c_int),
        ("numCars",                 ctypes.c_int),
        ("carModel",                ctypes.c_wchar * 33),
        ("track",                   ctypes.c_wchar * 33),
        ("playerName",              ctypes.c_wchar * 33),
        ("playerSurname",           ctypes.c_wchar * 33),
        ("playerNick",              ctypes.c_wchar * 33),
        ("sectorCount",             ctypes.c_int),
        ("maxTorque",               ctypes.c_float),
        ("maxPower",                ctypes.c_float),
        ("maxRpm",                  ctypes.c_int),
        ("maxFuel",                 ctypes.c_float),
        ("suspensionMaxTravel",     ctypes.c_float * 4),
        ("tyreRadius",              ctypes.c_float * 4),
        ("maxTurboBoost",           ctypes.c_float),
        ("deprecated_1",            ctypes.c_float),
        ("deprecated_2",            ctypes.c_float),
        ("penaltiesEnabled",        ctypes.c_int),
        ("aidFuelRate",             ctypes.c_float),
        ("aidTireRate",             ctypes.c_float),
        ("aidMechanicalDamage",     ctypes.c_float),
        ("aidAllowTyreBlankets",    ctypes.c_int),
        ("aidStability",            ctypes.c_float),
        ("aidAutoClutch",           ctypes.c_int),
        ("aidAutoBlip",             ctypes.c_int),
        ("hasDRS",                  ctypes.c_int),
        ("hasERS",                  ctypes.c_int),
        ("hasKERS",                 ctypes.c_int),
        ("kersMaxJ",                ctypes.c_float),
        ("engineBrakeSettingsCount",ctypes.c_int),
        ("ersPowerControllerCount", ctypes.c_int),
        ("trackSplineLength",       ctypes.c_float),
        ("trackConfiguration",      ctypes.c_wchar * 33),
        ("ersMaxJ",                 ctypes.c_float),
        ("isTimedRace",             ctypes.c_int),
        ("hasExtraLap",             ctypes.c_int),
        ("carSkin",                 ctypes.c_wchar * 33),
        ("reversedGridPositions",   ctypes.c_int),
        ("pitWindowStart",          ctypes.c_int),
        ("pitWindowEnd",            ctypes.c_int),
    ]


# ---------------------------------------------------------------------------
# Reader class — keeps shared memory handles open for fast polling
# ---------------------------------------------------------------------------

class ACTelemetryReader:
    """
    Opens a persistent connection to AC's three shared memory blocks
    and lets you read live data at any time.
    """

    def __init__(self):
        self._physics_handle  = None
        self._graphics_handle = None
        self._static_handle   = None
        self._physics_ptr     = None
        self._graphics_ptr    = None
        self._static_ptr      = None
        self.connected        = False

    # ------------------------------------------------------------------
    def connect(self) -> bool:
        """Try to open all three shared memory blocks. Returns True on success."""
        if platform.system() != "Windows":
            print("[reader] Not on Windows — shared memory unavailable.")
            return False
        try:
            self._physics_handle, self._physics_ptr = self._open("Local\\acpmf_physics", SPageFilePhysics)
            self._graphics_handle, self._graphics_ptr = self._open("Local\\acpmf_graphics", SPageFileGraphics)
            self._static_handle, self._static_ptr = self._open("Local\\acpmf_static", SPageFileStatic)
            self.connected = (
                self._physics_ptr is not None and
                self._graphics_ptr is not None and
                self._static_ptr is not None
            )
            return self.connected
        except Exception as e:
            print(f"[reader] Connect error: {e}")
            return False

    def disconnect(self):
        FILE_MAP_READ = 0x0004
        for ptr, handle in [
            (self._physics_ptr,  self._physics_handle),
            (self._graphics_ptr, self._graphics_handle),
            (self._static_ptr,   self._static_handle),
        ]:
            if ptr:
                ctypes.windll.kernel32.UnmapViewOfFile(ptr)
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
        self.connected = False

    # ------------------------------------------------------------------
    def read_physics(self) -> Optional[SPageFilePhysics]:
        return self._read(self._physics_ptr, SPageFilePhysics)

    def read_graphics(self) -> Optional[SPageFileGraphics]:
        return self._read(self._graphics_ptr, SPageFileGraphics)

    def read_static(self) -> Optional[SPageFileStatic]:
        return self._read(self._static_ptr, SPageFileStatic)

    # ------------------------------------------------------------------
    @staticmethod
    def _open(name: str, struct_type):
        FILE_MAP_READ = 0x0004
        handle = ctypes.windll.kernel32.OpenFileMappingW(FILE_MAP_READ, False, name)
        if not handle:
            return None, None
        size = ctypes.sizeof(struct_type)
        ptr = ctypes.windll.kernel32.MapViewOfFile(handle, FILE_MAP_READ, 0, 0, size)
        if not ptr:
            ctypes.windll.kernel32.CloseHandle(handle)
            return None, None
        return handle, ptr

    @staticmethod
    def _read(ptr, struct_type):
        if not ptr:
            return None
        size = ctypes.sizeof(struct_type)
        buf = (ctypes.c_byte * size)()
        ctypes.memmove(buf, ptr, size)
        return struct_type.from_buffer_copy(buf)


# ---------------------------------------------------------------------------
# Helper: format milliseconds -> "1:23.456"
# ---------------------------------------------------------------------------

def ms_to_laptime(ms: int) -> str:
    if ms <= 0:
        return "--:--.---"
    total_seconds = ms / 1000.0
    minutes = int(total_seconds // 60)
    seconds = total_seconds % 60
    return f"{minutes}:{seconds:06.3f}"
