"""
VW ECU Logger — Standalone wxPython GUI
"""

import wx
import threading
import os
import sys

from logger_core import LoggerCore

DEFAULT_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")


# ── Colour constants ──────────────────────────────────────────────────────────
CLR_BG      = wx.Colour(15,  17,  23)
CLR_SURFACE = wx.Colour(26,  29,  39)
CLR_BORDER  = wx.Colour(42,  45,  58)
CLR_TEXT    = wx.Colour(226, 232, 240)
CLR_MUTED   = wx.Colour(100, 116, 139)
CLR_GREEN   = wx.Colour(34,  197, 94)
CLR_RED     = wx.Colour(239, 68,  68)
CLR_ACCENT  = wx.Colour(79,  156, 249)


# ── Interface scanner (ported from VW_Flash_GUI.py) ───────────────────────────

def _scan_j2534_registry():
    """Read installed J2534 devices from Windows registry.
    Checks both the native hive and Wow6432Node (32-bit entries) so
    64-bit Python finds the same devices as 32-bit VW Flash."""
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
    """
    Returns a list of (display_label, interface_type, interface_path) tuples.
    interface_type is "J2534", "SocketCAN", or "USBISOTP".
    interface_path is the DLL path, CAN interface name, or serial port.
    """
    results = []

    # J2534 devices from Windows registry
    for name, dll in _scan_j2534_registry():
        results.append((f"J2534 — {name}", "J2534", dll))

    # SocketCAN on Linux
    if sys.platform == "linux":
        results.append(("SocketCAN — can0", "SocketCAN", "can0"))

    # Serial ports (USB-ISOTP adapters)
    try:
        import serial.tools.list_ports
        for port in serial.tools.list_ports.comports():
            label = f"USB-ISOTP — {port.name} : {port.description}"
            results.append((label, "USBISOTP", port.device))
    except Exception:
        pass

    return results


# ── Gauge card ────────────────────────────────────────────────────────────────

