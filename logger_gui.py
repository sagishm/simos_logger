"""
VW ECU Logger — Standalone wxPython GUI
"""

import wx
import threading
import os
import sys

from logger_core import LoggerCore

DEFAULT_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
DEFAULT_J2534_DLL = (
    "C:/Program Files (x86)/OpenECU/OpenPort 2.0/drivers/openport 2.0/op20pt32.dll"
)


# ── Colour constants ──────────────────────────────────────────────────────────
CLR_BG      = wx.Colour(15,  17,  23)
CLR_SURFACE = wx.Colour(26,  29,  39)
CLR_BORDER  = wx.Colour(42,  45,  58)
CLR_TEXT    = wx.Colour(226, 232, 240)
CLR_MUTED   = wx.Colour(100, 116, 139)
CLR_GREEN   = wx.Colour(34,  197, 94)
CLR_RED     = wx.Colour(239, 68,  68)
CLR_ACCENT  = wx.Colour(79,  156, 249)


class GaugeCard(wx.Panel):
    """Single live-value card in the gauge grid."""

    def __init__(self, parent, name, unit):
        super().__init__(parent, style=wx.BORDER_NONE)
        self.name  = name
        self.unit  = unit
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

        self.Bind(wx.EVT_RIGHT_DOWN, self._on_right_click)
        self._name_lbl.Bind(wx.EVT_RIGHT_DOWN, self._on_right_click)
        self._value_lbl.Bind(wx.EVT_RIGHT_DOWN, self._on_right_click)

    def update(self, value, logging_active):
        self._value_lbl.SetLabel(str(value))
        if logging_active:
            self.SetBackgroundColour(wx.Colour(40, 20, 20))
        elif self._mark_color:
            self.SetBackgroundColour(self._mark_color)
        else:
            self.SetBackgroundColour(CLR_SURFACE)
        self.Refresh()

    def _on_right_click(self, _):
        menu   = wx.Menu()
        colors = [
            ("Red",    wx.Colour(80, 20, 20)),
            ("Orange", wx.Colour(80, 50, 10)),
            ("Green",  wx.Colour(10, 60, 30)),
            ("Blue",   wx.Colour(10, 40, 80)),
            ("Purple", wx.Colour(50, 20, 80)),
            ("Clear",  None),
        ]
        for label, color in colors:
            item = menu.Append(wx.ID_ANY, label)
            self.Bind(wx.EVT_MENU, lambda e, c=color: self._set_color(c), item)
        self.PopupMenu(menu)
        menu.Destroy()

    def _set_color(self, color):
        self._mark_color = color
        bg = color if color else CLR_SURFACE
        self.SetBackgroundColour(bg)
        self.Refresh()


