"""
VW ECU Logger — Standalone wxPython GUI
"""

import wx
import wx.html2
import threading
import os
import sys
import json

from logger_core import LoggerCore

DEFAULT_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
PREFS_FILE       = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gauge_prefs.json")

# ── Colour constants (wx widgets only) ───────────────────────────────────────
CLR_BG      = wx.Colour(15,  17,  23)
CLR_SURFACE = wx.Colour(26,  29,  39)
CLR_TEXT    = wx.Colour(226, 232, 240)
CLR_MUTED   = wx.Colour(100, 116, 139)
CLR_GREEN   = wx.Colour(34,  197, 94)
CLR_RED     = wx.Colour(239, 68,  68)
CLR_ACCENT  = wx.Colour(79,  156, 249)

# ── Gauge HTML (loaded once into WebView) ─────────────────────────────────────
GAUGE_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: #0f1117;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    padding: 8px;
    overflow-y: auto;
  }
  #grid {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
  .card {
    width: 150px;
    height: 90px;
    background: #1a1d27;
    border-radius: 6px;
    overflow: hidden;
    cursor: grab;
    position: relative;
    transition: background 0.15s;
  }
  .card.logging { background: #281414; }
  .card .strip {
    height: 5px;
    background: #2a2d3a;
    transition: background 0.15s;
  }
  .card.drag-over .strip { background: #4f9cf9; }
  .card .name {
    font-size: 9px;
    color: #64748b;
    text-align: center;
    padding: 4px 4px 0;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .card .value {
    font-size: 22px;
    font-weight: 700;
    color: #e2e8f0;
    text-align: center;
    padding: 2px 4px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
</style>
</head>
<body>
<div id="grid"></div>
<script>
var _order  = [];
var _values = {};
var _colors = {};
var _logging = false;
var _dragSrc = null;

function setParams(order, colors) {
  _order  = order;
  _colors = colors;
  renderAll();
}

function update(byName, isLogging) {
  var changed = (isLogging !== _logging);
  _logging = isLogging;
  for (var i = 0; i < _order.length; i++) {
    var n = _order[i];
    if (byName[n] !== undefined && byName[n] !== _values[n]) {
      _values[n] = byName[n];
      changed = true;
    }
  }
  if (changed) renderAll();
}

function renderAll() {
  var grid = document.getElementById('grid');
  // reuse existing cards to avoid full DOM rebuild
  var existing = {};
  var cards = grid.querySelectorAll('.card');
  for (var i = 0; i < cards.length; i++) existing[cards[i].dataset.name] = cards[i];

  // remove cards no longer in order
  for (var n in existing) {
    if (_order.indexOf(n) === -1) grid.removeChild(existing[n]);
  }

  // insert/reorder cards
  for (var i = 0; i < _order.length; i++) {
    var name = _order[i];
    var card = existing[name] || makeCard(name);
    updateCard(card, name);
    if (grid.children[i] !== card) grid.insertBefore(card, grid.children[i] || null);
  }
}

function makeCard(name) {
  var card = document.createElement('div');
  card.className = 'card';
  card.dataset.name = name;
  card.draggable = true;
  card.innerHTML =
    '<div class="strip"></div>' +
    '<div class="name">' + escHtml(name) + '</div>' +
    '<div class="value">—</div>';
  card.addEventListener('dragstart', onDragStart);
  card.addEventListener('dragover',  onDragOver);
  card.addEventListener('dragleave', onDragLeave);
  card.addEventListener('drop',      onDrop);
  card.addEventListener('contextmenu', onCtxMenu);
  return card;
}

function updateCard(card, name) {
  card.classList.toggle('logging', _logging);
  var bg = (!_logging && _colors[name]) ? _colors[name] : null;
  card.style.background = bg || (_logging ? '#281414' : '#1a1d27');
  card.querySelector('.value').textContent = _values[name] !== undefined ? _values[name] : '—';
}

function escHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ── Drag-to-reorder ───────────────────────────────────────────────────────
function onDragStart(e) { _dragSrc = this; e.dataTransfer.effectAllowed = 'move'; }
function onDragOver(e)  { e.preventDefault(); this.classList.add('drag-over'); }
function onDragLeave()  { this.classList.remove('drag-over'); }
function onDrop(e) {
  e.preventDefault();
  this.classList.remove('drag-over');
  if (_dragSrc === this) return;
  var src = _order.indexOf(_dragSrc.dataset.name);
  var dst = _order.indexOf(this.dataset.name);
  if (src === -1 || dst === -1) return;
  _order.splice(dst, 0, _order.splice(src, 1)[0]);
  renderAll();
  if (window.onOrderChanged) window.onOrderChanged(_order);
}

// ── Right-click color menu ────────────────────────────────────────────────
var _ctxName = null;
var _ctxMenu = null;
var COLOR_PRESETS = [
  ['Red',    'rgb(80,20,20)'],
  ['Orange', 'rgb(80,50,10)'],
  ['Green',  'rgb(10,60,30)'],
  ['Blue',   'rgb(10,40,80)'],
  ['Purple', 'rgb(50,20,80)'],
  ['Clear',  null],
];

function onCtxMenu(e) {
  e.preventDefault();
  _ctxName = this.dataset.name;
  removeCtxMenu();
  _ctxMenu = document.createElement('div');
  _ctxMenu.style.cssText = 'position:fixed;background:#1a1d27;border:1px solid #2a2d3a;' +
    'border-radius:4px;padding:4px 0;z-index:9999;min-width:90px;' +
    'left:' + e.clientX + 'px;top:' + e.clientY + 'px;';
  COLOR_PRESETS.forEach(function(p) {
    var item = document.createElement('div');
    item.textContent = p[0];
    item.style.cssText = 'padding:5px 14px;color:#e2e8f0;font-size:12px;cursor:pointer;';
    item.onmouseenter = function() { this.style.background='#2a2d3a'; };
    item.onmouseleave = function() { this.style.background=''; };
    item.onclick = function() { pickColor(_ctxName, p[1]); removeCtxMenu(); };
    _ctxMenu.appendChild(item);
  });
  document.body.appendChild(_ctxMenu);
  setTimeout(function() { document.addEventListener('click', removeCtxMenu, {once:true}); }, 0);
}

function removeCtxMenu() {
  if (_ctxMenu && _ctxMenu.parentNode) _ctxMenu.parentNode.removeChild(_ctxMenu);
  _ctxMenu = null;
}

function pickColor(name, color) {
  _colors[name] = color;
  renderAll();
  if (window.onColorChanged) window.onColorChanged(name, color);
}

function getColors() { return JSON.stringify(_colors); }
function getOrder()  { return JSON.stringify(_order); }
</script>
</body>
</html>
"""


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


# ── GaugeView — WebView-based gauge renderer ──────────────────────────────────

class GaugeView:
    def __init__(self, parent, on_order_changed, on_color_changed):
        self._wv = wx.html2.WebView.New(parent)
        self.widget = self._wv  # expose for sizer
        self._on_order_changed = on_order_changed
        self._on_color_changed = on_color_changed
        self._ready    = False
        self._order    = []
        self._colors   = {}
        self._values   = {}
        self._is_logging = False
        self._pending_params = None

        self._wv.SetPage(GAUGE_HTML, "")
        self._wv.Bind(wx.html2.EVT_WEBVIEW_LOADED, self._on_loaded)

    def _run(self, script):
        try:
            ok, result = self._wv.RunScript(script)
            if not ok:
                print(f"RunScript failed: {result!r}", flush=True)
            return ok, result
        except Exception as e:
            print(f"RunScript exception: {e}", flush=True)
            return False, ""

    def _on_loaded(self, _):
        if self._ready:
            return
        self._ready = True
        print("WebView loaded", flush=True)
        self._run("""
            window.onOrderChanged = function(order) {
                window.wx_order_changed = JSON.stringify(order);
            };
            window.onColorChanged = function(name, color) {
                window.wx_color_changed = JSON.stringify({name: name, color: color});
            };
        """)
        if self._pending_params is not None:
            order, colors = self._pending_params
            self._pending_params = None
            self.set_params(order, colors)

    def set_params(self, order, colors):
        self._order  = list(order)
        self._colors = dict(colors)
        print(f"set_params: ready={self._ready} order={order}", flush=True)
        if not self._ready:
            self._pending_params = (order, colors)
            return
        self._pending_params = None
        self._run(f"setParams({json.dumps(order)}, {json.dumps(colors)});")

    def update(self, by_name, is_logging):
        if not self._ready:
            return
        self._is_logging = is_logging
        filtered = {n: by_name[n] for n in self._order if n in by_name}
        self._run(f"update({json.dumps(filtered)}, {'true' if is_logging else 'false'});")
        self._poll_js_changes()

    def _poll_js_changes(self):
        ok, val = self._run("window.wx_order_changed || ''")
        if ok and val and val not in ("''", ""):
            self._run("window.wx_order_changed = null;")
            try:
                order = json.loads(val)
                self._order = order
                self._on_order_changed(order)
            except Exception:
                pass

        ok, val = self._run("window.wx_color_changed || ''")
        if ok and val and val not in ("''", ""):
            self._run("window.wx_color_changed = null;")
            try:
                data = json.loads(val)
                self._colors[data["name"]] = data["color"]
                self._on_color_changed(data["name"], data["color"])
            except Exception:
                pass

    def get_order(self):
        return list(self._order)

    def get_colors(self):
        return dict(self._colors)


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

        # ── Gauge WebView ─────────────────────────────────────────────────────
        self._gauge = GaugeView(self, self._on_order_changed, self._on_color_changed)
        root.Add(self._gauge.widget, 1, wx.EXPAND | wx.ALL, 10)

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
            self._prefs["order"] = [n for n in (self._prefs.get("order") or []) if n in self._selected]
            self._apply_selection()
            _save_prefs(self._prefs)
        dlg.Destroy()

    def _apply_selection(self):
        saved_order = self._prefs.get("order") or []
        ordered = [n for n in saved_order if n in self._selected]
        for n in self._selected:
            if n not in ordered:
                ordered.append(n)
        self._gauge.set_params(ordered, self._prefs.get("colors", {}))

    def _initial_apply(self):
        saved = set(self._prefs.get("selected") or [])
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
            wx.CallLater(1500, self._initial_apply)

        is_logging = by_name.get("isLogging", "False") == "True"
        self._set_status("Connected — polling", CLR_GREEN)
        log_label = "● LOGGING" if is_logging else ""
        if log_label != self._logging_text.GetLabel():
            self._logging_text.SetLabel(log_label)
        self._gauge.update(by_name, is_logging)

    # ── JS callbacks → prefs ──────────────────────────────────────────────────

    def _on_order_changed(self, order):
        self._prefs["order"] = order
        self._prefs["selected"] = list(self._selected)
        self._prefs["colors"] = self._gauge.get_colors()
        _save_prefs(self._prefs)

    def _on_color_changed(self, name, color):
        colors = self._gauge.get_colors()
        self._prefs["colors"] = colors
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
