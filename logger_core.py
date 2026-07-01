"""
VW ECU Logger Core
Standalone logger engine — J2534 / SocketCAN / USB-ISOTP
Modes: 22, 3E, HSL
"""

from datetime import datetime
from lib.connections.connection_setup import connection_setup

import yaml
import threading
import time
import os
import logging
import socket
import sys
import struct
import csv
import json
from math import sqrt

from udsoncan.client import Client
from udsoncan import configs, exceptions

KG_TO_N  = 9.80665
TQ_CONST = 16.3
PI       = 3.14


class LoggerCore:
    def __init__(
        self,
        interface="J2534",
        interface_path=None,
        mode="22",
        log_path="./logs/",
        run_server=True,
        single_csv=False,
        callback=None,
        log_level=logging.INFO,
    ):
        self.interface      = interface
        self.interface_path = interface_path
        self.mode           = mode.upper()
        self.log_path       = log_path
        self.run_server     = run_server
        self.single_csv     = single_csv
        self.callback       = callback   # fn(status=str, data=dict|None)

        self.kill        = False
        self.is_logging  = False
        self.is_key_triggered = False
        self.is_pid_triggered = False

        self.data_stream  = {}
        self._stream_buf  = {}
        self.data_row     = None
        self.log_file     = None
        self.log_prefix   = "Logging_"
        self.log_trigger  = ""
        self.current_time = datetime.now().strftime("%Y%m%d-%H%M%S")
        self._last_ui_push = 0.0   # throttle UI callbacks to ~10fps

        # HP/TQ calc defaults
        self.calc_hp          = 0
        self.gear_ratios      = [2.92, 1.79, 1.14, 0.78, 0.58, 0.46, 0.0]
        self.gear_final       = 4.77
        self.cd               = 0.28
        self.frontal_area     = 2.4
        self.tire_circ        = 0.639 * PI
        self.curb_weight      = 1500.0 * KG_TO_N

        # Activity logger
        self._log = logging.getLogger("VWLogger")
        self._log.setLevel(log_level)
        if not self._log.handlers:
            h = logging.StreamHandler()
            h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
            self._log.addHandler(h)

        self._load_config()
        self._load_params()

    # ── Config ────────────────────────────────────────────────────────────────

    def _load_config(self):
        config_file = os.path.join(self.log_path, "log_config.yaml")
        self._log.info("Config file: " + config_file)

        if self.mode == "22":
            log_type = "22"
        else:
            log_type = "3E"

        self._fps        = 0
        self._param_file = os.path.join("csv", "parameters_" + log_type.lower() + ".csv")

        if not os.path.exists(config_file):
            self._log.warning("No config file found, using defaults")
            return

        try:
            with open(config_file, "r") as f:
                cfg = yaml.safe_load(f)

            if "Log Prefix" in cfg:
                self.log_prefix = str(cfg["Log Prefix"])
            if "Log Trigger" in cfg:
                self.log_trigger = str(cfg["Log Trigger"])

            if "Calculate HP" in cfg:
                hp_cfg = cfg["Calculate HP"]
                t = str(hp_cfg.get("Type", "none")).lower()
                if t == "reported":
                    self.calc_hp = 1
                elif t == "accel":
                    self.calc_hp = 2
                if "Curb Weight"        in hp_cfg: self.curb_weight  = float(hp_cfg["Curb Weight"]) * KG_TO_N
                if "Tire Circumference" in hp_cfg: self.tire_circ    = float(hp_cfg["Tire Circumference"]) * PI
                if "Frontal Area"       in hp_cfg: self.frontal_area = float(hp_cfg["Frontal Area"])
                if "Coefficient Of Drag" in hp_cfg: self.cd          = float(hp_cfg["Coefficient Of Drag"])
                for g in range(1, 8):
                    key = "Gear " + str(g)
                    if key in hp_cfg:
                        self.gear_ratios[g - 1] = float(hp_cfg[key])
                if "Final Gear" in hp_cfg:
                    self.gear_final = float(hp_cfg["Final Gear"])

            mode_key = "Mode" + log_type
            if mode_key in cfg:
                mc = cfg[mode_key]
                if "fps"        in mc: self._fps        = mc["fps"]
                if "param_file" in mc: self._param_file = mc["param_file"]

        except Exception as e:
            self._log.error("Error loading config: " + str(e))

        self._delay = 0.0 if self._fps < 1 else 1.0 / self._fps
        if self.mode == "22" and self.calc_hp == 2:
            self.calc_hp = 1

    def _load_params(self):
        param_file = os.path.join(self.log_path, self._param_file)
        self._log.info("Parameter file: " + param_file)

        self.log_params       = {}   # int index → param dict
        self.assignments      = {}   # name → index
        self.assignment_vals  = {}
        self.csv_header       = "Time"
        self.csv_divider      = "0"

        if not os.path.exists(param_file):
            raise FileNotFoundError("Parameter file not found: " + param_file)

        with open(param_file, "r") as f:
            reader = csv.DictReader(f)
            idx = 0
            for row in reader:
                addr = row["Address"].lstrip("0x").lower()
                self.log_params[idx] = {
                    "Name":     row["Name"],
                    "Address":  row["Address"],
                    "Length":   int(row["Length"]),
                    "Equation": row["Equation"].lower(),
                    "Signed":   row["Signed"].lower() == "true",
                    "ProgMin":  float(row["ProgMin"]),
                    "ProgMax":  float(row["ProgMax"]),
                    "Value":    0.0,
                    "Raw":      0.0,
                    "Virtual":  addr in ("ffff", "ffffffff"),
                }
                assign_to = row.get("Assign To", "").lower().strip()
                if assign_to and assign_to not in ("", "x", "e", "hp", "tq"):
                    if all(c == "_" or c.isalpha() for c in assign_to):
                        self.assignments[assign_to] = idx

                self.csv_header  += "," + row["Name"]
                self.csv_divider += ",0"
                idx += 1

        self._log.info("Loaded %d parameters, %d assignments", idx, len(self.assignments))

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self):
        """Connect and start logging. Blocks until stop() is called."""
        self._notify("Connecting to ECU…")
        self.conn = connection_setup(
            self.interface, txid=0x7E0, rxid=0x7E8,
            interface_path=self.interface_path,
        )
        with Client(self.conn, request_timeout=2, config=configs.default_client_config) as client:
            try:
                self._main(client)
            except exceptions.NegativeResponseException as e:
                self._notify("ECU rejected request: " + e.response.code_name)
            except exceptions.TimeoutException:
                self._notify("Timeout — no response from ECU")
            except Exception as e:
                self._notify("Error: " + str(e))
                raise

    def stop(self):
        self._log.info("Stop requested")
        self.kill = True

    def toggle_key_trigger(self):
        self.is_key_triggered = not self.is_key_triggered

    # ── Internal ──────────────────────────────────────────────────────────────

    def _notify(self, status, data=None):
        if self.callback:
            self.callback(status=status, data=data or self.data_stream)

    def _main(self, client):
        # Setup 3E/HSL parameter list on ECU
        if self.mode != "22":
            prefix = "3E02" if self.mode == "HSL" else "3E32"
            self.memory_offset = 0xB001E700
            param_list = ""
            for p in self.log_params.values():
                if not p["Virtual"]:
                    param_list += "0" + str(p["Length"])[0] + p["Address"].lstrip("0x")
            param_list += "00"
            request = (
                prefix
                + format(self.memory_offset, "08x")
                + format(len(param_list) // 2, "04x")
                + param_list
            )
            self._log.debug("3E setup request: " + request)
            resp = self._send_raw(bytes.fromhex(request))
            if resp.hex()[:2].lower() != "7e":
                raise RuntimeError("Failed to set up 3E list: " + resp.hex())

        # Start polling thread
        poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        poll_thread.start()

        # Start TCP stream thread
        if self.run_server:
            stream_thread = threading.Thread(target=self._stream_server, daemon=True)
            stream_thread.start()

        self._notify("Connected — polling")

        # Just wait for stop() — all work happens in daemon threads
        while not self.kill:
            time.sleep(0.1)

        self._notify("Stopped")

    def _poll_loop(self):
        self.log_file  = None
        next_frame     = time.time()

        while not self.kill:
            now = time.time()
            if now >= next_frame:
                next_frame += self._delay
                if next_frame < now:
                    next_frame = now

                if self.mode == "22":
                    self._get_params_22()
                else:
                    self._get_params_hsl()

                self._set_assignment_values()
                self._calc_tq()

                if self.log_file:
                    self.log_file.flush()

                self._check_logging()
            else:
                time.sleep(0.001)  # yield to GUI thread when waiting for next frame

    # ── Parameter reading ─────────────────────────────────────────────────────

    def _get_params_22(self):
        pos    = 0
        req    = "22"
        for idx, p in self.log_params.items():
            if p["Virtual"]:
                self._set_pid_value(idx, p["Value"])
            else:
                if pos < 8:
                    req += p["Address"].lstrip("0x")
                    pos += 1
                else:
                    self._req_params_22(req)
                    pos = 1
                    req = "22" + p["Address"].lstrip("0x")
        if pos > 0:
            self._req_params_22(req)

        row = self._clear_stream()
        for idx, p in self.log_params.items():
            self._stream_buf[idx] = {"Name": p["Name"], "Value": str(p["Value"])}
            row += "," + str(p["Value"])
        self._write_csv(row)

    def _req_params_22(self, req_hex):
        result = self._send_raw(bytes.fromhex(req_hex)).hex()
        if not result.startswith("62"):
            return
        result = result[2:]
        while result:
            addr   = result[:4]
            result = result[4:]
            pid    = self._get_param_by_address(addr)
            if pid is None:
                break
            length  = self.log_params[pid]["Length"] * 2
            val_hex = result[:length]
            result  = result[length:]
            raw     = int.from_bytes(bytearray.fromhex(val_hex), "big",
                                     signed=self.log_params[pid]["Signed"])
            self._set_pid_value(pid, raw)

    def _get_params_hsl(self):
        prefix = "3e04" if self.mode == "HSL" else "3e33"
        suffix = "FFFF" if self.mode == "HSL" else ""
        req    = prefix + format(self.memory_offset, "08x") + suffix
        result = self._send_raw(bytes.fromhex(req))
        if result is None:
            return
        result = result.hex()[2:]  # strip response byte

        row = self._clear_stream()
        for idx, p in self.log_params.items():
            if not result:
                break
            if p["Virtual"]:
                self._set_pid_value(idx, p["Value"])
            else:
                nbytes  = p["Length"] * 2
                val_hex = result[:nbytes]
                result  = result[nbytes:]
                raw     = int.from_bytes(bytearray.fromhex(val_hex), "little",
                                         signed=p["Signed"])
                if p["Length"] == 4:
                    raw = struct.unpack("f", int(raw).to_bytes(4, "little"))[0]
                self._set_pid_value(idx, raw)

            self._stream_buf[idx] = {"Name": p["Name"], "Value": str(p["Value"])}
            row += "," + str(p["Value"])
        self._write_csv(row)

    # ── Value helpers ─────────────────────────────────────────────────────────

    def _get_param_by_address(self, addr):
        for idx, p in self.log_params.items():
            if addr == p["Address"].lstrip("0x"):
                return idx
        return None

    def _set_pid_value(self, idx, raw):
        try:
            self.assignment_vals["x"] = raw
            self.log_params[idx]["Raw"] = raw
            self.log_params[idx]["Value"] = round(
                eval(self.log_params[idx]["Equation"], self.assignment_vals), 2
            )
        except Exception:
            self.log_params[idx]["Value"] = 0.0

    def _set_assignment_values(self):
        for name, idx in self.assignments.items():
            self.assignment_vals[name] = self.log_params[idx]["Value"]

    def _calc_tq(self):
        if "rpm" not in self.assignments:
            return
        rpm = self.log_params[self.assignments["rpm"]]["Raw"]

        if self.calc_hp == 1:
            try:
                divisor = 10.0 if self.mode == "22" else 32.0
                tq = self.log_params[self.assignments["tq_rep"]]["Raw"] / divisor
                self.assignment_vals["tq"] = tq
                self.assignment_vals["hp"] = tq * rpm / 7127.0
            except Exception:
                self.assignment_vals["tq"] = 0.0
                self.assignment_vals["hp"] = 0.0

        elif self.calc_hp == 2:
            try:
                gear = int(self.log_params[self.assignments["gear"]]["Raw"])
                if gear in range(1, 8):
                    ms2   = sqrt((self.log_params[self.assignments["accel_long"]]["Raw"] - 512.0) / 32.0)
                    ratio = sqrt(self.gear_ratios[gear - 1] * self.gear_final)
                    vel   = self.log_params[self.assignments["speed"]]["Raw"] / 100.0
                    drag_air  = vel**3 * 0.00001564 * self.cd * self.frontal_area
                    drag_roll = vel * self.curb_weight * 0.00000464
                    drag      = (drag_air + drag_roll) / rpm * 7127.0
                    tq = self.curb_weight * ms2 / ratio / self.tire_circ / TQ_CONST + drag
                    self.assignment_vals["tq"] = tq
                    self.assignment_vals["hp"] = tq * rpm / 7127.0
            except Exception:
                self.assignment_vals["tq"] = 0.0
                self.assignment_vals["hp"] = 0.0

    # ── Logging trigger ───────────────────────────────────────────────────────

    def _check_logging(self):
        try:
            met = False
            for or_clause in self.log_trigger.split("|"):
                if met:
                    break
                all_met = True
                for expr in or_clause.split("&"):
                    expr = expr.strip()
                    if len(expr) < 3:
                        continue
                    for op in (">", "<", "="):
                        pos = expr.find(op)
                        if pos != -1:
                            break
                    name  = expr[:pos].strip()
                    cmp   = expr[pos]
                    val   = float(expr[pos + 1:].strip())
                    if name not in self.assignments:
                        all_met = False
                        break
                    current = self.log_params[self.assignments[name]]["Value"]
                    if cmp == ">" and current <= val:
                        all_met = False
                    elif cmp == "<" and current >= val:
                        all_met = False
                    elif cmp == "=" and abs(current - val) > 0.15:
                        all_met = False
                if all_met:
                    met = True
            self.is_pid_triggered = met
        except Exception:
            self.is_pid_triggered = False

        triggered = self.is_key_triggered or self.is_pid_triggered
        if self.is_logging and not triggered:
            self.is_logging = False
            self.log_file   = None
            self._notify("Logging stopped")
        elif not self.is_logging and triggered:
            self.is_logging = True
            self._notify("Logging started")

    # ── CSV ───────────────────────────────────────────────────────────────────

    def _clear_stream(self):
        self._stream_buf = {
            "Time":      {"Name": "Time",      "Value": str(datetime.now().time())},
            "isLogging": {"Name": "isLogging", "Value": str(self.is_logging)},
        }
        return str(datetime.now().time())

    def _write_csv(self, row):
        self.data_stream = self._stream_buf
        self.data_row    = row

        now = time.time()
        if now - self._last_ui_push >= 0.1:
            self._last_ui_push = now
            self._notify("Connected — polling")

        if not self.is_logging:
            return

        if self.log_file is None:
            if self.single_csv:
                fname = os.path.join(self.log_path, self.log_prefix + self.current_time + ".csv")
            else:
                fname = os.path.join(self.log_path, self.log_prefix
                                     + datetime.now().strftime("%Y%m%d-%H%M%S") + ".csv")
            self._log.info("Opening log file: " + fname)
            self.log_file = open(fname, "a", newline="")
            if not self.single_csv:
                self.log_file.write(self.csv_header + "\n")

        self.log_file.write(row + "\n")

    # ── TCP stream (for logger_viewer.html) ───────────────────────────────────

    def _stream_server(self):
        import select
        HOST, PORT = "127.0.0.1", 65432
        self._log.info("Starting TCP stream on %s:%d", HOST, PORT)
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind((HOST, PORT))
            srv.listen()
            srv.setblocking(False)
        except Exception as e:
            self._log.error("Stream server bind failed: %s", e)
            return

        while not self.kill:
            ready, _, _ = select.select([srv], [], [], 0.5)
            if not ready:
                continue
            try:
                conn, addr = srv.accept()
            except Exception:
                continue
            self._log.info("Stream client connected: %s", addr)
            try:
                conn.sendall(
                    b"HTTP/1.1 200 OK\n"
                    b"Content-Type: stream\n"
                    b"Access-Control-Allow-Origin: *\n\n"
                )
                while not self.kill:
                    conn.sendall((json.dumps(self.data_stream) + "\n").encode())
                    time.sleep(0.1)
            except Exception:
                pass
            finally:
                conn.close()
        srv.close()

    # ── Raw send ──────────────────────────────────────────────────────────────

    def _send_raw(self, data):
        result = None
        while result is None:
            self.conn.send(data)
            result = self.conn.wait_frame(timeout=4)
            if result is None:
                self._log.warning("No response from ECU, retrying…")
        return result
