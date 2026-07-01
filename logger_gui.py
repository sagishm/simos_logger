"""
VW ECU Logger — Standalone wxPython GUI
"""

import wx
import threading
import os
import sys
import json

from logger_core import LoggerCore

DEFAULT_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
PREFS_FILE       = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gauge_prefs.json")

# ── Colour constants ──────────────────────────────────────────────────────────
CLR_BG      = wx.Colour(15,  17,  23)
CLR_SURFACE = wx.Colour(26,  29,  39)
CLR_BORDER  = wx.Colour(42,  45,  58)
CLR_TEXT    = wx.Colour(226, 232, 240)
CLR_MUTED   = wx.Colour(100, 116, 139)
CLR_GREEN   = wx.Colour(34,  197, 94)
CLR_RED     = wx.Colour(239, 68,  68)
CLR_ACCENT  = wx.Colour(79,  156, 249)
CLR_LOG_BG  = wx.Colour(40,  20,  20)

CARD_W, CARD_H, CARD_PAD = 150, 90, 8

COLOR_PRESETS = [
    ("Red",    wx.Colour(80, 20, 20)),
    ("Orange", wx.Colour(80, 50, 10)),
    ("Green",  wx.Colour(10, 60, 30)),
    ("Blue",   wx.Colour(10, 40, 80)),
    ("Purple", wx.Colour(50, 20, 80)),
    ("Clear",  None),
]


# ── Prefs ─────────────────────────────────────────────────────────────────────