class GaugeCard(wx.Panel):
    def __init__(self, parent, name, unit):
        super().__init__(parent, style=wx.BORDER_NONE)
        self.name        = name
        self._mark_color = None

        self.SetBackgroundColour(CLR_SURFACE)
        sizer = wx.BoxSizer(wx.VERTICAL)

        self._name_lbl  = wx.StaticText(self, label=name.upper())
        self._value_lbl = wx.StaticText(self, label="—")
        self._unit_lbl  = wx.StaticText(self, label=unit)

        self._name_lbl.SetForegroundColour(CLR_MUTED)
        self._name_lbl.SetFont(wx.Font(7, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        self._value_lbl.SetForegroundColour(CLR_TEXT)
        self._value_lbl.SetFont(wx.Font(18, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        self._unit_lbl.SetForegroundColour(CLR_MUTED)
        self._unit_lbl.SetFont(wx.Font(7, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))

        sizer.Add(self._name_lbl,  0, wx.ALIGN_CENTER | wx.TOP, 8)
        sizer.Add(self._value_lbl, 0, wx.ALIGN_CENTER | wx.TOP | wx.BOTTOM, 4)
        sizer.Add(self._unit_lbl,  0, wx.ALIGN_CENTER | wx.BOTTOM, 8)
        self.SetSizer(sizer)
        self.SetMinSize((140, 80))

        for w in (self, self._name_lbl, self._value_lbl, self._unit_lbl):
            w.Bind(wx.EVT_RIGHT_DOWN, self._on_right_click)

    def update(self, value, logging_active):
        changed = False
        new_label = str(value)
        if self._value_lbl.GetLabel() != new_label:
            self._value_lbl.SetLabel(new_label)
            changed = True

        if logging_active:
            new_bg = wx.Colour(40, 20, 20)
        elif self._mark_color:
            new_bg = self._mark_color
        else:
            new_bg = CLR_SURFACE

        if self.GetBackgroundColour() != new_bg:
            self.SetBackgroundColour(new_bg)
            changed = True

        if changed:
            self._value_lbl.Refresh()  # only repaint the label, not the whole card

    def _on_right_click(self, _):
        menu = wx.Menu()
        for label, color in [
            ("Red",    wx.Colour(80, 20, 20)),
            ("Orange", wx.Colour(80, 50, 10)),
            ("Green",  wx.Colour(10, 60, 30)),
            ("Blue",   wx.Colour(10, 40, 80)),
            ("Purple", wx.Colour(50, 20, 80)),
            ("Clear",  None),
        ]:
            item = menu.Append(wx.ID_ANY, label)
            self.Bind(wx.EVT_MENU, lambda e, c=color: self._set_color(c), item)
        self.PopupMenu(menu)
        menu.Destroy()

    def _set_color(self, color):
        self._mark_color = color
        self.SetBackgroundColour(color if color else CLR_SURFACE)
        self.Refresh()


# ── Main panel ────────────────────────────────────────────────────────────────

class MainPanel(wx.Panel):
    def __init__(self, parent):
        super().__init__(parent)
        self.SetBackgroundColour(CLR_BG)

        self._logger      = None
        self._thread      = None
        self._cards       = {}        # name → GaugeCard
        self._iface_list  = []        # parallel list to dropdown: (label, type, path)
        self._last_status = ""

        self._build_ui()
        self._do_scan()               # populate on startup

        # Poll logger state at 10fps from GUI thread — no wx.CallAfter flooding
        self._ui_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_ui_timer, self._ui_timer)

    def _build_ui(self):
        root = wx.BoxSizer(wx.VERTICAL)

        # ── Controls bar (two rows) ───────────────────────────────────────────
        ctrl_panel = wx.Panel(self)
        ctrl_panel.SetBackgroundColour(CLR_SURFACE)
        ctrl = wx.BoxSizer(wx.VERTICAL)

        # Row 1: device picker
        row1 = wx.BoxSizer(wx.HORIZONTAL)
        row1.Add(wx.StaticText(ctrl_panel, label="Device:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 10)

        self._device_choice = wx.Choice(ctrl_panel, size=(380, -1))
        row1.Add(self._device_choice, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 6)

        self._scan_btn = wx.Button(ctrl_panel, label="Scan", size=(52, -1))
        self._scan_btn.Bind(wx.EVT_BUTTON, self._on_scan)
        row1.Add(self._scan_btn, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 6)

        # Mode
        row1.Add(wx.StaticText(ctrl_panel, label="Mode:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 16)
        self._mode_choice = wx.Choice(ctrl_panel, choices=["22", "3E", "HSL"])
        self._mode_choice.SetSelection(0)
        row1.Add(self._mode_choice, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 6)

        # TCP stream toggle
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

        self._log_toggle_btn = wx.Button(status_panel, label="Toggle Log", size=(90, -1))
        self._log_toggle_btn.Enable(False)
        self._log_toggle_btn.Bind(wx.EVT_BUTTON, self._on_toggle_log)

        ss.Add(self._status_dot,      0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT,  10)
        ss.Add(self._status_text,     0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT,   6)
        ss.Add(self._logging_text,    0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT,  16)
        ss.Add(self._log_toggle_btn,  0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT,  20)
        status_panel.SetSizer(ss)
        for child in status_panel.GetChildren():
            child.SetBackgroundColour(CLR_BG)
            child.SetForegroundColour(CLR_TEXT)
        root.Add(status_panel, 0, wx.EXPAND | wx.TOP, 6)

        # ── Gauge grid ────────────────────────────────────────────────────────
        self._grid_panel = wx.ScrolledWindow(self)
        self._grid_panel.SetScrollRate(0, 10)
        self._grid_panel.SetBackgroundColour(CLR_BG)
        self._grid_sizer = wx.WrapSizer(wx.HORIZONTAL)
        self._grid_panel.SetSizer(self._grid_sizer)
        root.Add(self._grid_panel, 1, wx.EXPAND | wx.ALL, 10)

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

    # ── Event handlers ────────────────────────────────────────────────────────

    def _browse_path(self, _):
        dlg = wx.DirDialog(self, "Select log folder", style=wx.DD_DEFAULT_STYLE)
        if dlg.ShowModal() == wx.ID_OK:
            self._path_text.SetValue(dlg.GetPath())
        dlg.Destroy()

    def _on_connect(self, _):
        idx = self._device_choice.GetSelection()
        if idx < 0 or idx >= len(self._iface_list):
            wx.MessageBox("Please select a device first.", "No device selected", wx.OK | wx.ICON_WARNING)
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
        self._set_status("Connecting…", CLR_MUTED)

        self._thread = threading.Thread(target=self._run_logger, daemon=True)
        self._thread.start()
        self._ui_timer.Start(100)  # 10fps

    def _run_logger(self):
        try:
            self._logger.start()
        except Exception as e:
            wx.CallAfter(self._set_status, "Error: " + str(e), CLR_RED)
            wx.CallAfter(self._reset_buttons)

    # ── Timer-driven UI update (replaces wx.CallAfter flooding) ──────────────

    def _on_ui_timer(self, _):
        if self._logger is None:
            return

        if self._logger.kill:
            self._ui_timer.Stop()
            self._set_status("Stopped", CLR_MUTED)
            self._logging_text.SetLabel("")
            self._reset_buttons()
            return

        data = dict(self._logger.data_stream)  # snapshot to avoid races

        is_logging = data.get("isLogging", {}).get("Value") == "True"

        self._set_status("Connected — polling", CLR_GREEN)
        self._logging_text.SetLabel("● LOGGING" if is_logging else "")

        rebuilt = False
        self._grid_panel.Freeze()
        try:
            for key, entry in data.items():
                if key in ("Time", "isLogging"):
                    continue
                name = entry.get("Name", str(key))
                val  = entry.get("Value", "—")
                if name not in self._cards:
                    card = GaugeCard(self._grid_panel, name, "")
                    self._cards[name] = card
                    self._grid_sizer.Add(card, 0, wx.ALL, 5)
                    rebuilt = True
                self._cards[name].update(val, is_logging)
        finally:
            self._grid_panel.Thaw()

        if rebuilt:
            self._grid_panel.Layout()
            self._grid_sizer.FitInside(self._grid_panel)

    def _on_toggle_log(self, _):
        if self._logger:
            self._logger.toggle_key_trigger()

    def _on_stop(self, _):
        self._ui_timer.Stop()
        if self._logger:
            self._logger.stop()
        self._reset_buttons()
        self._set_status("Disconnected", CLR_MUTED)
        self._logging_text.SetLabel("")

    def _reset_buttons(self):
        self._connect_btn.Enable(True)
        self._stop_btn.Enable(False)
        self._log_toggle_btn.Enable(False)

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
