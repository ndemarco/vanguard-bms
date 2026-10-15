"""Pack simulator and log replay.

The simulator models a Vanguard BMS closely enough to exercise a host: it emits the
periodic frames of CANBUS_PROTOCOL.md on both buses at the documented periods and
reacts to a simulated host (``0x105`` keep-alive, ``0x515`` sleep, CAN2 service
frames, ``0x614`` service channel). It runs on simulated time: nothing sleeps unless
the command line asks for real-time pacing.

Library use::

    sim = PackSimulator(seed=1)
    sim.receive(1, 0x105, bytes([0x00, 0x11, 0, 0, 0, 0, 0, 0]))
    frames = sim.advance(100_000)   # 100 ms of simulated time -> list[wire.Frame]

Command line::

    python -m vanguard.simulator --duration 10            # fast, prints R and S lines
    python -m vanguard.simulator --realtime --duration 0  # run until interrupted
    python -m vanguard.simulator --replay log.jsonl       # replay a JSON lines log

Modelled simplifications (not behaviour verified on a live pack): faults are tripped
immediately rather than after the documented multi-second filters; the pack/output ADC
voltages are zero with the contactors open and follow a precharge ramp afterwards;
lifetime and trip counters are constants.
"""

from __future__ import annotations

import argparse
import math
import random
import sys
import time
from typing import Iterable, Iterator, Optional

from .encoder import encode
from .frames import (
    CAN1, CAN1_CHAIN_1, CAN1_CHAIN_2, CAN2, CAN2_SPECS, FAULT_BITS, PACK_TX, SPEC_BY_KEY, SPEC_BY_NAME,
)
from .wire import Frame, Status, format_frame, format_status, read_jsonl

SCHED_US = 2_000  # firmware scheduler granularity
PASS_US = 10_000  # firmware main-loop pass
KEEPALIVE_TIMEOUT_US = 1_000_000
KEEPALIVE_LONG_TIMEOUT_US = 1_200_000_000  # 0x105 D1 bit 2: about 20 minutes
SDO_TIMEOUT_US = 100_000_000  # any 0x614 frame: about 100 s
SLEEP_AFTER_TIMEOUT_US = 300_000_000
CAPACITY_AH = 73.0  # variant 0

_CAN2_PERIODIC = tuple(s.can_id for s in CAN2_SPECS if s.direction == PACK_TX and s.period_ms)

_FAULT_BIT = {name: (byte, bit) for byte, bit, name, _c, _b in FAULT_BITS}
_BLOCKING_MASKS = (0xFF, 0xAF, 0xFF, 0xAF, 0xFF, 0xDF, 0xFF, 0xFF)  # 0x120 bytes that force idle (can1.md)

_SDO_VALUES = {
    (0x1000, 0): (0x55AA01A2, 4), (0x1018, 0): (4, 1), (0x1018, 1): (1, 4),
    (0x1018, 2): (0x7B, 4), (0x1018, 3): (0x00010001, 4), (0x1018, 4): (0x00BC614E, 4),
}