def _load_prefs():
    try:
        with open(PREFS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_prefs(prefs):
    try:
        with open(PREFS_FILE, "w") as f:
            json.dump(prefs, f, indent=2)
    except Exception:
        pass


# ── Interface scanner ─────────────────────────────────────────────────────────

def _scan_j2534_registry():
    devices = []
    if sys.platform != "win32":
        return devices
    try:
        import winreg
    except ImportError:
        return devices
    hive_paths = [r"Software\PassThruSupport.04.04",
                  r"Software\Wow6432Node\PassThruSupport.04.04"]
    seen_dlls = set()
    for path in hive_paths:
        try:
            base = winreg.OpenKeyEx(winreg.HKEY_LOCAL_MACHINE, path)
        except OSError:
            continue
        for i in range(winreg.QueryInfoKey(base)[0]):
            try:
                key  = winreg.OpenKeyEx(base, winreg.EnumKey(base, i))
                name = winreg.QueryValueEx(key, "Name")[0]
                dll  = winreg.QueryValueEx(key, "FunctionLibrary")[0]
                if dll not in seen_dlls:
                    seen_dlls.add(dll)
                    devices.append((name, dll))
            except OSError:
                pass
    return devices

def scan_interfaces():
    results = []
    for name, dll in _scan_j2534_registry():
        results.append((f"J2534 — {name}", "J2534", dll))
    if sys.platform == "linux":
        results.append(("SocketCAN — can0", "SocketCAN", "can0"))
    try:
        import serial.tools.list_ports
        for port in serial.tools.list_ports.comports():
            results.append((f"USB-ISOTP — {port.name} : {port.description}", "USBISOTP", port.device))
    except Exception:
        pass
    return results


# ── Param selector dialog ─────────────────────────────────────────────────────

class ParamSelectorDialog(wx.Dialog):
    def __init__(self, parent, all_params, selected):
        super().__init__(parent, title="Select Parameters", size=(340, 500),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.SetBackgroundColour(CLR_BG)
        self._all_params = all_params

        vbox = wx.BoxSizer(wx.VERTICAL)
        self._search = wx.TextCtrl(self, style=wx.TE_PROCESS_ENTER)
        self._search.SetBackgroundColour(CLR_SURFACE)
        self._search.SetForegroundColour(CLR_TEXT)
        self._search.SetHint("Search…")
        vbox.Add(self._search, 0, wx.EXPAND | wx.ALL, 8)

        self._clb = wx.CheckListBox(self, choices=all_params)
        self._clb.SetBackgroundColour(CLR_SURFACE)
        self._clb.SetForegroundColour(CLR_TEXT)
        for i, name in enumerate(all_params):
            if name in selected:
                self._clb.Check(i, True)
        vbox.Add(self._clb, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        all_btn  = wx.Button(self, label="All",  size=(60, -1))
        none_btn = wx.Button(self, label="None", size=(60, -1))
        all_btn.Bind(wx.EVT_BUTTON,  lambda _: self._check_all(True))
        none_btn.Bind(wx.EVT_BUTTON, lambda _: self._check_all(False))
        btn_row.Add(all_btn, 0, wx.RIGHT, 4)
        btn_row.Add(none_btn, 0)
        vbox.Add(btn_row, 0, wx.LEFT | wx.BOTTOM, 8)
        vbox.Add(self.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.EXPAND | wx.ALL, 8)
        self.SetSizer(vbox)
        self._search.Bind(wx.EVT_TEXT, self._on_search)

    def _check_all(self, state):
        for i in range(self._clb.GetCount()):
            self._clb.Check(i, state)

    def _on_search(self, _):
        q   = self._search.GetValue().lower()
        sel = self.get_selected()
        self._clb.Clear()
        filtered = [p for p in self._all_params if q in p.lower()]
        self._clb.InsertItems(filtered, 0)
        for i, name in enumerate(filtered):
            if name in sel:
                self._clb.Check(i, True)

    def get_selected(self):
        return {self._clb.GetString(i)
                for i in range(self._clb.GetCount())
                if self._clb.IsChecked(i)}


# ── GaugeCanvas — single panel draws ALL cards ───────────────────────────────

class GaugeCanvas(wx.Panel):
    """
    Draws every gauge card in one OnPaint call.
    Plain Panel — no scroll machinery overhead.
    """

    def __init__(self, parent, on_prefs_changed):
        super().__init__(parent, style=wx.BORDER_NONE)
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)

        self._on_prefs_changed = on_prefs_changed  # callback(order)

        # per-gauge state
        self._order      = []
        self._values     = {}
        self._colors     = {}
        self._upper      = {}
        self._name_extents = {}   # name → (tw, th) for label
        self._is_logging = False
        self._drag_name  = None        # name of card being dragged
        self._drop_idx   = None        # drop target index
        self._rects      = {}          # name → wx.Rect (updated each paint)

        self._font_name   = None        # created lazily
        self._font_value  = None
        self._brush_cache = {}          # wx.Colour key → wx.Brush

        self.Bind(wx.EVT_PAINT,       self._on_paint)
        self.Bind(wx.EVT_SIZE,        self._on_size)
        self.Bind(wx.EVT_LEFT_DOWN,   self._on_left_down)
        self.Bind(wx.EVT_LEFT_UP,     self._on_left_up)
        self.Bind(wx.EVT_MOTION,      self._on_motion)
        self.Bind(wx.EVT_RIGHT_DOWN,  self._on_right_click)

    # ── Public API ────────────────────────────────────────────────────────────

    def set_params(self, order, colors):
        self._order      = list(order)
        self._colors     = {n: wx.Colour(*c) if c else None for n, c in colors.items()}
        self._values     = {n: self._values.get(n, "—") for n in order}
        self._upper      = {n: n.upper() for n in order}   # cache uppercased labels
        self._brush_cache = {}
        self._recalc_virtual_size()
        self.Refresh()

    def update(self, by_name, is_logging):
        changed = is_logging != self._is_logging
        self._is_logging = is_logging
        for name in self._order:
            new_val = by_name.get(name)
            if new_val is not None and new_val != self._values.get(name):
                self._values[name] = new_val
                changed = True
        if changed:
            self.Refresh()

    def get_order(self):
        return list(self._order)

    def get_colors(self):
        return {n: (c.Red(), c.Green(), c.Blue()) for n, c in self._colors.items() if c}

    def set_mark_color(self, name, color):
        self._colors[name] = color
        self.Refresh()
        self.Update()

    # ── Layout helpers ────────────────────────────────────────────────────────

    def _cols(self):
        w = self.GetClientSize().width
        return max(1, (w + CARD_PAD) // (CARD_W + CARD_PAD))

    def _recalc_virtual_size(self):
        pass  # no scrolling

    def _card_rect(self, idx):
        cols = self._cols()
        row, col = divmod(idx, cols)
        x = CARD_PAD + col * (CARD_W + CARD_PAD)
        y = CARD_PAD + row * (CARD_H + CARD_PAD)
        return wx.Rect(x, y, CARD_W, CARD_H)

    def _idx_at(self, pos):
        for i in range(len(self._order)):
            if self._card_rect(i).Contains(pos):
                return i
        return None

    def _screen_to_canvas(self, screen_pt):
        return self.ScreenToClient(screen_pt)

    # ── Paint ─────────────────────────────────────────────────────────────────

    def _on_paint(self, _):
        import time as _t; _t0 = _t.perf_counter()
        if self._font_name is None:
            self._font_name  = wx.Font(7,  wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)
            self._font_value = wx.Font(16, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD)
            self._brush_bg   = wx.Brush(CLR_BG)
            self._brush_surf = wx.Brush(CLR_SURFACE)
            self._brush_log  = wx.Brush(CLR_LOG_BG)
            self._brush_bdr  = wx.Brush(CLR_BORDER)
            self._brush_acc  = wx.Brush(CLR_ACCENT)
            self._pen_none   = wx.TRANSPARENT_PEN

        dc = wx.PaintDC(self)
        _t1 = _t.perf_counter()
        dc.SetBackground(self._brush_bg)
        dc.Clear()
        _t2 = _t.perf_counter()
        dc.SetPen(self._pen_none)

        for i, name in enumerate(self._order):
            r = self._card_rect(i)

            if self._is_logging:
                dc.SetBrush(self._brush_log)
            elif self._colors.get(name):
                clr = self._colors[name]
                key = clr.GetRGB()
                if key not in self._brush_cache:
                    self._brush_cache[key] = wx.Brush(clr)
                dc.SetBrush(self._brush_cache[key])
            else:
                dc.SetBrush(self._brush_surf)
            dc.DrawRectangle(r.x, r.y, CARD_W, CARD_H)

            dc.SetBrush(self._brush_acc if i == self._drop_idx else self._brush_bdr)
            dc.DrawRectangle(r.x, r.y, CARD_W, 5)

            dc.SetFont(self._font_name)
            dc.SetTextForeground(CLR_MUTED)
            label = self._upper.get(name, name)
            tw, _ = dc.GetTextExtent(label)
            dc.DrawText(label, r.x + (CARD_W - tw) // 2, r.y + 10)

            dc.SetFont(self._font_value)
            dc.SetTextForeground(CLR_TEXT)
            val = self._values.get(name, "—")
            tw, _ = dc.GetTextExtent(val)
            dc.DrawText(val, r.x + (CARD_W - tw) // 2, r.y + 28)

        _t3 = _t.perf_counter()
        print(f"paint {len(self._order)} cards: total={(_t3-_t0)*1000:.1f}ms  dc_create={(_t1-_t0)*1000:.1f}ms  clear={(_t2-_t1)*1000:.1f}ms  draw={(_t3-_t2)*1000:.1f}ms", flush=True)

    def _on_size(self, _):
        self._recalc_virtual_size()
        self.Refresh()

    # ── Mouse: drag-to-reorder ────────────────────────────────────────────────

    def _on_left_down(self, event):
        pos = self._screen_to_canvas(self.ClientToScreen(event.GetPosition()))
        idx = self._idx_at(pos)
        if idx is None:
            return
        r = self._card_rect(idx)
        # only start drag from the top handle strip
        if pos.y - r.y <= 10:
            self._drag_name = self._order[idx]
            self.CaptureMouse()

    def _on_left_up(self, event):
        if self._drag_name is None:
            return
        if self.HasCapture():
            self.ReleaseMouse()
        pos = self._screen_to_canvas(self.ClientToScreen(event.GetPosition()))
        target = self._idx_at(pos)
        if target is not None:
            src = self._order.index(self._drag_name)
            if src != target:
                self._order.insert(target, self._order.pop(src))
                self._on_prefs_changed(self._order)
        self._drag_name = None
        self._drop_idx  = None
        self.Refresh()
        self.Update()

    def _on_motion(self, event):
        if self._drag_name is None or not event.Dragging():
            return
        pos = self._screen_to_canvas(self.ClientToScreen(event.GetPosition()))
        idx = self._idx_at(pos)
        if idx != self._drop_idx:
            self._drop_idx = idx
            self.Refresh()
            self.Update()

    # ── Mouse: right-click color menu ────────────────────────────────────────

    def _on_right_click(self, event):
        pos = self._screen_to_canvas(self.ClientToScreen(event.GetPosition()))
        idx = self._idx_at(pos)
        if idx is None:
            return
        name = self._order[idx]
        menu = wx.Menu()
        for label, color in COLOR_PRESETS:
            item = menu.Append(wx.ID_ANY, label)
            self.Bind(wx.EVT_MENU, lambda e, n=name, c=color: self._pick_color(n, c), item)
        self.PopupMenu(menu)
        menu.Destroy()

    def _pick_color(self, name, color):
        self._colors[name] = color
        self.Refresh()
        self._on_prefs_changed(self._order)


# ── Main panel ────────────────────────────────────────────────────────────────

class MainPanel(wx.Panel):
    def __init__(self, parent):
        super().__init__(parent)
        self.SetBackgroundColour(CLR_BG)

        self._logger      = None
        self._thread      = None
        self._iface_list  = []
        self._last_status = ""
        self._all_params  = []
        self._selected    = set()
        self._prefs       = _load_prefs()
        self._initial_apply_pending = False

        self._build_ui()
        self._do_scan()

        self._ui_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_ui_timer, self._ui_timer)

    def _build_ui(self):
        root = wx.BoxSizer(wx.VERTICAL)

        # ── Controls bar ─────────────────────────────────────────────────────
        ctrl_panel = wx.Panel(self)
        ctrl_panel.SetBackgroundColour(CLR_SURFACE)
        ctrl = wx.BoxSizer(wx.VERTICAL)

        row1 = wx.BoxSizer(wx.HORIZONTAL)
        row1.Add(wx.StaticText(ctrl_panel, label="Device:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 10)
        self._device_choice = wx.Choice(ctrl_panel, size=(380, -1))
        row1.Add(self._device_choice, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 6)
        self._scan_btn = wx.Button(ctrl_panel, label="Scan", size=(52, -1))
        self._scan_btn.Bind(wx.EVT_BUTTON, self._on_scan)
        row1.Add(self._scan_btn, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 6)
        row1.Add(wx.StaticText(ctrl_panel, label="Mode:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 16)
        self._mode_choice = wx.Choice(ctrl_panel, choices=["22", "3E", "HSL"])
        self._mode_choice.SetSelection(0)
        row1.Add(self._mode_choice, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 6)
        self._stream_chk = wx.CheckBox(ctrl_panel, label="Stream :65432")
        self._stream_chk.SetValue(False)
        row1.Add(self._stream_chk, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 16)
        row1.AddStretchSpacer()
        self._connect_btn = wx.Button(ctrl_panel, label="Connect", size=(80, -1))
        self._stop_btn    = wx.Button(ctrl_panel, label="Stop",    size=(80, -1))
        self._stop_btn.Enable(False)
        self._connect_btn.SetBackgroundColour(CLR_ACCENT)
        self._connect_btn.SetForegroundColour(wx.WHITE)
        self._connect_btn.Bind(wx.EVT_BUTTON, self._on_connect)
        self._stop_btn.Bind(wx.EVT_BUTTON, self._on_stop)
        row1.Add(self._connect_btn, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        row1.Add(self._stop_btn,    0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)
        ctrl.Add(row1, 0, wx.EXPAND | wx.TOP | wx.BOTTOM, 6)

        row2 = wx.BoxSizer(wx.HORIZONTAL)
        row2.Add(wx.StaticText(ctrl_panel, label="Log path:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 10)
        self._path_text = wx.TextCtrl(ctrl_panel, value=DEFAULT_LOG_PATH, size=(340, -1))
        self._path_btn  = wx.Button(ctrl_panel, label="…", size=(26, -1))
        self._path_btn.Bind(wx.EVT_BUTTON, self._browse_path)
        row2.Add(self._path_text, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 6)
        row2.Add(self._path_btn,  0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 2)
        ctrl.Add(row2, 0, wx.EXPAND | wx.BOTTOM, 6)

        ctrl_panel.SetSizer(ctrl)
        for child in ctrl_panel.GetChildren():
            child.SetBackgroundColour(CLR_SURFACE)
            child.SetForegroundColour(CLR_TEXT)
        root.Add(ctrl_panel, 0, wx.EXPAND)

        # ── Status bar ────────────────────────────────────────────────────────
        status_panel = wx.Panel(self)
        status_panel.SetBackgroundColour(CLR_BG)
        ss = wx.BoxSizer(wx.HORIZONTAL)
        self._status_dot   = wx.StaticText(status_panel, label="●")
        self._status_text  = wx.StaticText(status_panel, label="Disconnected")
        self._logging_text = wx.StaticText(status_panel, label="")
        self._status_dot.SetForegroundColour(CLR_MUTED)
        self._status_text.SetForegroundColour(CLR_MUTED)
        self._logging_text.SetForegroundColour(CLR_RED)
        self._logging_text.SetFont(wx.Font(9, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        self._log_toggle_btn    = wx.Button(status_panel, label="Toggle Log",    size=(90, -1))
        self._select_params_btn = wx.Button(status_panel, label="Select Params", size=(100, -1))
        self._log_toggle_btn.Enable(False)
        self._select_params_btn.Enable(False)
        self._log_toggle_btn.Bind(wx.EVT_BUTTON,    self._on_toggle_log)
        self._select_params_btn.Bind(wx.EVT_BUTTON, self._on_select_params)
        ss.Add(self._status_dot,        0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT,  10)
        ss.Add(self._status_text,       0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT,   6)
        ss.Add(self._logging_text,      0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT,  16)
        ss.Add(self._log_toggle_btn,    0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT,  20)
        ss.Add(self._select_params_btn, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT,  10)
        status_panel.SetSizer(ss)
        for child in status_panel.GetChildren():
            child.SetBackgroundColour(CLR_BG)
            child.SetForegroundColour(CLR_TEXT)
        root.Add(status_panel, 0, wx.EXPAND | wx.TOP, 6)

        # ── Gauge canvas ──────────────────────────────────────────────────────
        self._canvas = GaugeCanvas(self, self._save_prefs)
        root.Add(self._canvas, 1, wx.EXPAND | wx.ALL, 10)

        self.SetSizer(root)

    # ── Scan ──────────────────────────────────────────────────────────────────

    def _do_scan(self):
        self._iface_list = scan_interfaces()
        self._device_choice.Clear()
        if self._iface_list:
            for label, _, _ in self._iface_list:
                self._device_choice.Append(label)
            self._device_choice.SetSelection(0)
        else:
            self._device_choice.Append("No devices found — click Scan")
        self.Layout()

    def _on_scan(self, _):
        self._scan_btn.SetLabel("…")
        self._scan_btn.Enable(False)
        def _scan():
            interfaces = scan_interfaces()
            wx.CallAfter(self._apply_scan, interfaces)
        threading.Thread(target=_scan, daemon=True).start()

    def _apply_scan(self, interfaces):
        self._iface_list = interfaces
        self._device_choice.Clear()
        if interfaces:
            for label, _, _ in interfaces:
                self._device_choice.Append(label)
            self._device_choice.SetSelection(0)
        else:
            self._device_choice.Append("No devices found")
        self._scan_btn.SetLabel("Scan")
        self._scan_btn.Enable(True)
        self.Layout()

    # ── Connect / stop ────────────────────────────────────────────────────────

    def _browse_path(self, _):
        dlg = wx.DirDialog(self, "Select log folder", style=wx.DD_DEFAULT_STYLE)
        if dlg.ShowModal() == wx.ID_OK:
            self._path_text.SetValue(dlg.GetPath())
        dlg.Destroy()

    def _on_connect(self, _):
        idx = self._device_choice.GetSelection()
        if idx < 0 or idx >= len(self._iface_list):
            wx.MessageBox("Please select a device first.", "No device", wx.OK | wx.ICON_WARNING)
            return
        _, iface_type, iface_path = self._iface_list[idx]
        log_path = self._path_text.GetValue()
        os.makedirs(log_path, exist_ok=True)
        self._logger = LoggerCore(
            interface=iface_type,
            interface_path=iface_path,
            mode=self._mode_choice.GetStringSelection(),
            log_path=log_path + os.sep,
            run_server=self._stream_chk.GetValue(),
        )
        self._connect_btn.Enable(False)
        self._stop_btn.Enable(True)
        self._log_toggle_btn.Enable(True)
        self._select_params_btn.Enable(True)
        self._set_status("Connecting…", CLR_MUTED)
        self._thread = threading.Thread(target=self._run_logger, daemon=True)
        self._thread.start()
        self._ui_timer.Start(100)

    def _run_logger(self):
        try:
            self._logger.start()
        except Exception as e:
            wx.CallAfter(self._set_status, "Error: " + str(e), CLR_RED)
            wx.CallAfter(self._reset_buttons)

    def _on_stop(self, _):
        self._ui_timer.Stop()
        if self._logger:
            self._logger.stop()
        self._reset_buttons()
        self._set_status("Disconnected", CLR_MUTED)
        self._logging_text.SetLabel("")

    def _on_toggle_log(self, _):
        if self._logger:
            self._logger.toggle_key_trigger()

    def _reset_buttons(self):
        self._connect_btn.Enable(True)
        self._stop_btn.Enable(False)
        self._log_toggle_btn.Enable(False)
        self._select_params_btn.Enable(False)

    # ── Param selection ───────────────────────────────────────────────────────

    def _on_select_params(self, _):
        if not self._all_params:
            wx.MessageBox("No parameters received yet.", "No data", wx.OK | wx.ICON_INFORMATION)
            return
        dlg = ParamSelectorDialog(self, sorted(self._all_params), self._selected)
        if dlg.ShowModal() == wx.ID_OK:
            self._selected = dlg.get_selected()
            self._prefs["selected"] = list(self._selected)
            self._prefs["order"] = [n for n in self._prefs.get("order", []) if n in self._selected]
            self._apply_selection()
            _save_prefs(self._prefs)
        dlg.Destroy()

    def _apply_selection(self):
        saved_order = self._prefs.get("order", [])
        ordered = [n for n in saved_order if n in self._selected]
        for n in self._selected:
            if n not in ordered:
                ordered.append(n)
        self._canvas.set_params(ordered, self._prefs.get("colors", {}))

    def _initial_apply(self):
        saved = set(self._prefs.get("selected", []))
        self._selected = (saved & set(self._all_params)) if saved else set(self._all_params)
        self._apply_selection()

    # ── Timer UI update ───────────────────────────────────────────────────────

    def _on_ui_timer(self, _):
        if self._logger is None:
            return
        if self._logger.kill:
            self._ui_timer.Stop()
            self._set_status("Stopped", CLR_MUTED)
            self._logging_text.SetLabel("")
            self._reset_buttons()
            return

        data = dict(self._logger.data_stream)

        by_name   = {}
        new_found = False
        for v in data.values():
            if not isinstance(v, dict):
                continue
            name = v.get("Name", "")
            val  = v.get("Value", "—")
            if not name or name == "Time":
                continue
            by_name[name] = val
            if name != "isLogging" and name not in self._all_params:
                self._all_params.append(name)
                new_found = True

        if new_found and not self._selected and not self._initial_apply_pending:
            self._initial_apply_pending = True
            wx.CallLater(500, self._initial_apply)

        is_logging = by_name.get("isLogging", "False") == "True"
        self._set_status("Connected — polling", CLR_GREEN)
        log_label = "● LOGGING" if is_logging else ""
        if log_label != self._logging_text.GetLabel():
            self._logging_text.SetLabel(log_label)
        self._canvas.update(by_name, is_logging)

    # ── Prefs save ────────────────────────────────────────────────────────────

    def _save_prefs(self, order):
        self._prefs["order"]    = order
        self._prefs["selected"] = list(self._selected)
        self._prefs["colors"]   = self._canvas.get_colors()
        _save_prefs(self._prefs)

    def _set_status(self, text, color):
        if text == self._last_status:
            return
        self._last_status = text
        self._status_dot.SetForegroundColour(color)
        self._status_text.SetLabel(text)
        self._status_text.SetForegroundColour(color)
        self.Layout()


# ── Frame ─────────────────────────────────────────────────────────────────────

class LoggerFrame(wx.Frame):
    def __init__(self):
        super().__init__(None, title="VW ECU Logger", size=(1100, 660))
        self.SetBackgroundColour(CLR_BG)
        panel = MainPanel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(panel, 1, wx.EXPAND)
        self.SetSizer(sizer)
        self.Centre()
        self.Show()


def main():
    app = wx.App(False)
    LoggerFrame()
    app.MainLoop()


if __name__ == "__main__":
    main()
