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
CLR_DRAG_HL = wx.Colour(79,  156, 249, 60)   # drop-target highlight


# ── Prefs (selected params, order, colors) ────────────────────────────────────

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

    hive_paths = [
        r"Software\PassThruSupport.04.04",
        r"Software\Wow6432Node\PassThruSupport.04.04",
    ]
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

        # search box
        self._search = wx.TextCtrl(self, style=wx.TE_PROCESS_ENTER)
        self._search.SetBackgroundColour(CLR_SURFACE)
        self._search.SetForegroundColour(CLR_TEXT)
        self._search.SetHint("Search…")
        vbox.Add(self._search, 0, wx.EXPAND | wx.ALL, 8)

        # checklist
        self._clb = wx.CheckListBox(self, choices=all_params)
        self._clb.SetBackgroundColour(CLR_SURFACE)
        self._clb.SetForegroundColour(CLR_TEXT)
        for i, name in enumerate(all_params):
            if name in selected:
                self._clb.Check(i, True)
        vbox.Add(self._clb, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        # select all / none
        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        all_btn  = wx.Button(self, label="All",  size=(60, -1))
        none_btn = wx.Button(self, label="None", size=(60, -1))
        all_btn.Bind(wx.EVT_BUTTON,  lambda _: self._check_all(True))
        none_btn.Bind(wx.EVT_BUTTON, lambda _: self._check_all(False))
        btn_row.Add(all_btn,  0, wx.RIGHT, 4)
        btn_row.Add(none_btn, 0)
        vbox.Add(btn_row, 0, wx.LEFT | wx.BOTTOM, 8)

        # OK / Cancel — parented to dialog, added to dialog sizer
        vbox.Add(self.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.EXPAND | wx.ALL, 8)

        self.SetSizer(vbox)
        self._search.Bind(wx.EVT_TEXT, self._on_search)

    def _check_all(self, state):
        for i in range(self._clb.GetCount()):
            self._clb.Check(i, state)

    def _on_search(self, _):
        q = self._search.GetValue().lower()
        sel = self.get_selected()
        self._clb.Clear()
        filtered = [p for p in self._all_params if q in p.lower()]
        self._clb.InsertItems(filtered, 0)
        for i, name in enumerate(filtered):
            if name in sel:
                self._clb.Check(i, True)

    def get_selected(self):
        result = set()
        for i in range(self._clb.GetCount()):
            if self._clb.IsChecked(i):
                result.add(self._clb.GetString(i))
        return result


# ── Gauge card ────────────────────────────────────────────────────────────────

class GaugeCard(wx.Panel):
    COLOR_PRESETS = [
        ("Red",    (80, 20, 20)),
        ("Orange", (80, 50, 10)),
        ("Green",  (10, 60, 30)),
        ("Blue",   (10, 40, 80)),
        ("Purple", (50, 20, 80)),
        ("Clear",  None),
    ]

    _FONT_NAME  = None
    _FONT_VALUE = None

    def __init__(self, parent, name, unit=""):
        super().__init__(parent, size=(140, 86), style=wx.BORDER_NONE)
        self.name        = name
        self._unit       = unit
        self._value      = "—"
        self._mark_color = None
        self._bg         = CLR_SURFACE
        self._drop_target = False
        self.SetMinSize((140, 86))
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)

        self.Bind(wx.EVT_PAINT,      self._on_paint)
        self.Bind(wx.EVT_RIGHT_DOWN, self._on_right_click)

    # ── Drawing ───────────────────────────────────────────────────────────────

    def _on_paint(self, _):
        if GaugeCard._FONT_NAME is None:
            GaugeCard._FONT_NAME  = wx.Font(7,  wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)
            GaugeCard._FONT_VALUE = wx.Font(18, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD)
        dc = wx.AutoBufferedPaintDC(self)   # double-buffered, no flicker
        w, h = self.GetSize()

        # background
        dc.SetBackground(wx.Brush(self._bg))
        dc.Clear()

        # drag handle strip at top — highlight blue when drop target
        dc.SetPen(wx.TRANSPARENT_PEN)
        dc.SetBrush(wx.Brush(CLR_ACCENT if self._drop_target else CLR_BORDER))
        dc.DrawRectangle(0, 0, w, 5)

        # name
        dc.SetFont(self._FONT_NAME)
        dc.SetTextForeground(CLR_MUTED)
        name_text = self.name.upper()
        tw, _ = dc.GetTextExtent(name_text)
        dc.DrawText(name_text, (w - tw) // 2, 10)

        # value
        dc.SetFont(self._FONT_VALUE)
        dc.SetTextForeground(CLR_TEXT)
        tw, th = dc.GetTextExtent(self._value)
        dc.DrawText(self._value, (w - tw) // 2, 10 + 14 + 4)

        # unit
        if self._unit:
            dc.SetFont(self._FONT_NAME)
            dc.SetTextForeground(CLR_MUTED)
            tw, _ = dc.GetTextExtent(self._unit)
            dc.DrawText(self._unit, (w - tw) // 2, h - 14)

    # ── Live update ───────────────────────────────────────────────────────────

    def update(self, value, logging_active):
        new_value = str(value)
        new_bg    = wx.Colour(40, 20, 20) if logging_active else (
                    wx.Colour(*self._mark_color) if self._mark_color else CLR_SURFACE)

        if self._value != new_value or self._bg != new_bg:
            self._value = new_value
            self._bg    = new_bg
            return True   # caller should Refresh/Update us
        return False

    # ── Color marking ─────────────────────────────────────────────────────────

    def set_mark_color(self, color_tuple):
        self._mark_color = color_tuple
        self._bg = wx.Colour(*color_tuple) if color_tuple else CLR_SURFACE
        self.Refresh()

    def _on_right_click(self, _):
        menu = wx.Menu()
        for label, color in self.COLOR_PRESETS:
            item = menu.Append(wx.ID_ANY, label)
            self.Bind(wx.EVT_MENU, lambda e, c=color: self._pick_color(c), item)
        self.PopupMenu(menu)
        menu.Destroy()

    def _pick_color(self, color_tuple):
        self.set_mark_color(color_tuple)
        evt = wx.CommandEvent(wx.EVT_MENU.typeId)
        evt.SetString("color:" + self.name)
        wx.PostEvent(self.GetParent(), evt)

    # ── Drag handle ───────────────────────────────────────────────────────────

    def _start_drag(self, event):
        self._dragging   = True
        self._drag_start = self.GetParent().ScreenToClient(self.ClientToScreen(event.GetPosition()))
        self.CaptureMouse()

    def _end_drag(self, event):
        if not getattr(self, "_dragging", False):
            return
        self._dragging = False
        if self.HasCapture():
            self.ReleaseMouse()
        pos = self.GetParent().ScreenToClient(self.ClientToScreen(event.GetPosition()))
        wx.PostEvent(self.GetParent(), _DropEvent(self.name, pos))

    def _motion_drag(self, event):
        if getattr(self, "_dragging", False) and event.Dragging() and event.LeftIsDown():
            pos = self.GetParent().ScreenToClient(self.ClientToScreen(event.GetPosition()))
            wx.PostEvent(self.GetParent(), _DragEvent(self.name, pos))

    def _bind_drag(self):
        self.Bind(wx.EVT_LEFT_DOWN, self._start_drag)
        self.Bind(wx.EVT_LEFT_UP,   self._end_drag)
        self.Bind(wx.EVT_MOTION,    self._motion_drag)


# ── Custom drag/drop events ───────────────────────────────────────────────────

_EVT_CARD_DRAG_TYPE = wx.NewEventType()
_EVT_CARD_DROP_TYPE = wx.NewEventType()
EVT_CARD_DRAG = wx.PyEventBinder(_EVT_CARD_DRAG_TYPE)
EVT_CARD_DROP = wx.PyEventBinder(_EVT_CARD_DROP_TYPE)

class _DragEvent(wx.PyEvent):
    def __init__(self, name, pos):
        super().__init__(_EVT_CARD_DRAG_TYPE)
        self.card_name = name
        self.pos       = pos

class _DropEvent(wx.PyEvent):
    def __init__(self, name, pos):
        super().__init__(_EVT_CARD_DROP_TYPE)
        self.card_name = name
        self.pos       = pos


# ── Gauge grid panel ──────────────────────────────────────────────────────────

class GaugeGrid(wx.ScrolledWindow):
    """Wrapping grid of GaugeCards with drag-to-reorder support."""

    def __init__(self, parent):
        super().__init__(parent, style=wx.BORDER_NONE)
        self.SetScrollRate(0, 10)
        self.SetBackgroundColour(CLR_BG)

        self._sizer      = wx.WrapSizer(wx.HORIZONTAL)
        self.SetSizer(self._sizer)

        self._cards      = {}     # name → GaugeCard
        self._order      = []     # list of names in display order
        self._drag_name  = None   # card being dragged
        self._drop_idx   = None   # current drop target index
        self._on_order_changed = None  # callback(order)

        self.Connect(-1, -1, _EVT_CARD_DRAG_TYPE, self._on_card_drag)
        self.Connect(-1, -1, _EVT_CARD_DROP_TYPE, self._on_card_drop)
        # save colors when right-click menu fires
        self.Bind(wx.EVT_MENU, self._on_color_changed)

    # ── Public API ────────────────────────────────────────────────────────────

    def set_params(self, names, prefs):
        """Show only the given param names as cards."""
        names_set = set(names)

        # remove cards no longer selected
        to_remove = [n for n in list(self._cards) if n not in names_set]
        if to_remove:
            self.Freeze()
            for name in to_remove:
                self._cards[name].Destroy()
                del self._cards[name]
            self.Thaw()

        # update order
        self._order = [n for n in self._order if n in names_set]
        for name in names:
            if name not in self._order:
                self._order.append(name)

        # create only missing cards, batched
        missing = [n for n in self._order if n not in self._cards]
        if missing:
            self.Freeze()
            for name in missing:
                card = GaugeCard(self, name)
                card._bind_drag()
                self._cards[name] = card
                color = prefs.get("colors", {}).get(name)
                if color:
                    card.set_mark_color(color)
            self.Thaw()

        self._rebuild_sizer()

    def update(self, by_name, is_logging):
        # by_name is a pre-built dict: name → value string
        dirty = []
        for name, card in self._cards.items():
            if name in by_name:
                if card.update(by_name[name], is_logging):
                    dirty.append(card)
        # invalidate all dirty cards then flush once
        for card in dirty:
            card.Refresh()
        if dirty:
            self.Update()

    def get_order(self):
        return list(self._order)

    def get_colors(self):
        return {name: card._mark_color for name, card in self._cards.items()
                if card._mark_color is not None}

    def set_order_changed_callback(self, fn):
        self._on_order_changed = fn

    # ── Internal ─────────────────────────────────────────────────────────────

    def _rebuild_sizer(self):
        self._sizer.Clear(delete_windows=False)
        for name in self._order:
            if name in self._cards:
                self._sizer.Add(self._cards[name], 0, wx.ALL, 5)
        self.Layout()
        self._sizer.FitInside(self)

    def _card_at(self, pos):
        """Return index in self._order of the card whose rect contains pos."""
        for i, name in enumerate(self._order):
            card = self._cards.get(name)
            if card and card.GetRect().Contains(pos):
                return i
        return None

    def _on_card_drag(self, event):
        self._drag_name = event.card_name
        idx = self._card_at(event.pos)
        if idx is not None and idx != self._drop_idx:
            self._drop_idx = idx
            self._highlight_drop(idx)

    def _on_card_drop(self, event):
        if self._drag_name is None:
            return
        target_idx = self._card_at(event.pos)
        if target_idx is not None and self._drag_name in self._order:
            src_idx = self._order.index(self._drag_name)
            if src_idx != target_idx:
                self._order.insert(target_idx, self._order.pop(src_idx))
                self._rebuild_sizer()
                if self._on_order_changed:
                    self._on_order_changed(self._order)
        self._drag_name = None
        self._drop_idx  = None
        self._clear_highlights()

    def _highlight_drop(self, idx):
        self._clear_highlights()
        name = self._order[idx] if idx < len(self._order) else None
        if name and name in self._cards:
            self._cards[name]._drop_target = True
            self._cards[name].Refresh()

    def _clear_highlights(self):
        for card in self._cards.values():
            if getattr(card, "_drop_target", False):
                card._drop_target = False
                card.Refresh()

    def _on_color_changed(self, event):
        s = event.GetString()
        if s.startswith("color:") and self._on_order_changed:
            self._on_order_changed(self._order)  # triggers prefs save in parent
        event.Skip()


# ── Main panel ────────────────────────────────────────────────────────────────

class MainPanel(wx.Panel):
    def __init__(self, parent):
        super().__init__(parent)
        self.SetBackgroundColour(CLR_BG)

        self._logger      = None
        self._thread      = None
        self._iface_list  = []
        self._last_status = ""
        self._all_params  = []     # all param names seen so far
        self._selected    = set()  # currently selected param names
        self._prefs       = _load_prefs()

        self._build_ui()
        self._do_scan()

        self._ui_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_ui_timer, self._ui_timer)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        root = wx.BoxSizer(wx.VERTICAL)

        # ── Controls bar ─────────────────────────────────────────────────────
        ctrl_panel = wx.Panel(self)
        ctrl_panel.SetBackgroundColour(CLR_SURFACE)
        ctrl = wx.BoxSizer(wx.VERTICAL)

        # Row 1: device picker + mode + stream + connect
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

        # Row 2: log path
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

        self._log_toggle_btn    = wx.Button(status_panel, label="Toggle Log",     size=(90, -1))
        self._select_params_btn = wx.Button(status_panel, label="Select Params",  size=(100, -1))
        self._log_toggle_btn.Enable(False)
        self._select_params_btn.Enable(False)
        self._log_toggle_btn.Bind(wx.EVT_BUTTON, self._on_toggle_log)
        self._select_params_btn.Bind(wx.EVT_BUTTON, self._on_select_params)

        ss.Add(self._status_dot,         0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT,  10)
        ss.Add(self._status_text,        0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT,   6)
        ss.Add(self._logging_text,       0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT,  16)
        ss.Add(self._log_toggle_btn,     0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT,  20)
        ss.Add(self._select_params_btn,  0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT,  10)
        status_panel.SetSizer(ss)
        for child in status_panel.GetChildren():
            child.SetBackgroundColour(CLR_BG)
            child.SetForegroundColour(CLR_TEXT)
        root.Add(status_panel, 0, wx.EXPAND | wx.TOP, 6)

        # ── Gauge grid ────────────────────────────────────────────────────────
        self._grid = GaugeGrid(self)
        self._grid.set_order_changed_callback(self._save_prefs)
        root.Add(self._grid, 1, wx.EXPAND | wx.ALL, 10)

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
        mode     = self._mode_choice.GetStringSelection()
        log_path = self._path_text.GetValue()
        stream   = self._stream_chk.GetValue()

        os.makedirs(log_path, exist_ok=True)

        self._logger = LoggerCore(
            interface=iface_type,
            interface_path=iface_path,
            mode=mode,
            log_path=log_path + os.sep,
            run_server=stream,
        )

        self._connect_btn.Enable(False)
        self._stop_btn.Enable(True)
        self._log_toggle_btn.Enable(True)
        self._select_params_btn.Enable(True)
        self._set_status("Connecting…", CLR_MUTED)

        self._thread = threading.Thread(target=self._run_logger, daemon=True)
        self._thread.start()
        self._ui_timer.Start(200)  # 5fps — sufficient for gauges, keeps UI responsive

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
            wx.MessageBox("No parameters received yet — wait for data.", "No data", wx.OK | wx.ICON_INFORMATION)
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
        # restore saved order, append any new names at end
        saved_order = self._prefs.get("order", [])
        ordered = [n for n in saved_order if n in self._selected]
        for n in self._selected:
            if n not in ordered:
                ordered.append(n)
        self._grid.set_params(ordered, self._prefs)

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

        # build a flat name→value map and discover new params in one pass
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

        # first time we see params: apply saved selection or show all
        if new_found and not self._selected:
            saved = set(self._prefs.get("selected", []))
            self._selected = (saved & set(self._all_params)) if saved else set(self._all_params)
            self._apply_selection()

        is_logging = by_name.get("isLogging", "False") == "True"
        self._set_status("Connected — polling", CLR_GREEN)
        self._logging_text.SetLabel("● LOGGING" if is_logging else "")
        self._grid.update(by_name, is_logging)

    # ── Prefs save ────────────────────────────────────────────────────────────

    def _save_prefs(self, order):
        self._prefs["order"]    = order
        self._prefs["selected"] = list(self._selected)
        self._prefs["colors"]   = self._grid.get_colors()
        _save_prefs(self._prefs)

    # ── Status ────────────────────────────────────────────────────────────────

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