class MainPanel(wx.Panel):
    def __init__(self, parent):
        super().__init__(parent)
        self.SetBackgroundColour(CLR_BG)

        self._logger   = None
        self._thread   = None
        self._cards    = {}   # name → GaugeCard

        self._build_ui()

    def _build_ui(self):
        root = wx.BoxSizer(wx.VERTICAL)

        # ── Top controls bar ─────────────────────────────────────────────────
        ctrl_panel = wx.Panel(self)
        ctrl_panel.SetBackgroundColour(CLR_SURFACE)
        ctrl = wx.BoxSizer(wx.HORIZONTAL)

        # Interface
        ctrl.Add(wx.StaticText(ctrl_panel, label="Interface:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 10)
        self._iface_choice = wx.Choice(ctrl_panel, choices=["J2534", "SocketCAN", "USBISOTP"])
        self._iface_choice.SetSelection(0)
        ctrl.Add(self._iface_choice, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 6)
        self._iface_choice.Bind(wx.EVT_CHOICE, self._on_iface_change)

        # DLL path (J2534 only)
        self._dll_label = wx.StaticText(ctrl_panel, label="DLL:")
        self._dll_text  = wx.TextCtrl(ctrl_panel, value=DEFAULT_J2534_DLL, size=(280, -1))
        self._dll_btn   = wx.Button(ctrl_panel, label="…", size=(26, -1))
        self._dll_btn.Bind(wx.EVT_BUTTON, self._browse_dll)
        ctrl.Add(self._dll_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 10)
        ctrl.Add(self._dll_text,  0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 4)
        ctrl.Add(self._dll_btn,   0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 2)

        # Mode
        ctrl.Add(wx.StaticText(ctrl_panel, label="Mode:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 14)
        self._mode_choice = wx.Choice(ctrl_panel, choices=["22", "3E", "HSL"])
        self._mode_choice.SetSelection(0)
        ctrl.Add(self._mode_choice, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 6)

        # Log path
        ctrl.Add(wx.StaticText(ctrl_panel, label="Log path:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 14)
        self._path_text = wx.TextCtrl(ctrl_panel, value=DEFAULT_LOG_PATH, size=(200, -1))
        self._path_btn  = wx.Button(ctrl_panel, label="…", size=(26, -1))
        self._path_btn.Bind(wx.EVT_BUTTON, self._browse_path)
        ctrl.Add(self._path_text, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 4)
        ctrl.Add(self._path_btn,  0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 2)

        # TCP stream toggle
        self._stream_chk = wx.CheckBox(ctrl_panel, label="Stream :65432")
        self._stream_chk.SetValue(True)
        ctrl.Add(self._stream_chk, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 14)

        ctrl.AddStretchSpacer()

        # Connect / Stop
        self._connect_btn = wx.Button(ctrl_panel, label="Connect", size=(80, -1))
        self._stop_btn    = wx.Button(ctrl_panel, label="Stop",    size=(80, -1))
        self._stop_btn.Enable(False)
        self._connect_btn.SetBackgroundColour(CLR_ACCENT)
        self._connect_btn.SetForegroundColour(wx.WHITE)
        self._connect_btn.Bind(wx.EVT_BUTTON, self._on_connect)
        self._stop_btn.Bind(wx.EVT_BUTTON, self._on_stop)
        ctrl.Add(self._connect_btn, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        ctrl.Add(self._stop_btn,    0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)

        ctrl_panel.SetSizer(ctrl)
        ctrl_panel.SetMinSize((-1, 44))
        for child in ctrl_panel.GetChildren():
            child.SetBackgroundColour(CLR_SURFACE)
            child.SetForegroundColour(CLR_TEXT)

        root.Add(ctrl_panel, 0, wx.EXPAND)

        # ── Status bar ────────────────────────────────────────────────────────
        status_panel = wx.Panel(self)
        status_panel.SetBackgroundColour(CLR_BG)
        status_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self._status_dot   = wx.StaticText(status_panel, label="●")
        self._status_text  = wx.StaticText(status_panel, label="Disconnected")
        self._logging_text = wx.StaticText(status_panel, label="")
        self._status_dot.SetForegroundColour(CLR_MUTED)
        self._status_text.SetForegroundColour(CLR_MUTED)
        self._logging_text.SetForegroundColour(CLR_RED)
        self._logging_text.SetFont(wx.Font(9, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        status_sizer.Add(self._status_dot,   0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT,  10)
        status_sizer.Add(self._status_text,  0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT,   6)
        status_sizer.Add(self._logging_text, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT,  16)
        # Manual log toggle button
        self._log_toggle_btn = wx.Button(status_panel, label="Toggle Log", size=(90, -1))
        self._log_toggle_btn.Enable(False)
        self._log_toggle_btn.Bind(wx.EVT_BUTTON, self._on_toggle_log)
        status_sizer.Add(self._log_toggle_btn, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 20)
        status_panel.SetSizer(status_sizer)
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

    # ── Event handlers ────────────────────────────────────────────────────────

    def _on_iface_change(self, _):
        iface = self._iface_choice.GetStringSelection()
        show_dll = iface == "J2534"
        self._dll_label.Show(show_dll)
        self._dll_text.Show(show_dll)
        self._dll_btn.Show(show_dll)
        self.Layout()

    def _browse_dll(self, _):
        dlg = wx.FileDialog(self, "Select J2534 DLL", wildcard="DLL files (*.dll)|*.dll",
                            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST)
        if dlg.ShowModal() == wx.ID_OK:
            self._dll_text.SetValue(dlg.GetPath())
        dlg.Destroy()

    def _browse_path(self, _):
        dlg = wx.DirDialog(self, "Select log folder", style=wx.DD_DEFAULT_STYLE)
        if dlg.ShowModal() == wx.ID_OK:
            self._path_text.SetValue(dlg.GetPath())
        dlg.Destroy()

    def _on_connect(self, _):
        iface    = self._iface_choice.GetStringSelection()
        mode     = self._mode_choice.GetStringSelection()
        log_path = self._path_text.GetValue()
        stream   = self._stream_chk.GetValue()

        iface_path = None
        if iface == "J2534":
            iface_path = self._dll_text.GetValue()

        os.makedirs(log_path, exist_ok=True)

        self._logger = LoggerCore(
            interface=iface,
            interface_path=iface_path,
            mode=mode,
            log_path=log_path + os.sep,
            run_server=stream,
            callback=self._on_logger_callback,
        )

        self._connect_btn.Enable(False)
        self._stop_btn.Enable(True)
        self._log_toggle_btn.Enable(True)
        self._set_status("Connecting…", CLR_MUTED)

        self._thread = threading.Thread(target=self._run_logger, daemon=True)
        self._thread.start()

    def _run_logger(self):
        try:
            self._logger.start()
        except Exception as e:
            wx.CallAfter(self._set_status, "Error: " + str(e), CLR_RED)
            wx.CallAfter(self._reset_buttons)

    def _on_stop(self, _):
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

    # ── Logger callback (called from background thread) ───────────────────────

    def _on_logger_callback(self, status="", data=None):
        wx.CallAfter(self._update_ui, status, data)

    def _update_ui(self, status, data):
        is_logging = False
        if data:
            is_logging = data.get("isLogging", {}).get("Value") == "True"

        # Status dot color
        if "Error" in status or "Timeout" in status:
            self._set_status(status, CLR_RED)
        elif "running" in status.lower() or "polling" in status.lower() or "connected" in status.lower():
            self._set_status(status, CLR_GREEN)
        else:
            self._set_status(status, CLR_MUTED)

        self._logging_text.SetLabel("● LOGGING" if is_logging else "")

        if not data:
            return

        # Build/update gauge cards
        rebuilt = False
        for key, entry in data.items():
            if key in ("Time", "isLogging"):
                continue
            name = entry.get("Name", str(key))
            val  = entry.get("Value", "—")
            if name not in self._cards:
                unit = ""
                card = GaugeCard(self._grid_panel, name, unit)
                self._cards[name] = card
                self._grid_sizer.Add(card, 0, wx.ALL, 5)
                rebuilt = True
            self._cards[name].update(val, is_logging)

        if rebuilt:
            self._grid_panel.Layout()
            self._grid_sizer.FitInside(self._grid_panel)

    def _set_status(self, text, color):
        self._status_dot.SetForegroundColour(color)
        self._status_text.SetLabel(text)
        self._status_text.SetForegroundColour(color)
        self.Layout()


class LoggerFrame(wx.Frame):
    def __init__(self):
        super().__init__(None, title="VW ECU Logger", size=(1024, 640))
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