class PackSimulator:
    def __init__(self, *, seed: int = 1, start_us: int = 0, enable_asserted: bool = True,
                 soc_pct: float = 45.0, load_current_a: float = -2.0, charge_current_a: float = 10.0):
        self.rng = random.Random(seed)
        self.now_us = start_us
        self.enable_asserted = enable_asserted
        self.soc_pct = soc_pct
        self.load_current_a = load_current_a  # used when closed in the drive path
        self.charge_current_a = charge_current_a  # used when closed in the charge path
        self.cell_offsets_mv = [max(-15, min(15, round(self.rng.gauss(0, 6)))) for _ in range(14)]
        self.cell_override_mv: Optional[list] = None
        self.temps_c = [self.rng.randint(21, 26) for _ in range(14)]
        self.inputs = {"rb11": False, "rb13": False, "rb14": False}  # True = pin low
        self._status: list = []
        self._outbox: list = []
        self._next_tick = start_us + SCHED_US
        self._boot(start_us)

    # ------------------------------------------------------------------ state

    def _boot(self, t: int) -> None:
        self.sleeping = False
        self.service = False
        self.mode = 0x10  # just booted, behaves as idle until a request arrives
        self.d0 = 0
        self.d1 = 0
        self.last_105_us: Optional[int] = None
        self.timeout_us = KEEPALIVE_TIMEOUT_US
        self.last_clear_us = -10**12
        self.fault = [0] * 8
        self.latched_word = 0
        self.live_word = 0
        self.config_byte = 9
        self.phase = "open"  # open, close_b, precharge, close_a, precharge_off, closed, opening
        self.phase_t = t
        self.drv_a = False
        self.drv_b = False
        self.precharge = False
        self.v_out = 0.0
        self.counter = 0
        self.sleep_at_us: Optional[int] = None
        self.sleep_value = 0
        self.balancing_mask = 0
        self.last_21: bytes = bytes(8)
        self._pass_acc = 0
        self.last_sent = {(CAN1, i): t for i in CAN1_CHAIN_1 + CAN1_CHAIN_2}
        self.last_sent.update({(CAN2, i): t for i in _CAN2_PERIODIC})
        self.current_a = 0.0

    def status(self, ts_us: int, text: str) -> None:
        self._status.append((ts_us, text))

    def drain_status(self) -> list:
        out, self._status = self._status, []
        return out

    @property
    def contactors_closed(self) -> bool:
        return self.phase == "closed"

    def cells_mv(self) -> list:
        if self.cell_override_mv is not None:
            return list(self.cell_override_mv)
        base = 3300 + 9.0 * self.soc_pct
        return [round(base + o) for o in self.cell_offsets_mv]

    def set_cells_mv(self, cells: Optional[list]) -> None:
        if cells is not None and len(cells) != 14:
            raise ValueError("need 14 cell voltages")
        self.cell_override_mv = None if cells is None else list(cells)

    def set_enable(self, asserted: bool) -> None:
        self.enable_asserted = asserted

    def _set_fault(self, name: str) -> None:
        byte, bit = _FAULT_BIT[name]
        if not self.fault[byte] >> bit & 1:
            self.status(self.now_us, f"fault set: {name}")
        self.fault[byte] |= 1 << bit

    def blocking_fault(self) -> bool:
        return any(self.fault[i] & _BLOCKING_MASKS[i] for i in range(8)) or self.latched_word != 0

    # --------------------------------------------------------------- host side

    def receive(self, bus: int, can_id: int, data: bytes) -> None:
        """Feed a frame the host puts on a bus, at the current simulated time."""
        data = bytes(data).ljust(8, b"\x00")  # firmware does not check DLC; missing bytes read as stale/zero
        if self.sleeping:
            if bus == CAN1 and self.soc_pct > 4.9:
                self.status(self.now_us, "wake on CAN1 activity")
                self._boot(self.now_us)
            else:
                return
        if bus == CAN1 and can_id == 0x105:
            self._on_keepalive(data)
        elif bus == CAN1 and can_id == 0x515:
            if data[0] & 0x30 and self.sleep_at_us is None:
                self.sleep_at_us = self.now_us + 500_000
                self.sleep_value = (data[0] & 0x30) >> 4
        elif bus == CAN1 and can_id == 0x614:
            self.timeout_us = SDO_TIMEOUT_US
            self._on_sdo(data)
        elif bus == CAN2 and can_id == 0x0CFF00F3:
            self._on_maintenance(data)
        elif bus == CAN2 and can_id == 0x0CFFBC49:
            self.config_byte = data[4]
            if data[4] in (3, 5):
                self.live_word &= ~(1 << 8)
            else:
                self.live_word |= 1 << 8
        # 0x0C1BF349, 0x0C1BFF49, 0x18DFFFF3 and unknown IDs are ignored

    def _on_keepalive(self, d: bytes) -> None:
        self.last_105_us = self.now_us
        self.d0, self.d1 = d[0], d[1]
        self.timeout_us = KEEPALIVE_LONG_TIMEOUT_US if d[1] & 0x04 else KEEPALIVE_TIMEOUT_US
        if d[1] & 0x02 and self.now_us - self.last_clear_us >= 100_000:
            self.last_clear_us = self.now_us
            keep = 0x82
            self.fault = [0, 0, 0, 0, 0, self.fault[5] & keep, 0, 0]
            self.latched_word &= 1 << 15
            self.status(self.now_us, "faults cleared")

    def _on_sdo(self, d: bytes) -> None:
        # Only reads of identity objects are answered. Writes (including the
        # bootloader reboot object 0x1F51) are deliberately ignored.
        if d[0] != 0x40:
            return
        key = (int.from_bytes(d[1:3], "little"), d[3])
        if key in _SDO_VALUES:
            val, n = _SDO_VALUES[key]
            self._outbox.append((CAN1, 0x594, bytes([0x43]) + d[1:4] + val.to_bytes(n, "little")))

    def _on_maintenance(self, d: bytes) -> None:
        if not self.service:
            self.status(self.now_us, "service mode entered")
        self.service = True
        self.mode = 8
        self._open_now()
        if d[0] == 0x00:
            self.service = False
            self.mode = 1
            self.status(self.now_us, "service mode left")
        # Other commands are not modelled (no persistent state is changed).

    # ----------------------------------------------------------------- logic

    def _open_now(self) -> None:
        if self.phase not in ("open", "opening"):
            self.phase = "opening"
            self.phase_t = self.now_us
            self.drv_b = False  # B off first
            self.precharge = False
            self.status(self.now_us, "contactors opening")

    def _host_silent_us(self) -> Optional[int]:
        return None if self.last_105_us is None else self.now_us - self.last_105_us

    def _pass(self) -> None:
        """One 10 ms main-loop pass."""
        if self.sleeping:
            return
        now = self.now_us
        cells = self.cells_mv()
        # Protection trips (immediate here; the firmware filters them over seconds).
        if max(cells) >= 4250:
            self._set_fault("cell_overvoltage_trip"); self.latched_word |= 1 << 4
        if min(cells) < 2500 and self.mode == 2:
            self._set_fault("cell_undervoltage_trip"); self.latched_word |= 1
        if max(self.temps_c) >= 70:
            self._set_fault("overtemperature"); self.latched_word |= 1 << 1
        if min(self.temps_c) <= -20:
            self._set_fault("undertemperature_or_few_sensors"); self.latched_word |= 1 << 1

        running = self.mode in (2, 4)
        if running and not self.enable_asserted:
            self._set_fault("enable_input_released"); self.latched_word |= 1 << 10
        silent = self._host_silent_us()
        if running and silent is not None and silent > self.timeout_us:
            self._set_fault("keepalive_timeout"); self.latched_word |= 1 << 8
            self.d0 &= 0xAB  # aux output pins fall
        if silent is not None and silent > self.timeout_us + SLEEP_AFTER_TIMEOUT_US:
            self._enter_sleep(0x15)
            return
        if self.sleep_at_us is not None and now >= self.sleep_at_us:
            self._enter_sleep(3 if self.sleep_value == 2 else 2)
            return

        # Mode selection
        if self.service:
            self.mode = 8
        elif self.last_105_us is not None:
            enabled = bool(self.d1 & 0x10) and not self.blocking_fault() and self.enable_asserted
            new = (4 if self.d0 & 0x03 else 2) if enabled else 1
            if new != self.mode:
                self.status(now, f"mode {self.mode:#x} -> {new:#x}")
            self.mode = new

        want_close = self.mode in (2, 4) and not self.blocking_fault()
        self._contactors(want_close, sum(cells) / 1000.0)

        # Current, SOC, balancing
        if self.phase == "closed":
            base = self.charge_current_a if self.mode == 4 else self.load_current_a
            self.current_a = round(base + self.rng.uniform(-0.2, 0.2), 2)
        else:
            self.current_a = 0.0
        self._pass_acc += PASS_US
        if self._pass_acc >= 1_000_000:  # coulomb counting once a second
            self._pass_acc -= 1_000_000
            self.soc_pct = min(100.0, max(0.0, self.soc_pct + self.current_a / (CAPACITY_AH * 3600) * 100))
        self.balancing_mask = 0
        if self.mode == 4 and max(cells) >= 3800:
            lo = min(cells)
            for i, c in enumerate(cells):
                if c - lo >= 40:
                    self.balancing_mask |= 1 << i

    def _contactors(self, want_close: bool, v_pack: float) -> None:
        now, ph = self.now_us, self.phase
        el = now - self.phase_t
        if not want_close:
            if ph == "opening":
                if el >= 150_000:
                    self.drv_a = False
                if el >= 300_000:
                    self.phase, self.phase_t = "open", now
                    self.v_out = 0.0
                    self.status(now, "contactors open")
            elif ph != "open":
                self._open_now()
            return
        if ph == "opening":
            if el >= 300_000:
                self.phase, self.phase_t = "open", now
            return
        if ph == "open":
            self.phase, self.phase_t, self.drv_b = "close_b", now, True
            self.status(now, "closing: driver B")
        elif ph == "close_b" and el >= 80_000:
            self.phase, self.phase_t, self.precharge = "precharge", now, True
        elif ph == "precharge":
            self.v_out = v_pack * (1 - math.exp(-el / 150_000))
            if self.v_out >= 0.7 * v_pack:
                self.phase, self.phase_t, self.drv_a = "close_a", now, True
        elif ph == "close_a" and el >= 80_000:
            self.phase, self.phase_t, self.precharge = "precharge_off", now, False
            self.v_out = v_pack
        elif ph == "precharge_off" and el >= 80_000:
            self.phase, self.phase_t = "closed", now
            self.status(now, "contactors closed")
        elif ph == "closed":
            self.v_out = v_pack

    def _enter_sleep(self, reason: int) -> None:
        self.status(self.now_us, f"sleep (reason {reason:#x})")
        self._open_now()
        self.drv_a = self.drv_b = self.precharge = False
        self.phase = "open"
        self.sleeping = True
        self.sleep_at_us = None
        # One-shot frames go out as the pack sleeps.
        self._outbox.append((CAN1, 0x120, self._build("fault_bytes")))
        self._outbox.append((CAN2, 0x18FF08F3, self._build("c2_status_words")))
        self._outbox.append((CAN2, 0x0CF096F3, self._build("c2_output_state")))

    # ------------------------------------------------------------- frame data

    def _build(self, name: str) -> bytes:
        spec = SPEC_BY_NAME[name]
        vals, raw = getattr(self, "_v_" + name)()
        return encode(spec.name, None, vals, raw=raw)

    def _mode_c2(self) -> str:
        return {0x10: "startup", 1: "off_idle", 2: "discharge_run", 4: "charge", 8: "service"}[self.mode]

    def _temp_stats(self):
        return max(self.temps_c), min(self.temps_c), round(sum(self.temps_c) / len(self.temps_c))

    def _warn_nonzero(self) -> bool:
        return bool(self.live_word or self.fault[1] & 0x50 or self.fault[5] & 0x20)

    # CAN1
    def _v_pack_current_limits(self):
        self.counter = (self.counter + 1) & 0xFF
        return {"pack_current_ma": round(self.current_a * 1000), "discharge_limit_a": 120.0,
                "charge_limit_a": 36.0, "counter": (self.counter - 1) & 0xFF}, None

    def _v_status_voltages(self):
        vp = sum(self.cells_mv()) / 1000.0
        return {"any_fault_byte_nonzero": any(self.fault), "warning_word_nonzero": self._warn_nonzero(),
                "contactor_a_driven": self.drv_a, "contactor_b_driven": self.drv_b,
                "pack_side_adc_v": vp if self.drv_b else 0.0, "pack_voltage_v": vp,
                "output_side_adc_v": self.v_out}, None

    def _v_state_soc(self):
        charging = self.mode == 4
        return {"charge_mode": charging, "aux_echo_bit6": bool(self.d0 & 0x40),
                "balancing_active": bool(self.balancing_mask), "aux_echo_bit2": bool(self.d0 & 0x04),
                "aux_echo_bit4": bool(self.d0 & 0x10), "input_rb13_asserted": self.inputs["rb13"],
                "input_rb14_asserted": self.inputs["rb14"],
                "substate_entered": charging, "substate_closing_or_closed": charging and self.phase != "open",
                "substate_blocked_by_fault": charging and self.blocking_fault(),
                "model_code": 1, "soh_pct": 100.0, "soc_pct": round(self.soc_pct * 2) / 2,
                "temp_charge_limit_a": 36 if charging else 0}, None

    def _v_temp_cell_extremes(self):
        hot, cool, _ = self._temp_stats()
        c = self.cells_mv()
        return {"hottest_temp_c": hot, "coolest_temp_c": cool, "max_cell_mv": max(c), "min_cell_mv": min(c)}, None

    def _v_fault_bytes(self):
        return {f"b{i}": self.fault[i] for i in range(8)}, None

    def _v_identity(self):
        return {"identity_a": 0, "identity_b_byte4": 0, "identity_b": 0}, None

    def _v_voltage_limits(self):
        return {"low_cell_limit_v": 2.50, "high_cell_limit_v": 4.25, "low_pack_limit_v": 35.00,
                "high_pack_limit_v": 58.80}, None

    def _v_capacity_ratings(self):
        return {"capacity_ah": 73, "current_rating_1_a": 150.0, "current_rating_2_a": 240.0, "constant_5": 5,
                "low_temp_limit_c": -20, "high_temp_limit_c": 70}, None

    def _counters(self, names, base):
        return {n: v for n, v in zip(names, base)}, None

    def _v_trip_ah(self):
        return self._counters(["trip_ah_discharge", "trip_ah_net", "trip_ah_regen", "trip_ah_charger"], [12, 0, 3, 0])

    def _v_trip_wh(self):
        return self._counters(["trip_wh_discharge", "trip_wh_net", "trip_wh_regen", "trip_wh_charger"], [620, 0, 150, 0])

    def _v_lifetime_wh_net_regen(self):
        return {"lifetime_wh_net": 1_200, "lifetime_wh_regen": 5_000}, None

    def _v_lifetime_wh_charger_discharge(self):
        return {"lifetime_wh_charger": 80_000, "lifetime_wh_discharge": 78_800}, None

    def _v_lifetime_ah_net_regen(self):
        return {"lifetime_ah_net": 25, "lifetime_ah_regen": 100}, None

    def _v_lifetime_ah_charger_discharge(self):
        return {"lifetime_ah_charger": 1_500, "lifetime_ah_discharge": 1_475}, None

    def _v_version_target(self):
        return {"fw_major": 2, "fw_minor": 9, "fw_patch": 7, "board_address": 0x28, "charge_target_mv": 4150,
                "model_constant": 0}, None

    # CAN2
    def _cur(self):
        return round(self.current_a, 2)

    def _v_c2_summary(self):
        cells = self.cells_mv()
        return {"mode": self._mode_c2(), "fault_present": int(any(self.fault)),
                "remaining_capacity_ah": round(CAPACITY_AH * self.soc_pct / 100, 2),
                "cell_sum_mv": sum(cells), "current_a": self._cur()}, None

    def _v_c2_extremes(self):
        hot, cool, _ = self._temp_stats()
        c = self.cells_mv()
        return {"max_temp_c": hot, "min_temp_c": cool, "max_cell_mv": max(c), "min_cell_mv": min(c)}, None

    def _v_c2_soc_x640(self):
        # Reproduce the firmware defect: bytes 0-3 carry stale bytes of the previous 0x0CFF21F3.
        return {"soc_pct": round(self.soc_pct), "soc_highcell_pct": round(self.soc_pct)}, \
            {"bytes_0_3_defective": int.from_bytes(self.last_21[:4], "little")}

    def _v_c2_smoothed_current(self):
        return {"smoothed_current_a": self._cur(), "overcurrent_counter_a": 0, "mean_current_a": self._cur(),
                "overcurrent_counter_b": 0}, None

    def _v_c2_adc_voltages(self):
        vp = sum(self.cells_mv())
        return {"cell_sum_mv": vp, "pack_side_adc_mv": vp if self.drv_b else 0,
                "output_side_adc_mv": round(self.v_out * 1000)}, None

    def _v_c2_learned_capacity(self):
        return {"soh_pct": 100.0, "learned_capacity_1": 7300, "learned_capacity_2": 7300,
                "learned_capacity_3": 7300}, None

    def _v_c2_capacity_counters(self):
        return {"measurements_done": 0, "reference_capacity": 460, "ring_index": 0, "latest_learned_capacity": 0}, None

    def _v_c2_soh_bounds(self):
        return {"soh_upper_pct": 100.0, "soh_lower_pct": 90.0}, None

    def _v_c2_pack_config(self):
        return {"model_index": 0, "type_code": 0, "variant_id": 1, "parallel_cells_guess": 24,
                "series_cells": 14, "capacity_ah": CAPACITY_AH}, None

    def _v_c2_pack_voltage_current(self):
        return {"pack_voltage_v": sum(self.cells_mv()) / 1000.0, "current_a": self._cur()}, None

    def _v_c2_soc_precharge(self):
        return {"soc_pct": round(self.soc_pct), "precharge_threshold_pct": 70, "config_word": 3000}, None

    def _v_c2_capacity_balancing(self):
        rem = CAPACITY_AH * self.soc_pct / 100
        return {"remaining_capacity_ah": round(rem, 2), "missing_capacity_ah": round(CAPACITY_AH - rem, 2),
                "balancing_mask": self.balancing_mask, "rated_capacity_ah": CAPACITY_AH}, None

    def _v_c2_output_voltage(self):
        return {"output_voltage_v": round(self.v_out, 2)}, None

    def _v_c2_cell_stats(self):
        c = self.cells_mv()
        return {"max_cell_number": c.index(max(c)) + 1, "min_cell_number": c.index(min(c)) + 1,
                "mean_cell_mv": round(sum(c) / 14), "spread_mv": max(c) - min(c)}, None

    def _v_c2_temps_1_7(self):
        return {f"temp{n}_c": self.temps_c[n - 1] for n in range(1, 8)}, None

    def _v_c2_temps_8_14(self):
        return {f"temp{n}_c": self.temps_c[n - 1] for n in range(8, 15)}, None

    def _cells(self, lo, hi):
        c = self.cells_mv()
        return {f"cell{n}_mv": c[n - 1] for n in range(lo, hi + 1)}, None

    def _v_c2_cells_1_4(self): return self._cells(1, 4)
    def _v_c2_cells_5_8(self): return self._cells(5, 8)
    def _v_c2_cells_9_12(self): return self._cells(9, 12)
    def _v_c2_cells_13_14(self): return self._cells(13, 14)

    def _v_c2_output_state(self):
        return {"driver_b_on": self.drv_b, "driver_a_on": self.drv_a,
                "input_rb14_low": self.inputs["rb14"], "input_rb12_low": self.enable_asserted,
                "input_rb11_low": self.inputs["rb11"], "input_rb13_low": self.inputs["rb13"],
                "out_driver_b": self.drv_b, "aux_lata4": bool(self.d0 & 0x10), "aux_lata3": bool(self.d0 & 0x40),
                "aux_latd3": bool(self.d0 & 0x04), "board_on_outputs": not self.sleeping,
                "out_driver_a": self.drv_a}, None

    def _v_c2_status_words(self):
        if self.blocking_fault():
            sub = 8
        elif self.phase == "closed":
            sub = 2
        elif self.phase in ("close_b", "precharge", "close_a", "precharge_off"):
            sub = 1
        else:
            sub = 0
        return {}, {"mode_raw": self.mode, "substate": sub, "latched_word": self.latched_word,
                    "live_word": self.live_word, "config_byte": self.config_byte}

    def _v_c2_temp_stats(self):
        hot, cool, mean = self._temp_stats()
        return {"max_temp_c": hot, "min_temp_c": cool, "mean_temp_c": mean}, None

    def _v_c2_constant(self):
        return {}, {"constant_pattern": int.from_bytes(bytes.fromhex("CDE902934A000000"), "little")}

    def _v_c2_current_scale(self):
        return {"current_scale": 1.0, "charge_cell_limit_mv": 4150, "scale_ratio_x100": 100,
                "current_zero_offset_counts": 0}, None

    def _v_c2_monitor_identity(self):
        return {"identity_word": 0}, None

    def _v_c2_log_status(self):
        return {"log_write_pointer": 0, "verify_status": 0, "write_count": 0, "last_event_code": 0}, None

    # -------------------------------------------------------------- scheduler

    def _emit(self, out: list, bus: int, can_id: int, data: bytes) -> None:
        out.append(Frame(self.now_us, bus, can_id, data, "rx"))

    def _tick(self, out: list) -> None:
        now = self.now_us
        if (now // SCHED_US) % (PASS_US // SCHED_US) == 0:
            self._pass()
        while self._outbox:
            self._emit(out, *self._outbox.pop(0))
        if self.sleeping:
            return
        chains = [(CAN1, CAN1_CHAIN_1), (CAN1, CAN1_CHAIN_2), (CAN2, _CAN2_PERIODIC)]
        for bus, chain in chains:
            for can_id in chain:
                spec = SPEC_BY_KEY[(bus, can_id)]
                if now - self.last_sent[(bus, can_id)] < spec.period_ms * 1000:
                    continue
                if spec.gate == "d1_bit0" and not self.d1 & 1:
                    continue
                self.last_sent[(bus, can_id)] = now
                data = self._build(spec.name)
                if can_id == 0x0CFF21F3:
                    self.last_21 = data
                self._emit(out, bus, can_id, data)
                break

    def advance(self, dt_us: int) -> list:
        """Run ``dt_us`` of simulated time and return the frames the pack sent (as :class:`Frame`)."""
        end = self.now_us + dt_us
        out: list = []
        while self._next_tick <= end:
            self.now_us = self._next_tick
            self._next_tick += SCHED_US
            self._tick(out)
        self.now_us = end
        return out


# ---------------------------------------------------------------------------
# Replay and command line
# ---------------------------------------------------------------------------

def replay(frames: Iterable[Frame], *, speed: float = 0.0, directions=("rx", "tx"),
           sleep=time.sleep, clock=time.monotonic) -> Iterator[Frame]:
    """Yield logged frames in order. ``speed`` 1.0 paces by the log timestamps, 0 does not pace."""
    t0_log = None
    t0_wall = clock()
    for f in frames:
        if f.direction not in directions:
            continue
        if speed > 0:
            if t0_log is None:
                t0_log = f.ts_us
            due = (f.ts_us - t0_log) / 1e6 / speed
            wait = due - (clock() - t0_wall)
            if wait > 0:
                sleep(wait)
        yield f


def main(argv: Optional[list] = None, out=None) -> int:
    p = argparse.ArgumentParser(prog="python -m vanguard.simulator", description=__doc__.split("\n\n")[0])
    p.add_argument("--duration", type=float, default=10.0, help="simulated seconds, 0 = until interrupted")
    p.add_argument("--realtime", action="store_true", help="pace output to the wall clock")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--charge", action="store_true", help="simulated host requests the charge path")
    p.add_argument("--no-keepalive", action="store_true", help="simulated host sends no 0x105")
    p.add_argument("--replay", metavar="FILE", help="replay a JSON lines log instead of simulating")
    p.add_argument("--speed", type=float, default=0.0, help="replay pacing, 1.0 = original timing, 0 = unpaced")
    p.add_argument("--rx-only", action="store_true", help="replay only frames logged as rx")
    a = p.parse_args(argv)
    out = out or sys.stdout

    def put(line: str) -> None:
        out.write(line + "\n")
        out.flush()

    if a.replay:
        with open(a.replay, encoding="utf-8") as fh:
            put(format_status(f"replay {a.replay}"))
            for f in replay(read_jsonl(fh), speed=a.speed, directions=("rx",) if a.rx_only else ("rx", "tx")):
                put(format_frame(f))
        return 0

    sim = PackSimulator(seed=a.seed)
    put(format_status("simulator start"))
    keepalive = bytes([0x01 if a.charge else 0x00, 0x11, 0, 0, 0, 0, 0, 0])
    wall0 = time.monotonic()
    try:
        steps = 0
        while a.duration == 0 or sim.now_us < a.duration * 1e6:
            if not a.no_keepalive and steps % 10 == 0:
                sim.receive(CAN1, 0x105, keepalive)
            frames = sim.advance(10_000)
            steps += 1
            for ts, text in sim.drain_status():
                put(format_status(f"{ts} us: {text}"))
            for f in frames:
                put(format_frame(f))
            if a.realtime:
                wait = sim.now_us / 1e6 - (time.monotonic() - wall0)
                if wait > 0:
                    time.sleep(wait)
    except (KeyboardInterrupt, BrokenPipeError):
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
