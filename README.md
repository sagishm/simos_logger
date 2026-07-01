# Simos Logger

Standalone VW ECU data logger with wxPython GUI and J2534 support.

## Features
- Modes: 22, 3E, HSL
- Interface: J2534 (OpenPort 2.0), SocketCAN, USB-ISOTP
- CSV logging with trigger conditions
- Live gauge display with color marking
- TCP stream on port 65432 for [logger_viewer.html](../VW_Flash-master/logger_viewer.html)

## Requirements
```
pip install -r requirements.txt
```

## Run
```
python logger_gui.py
```

## Files
- `logger_gui.py` — wxPython GUI
- `logger_core.py` — ECU communication and logging logic
- `logs/log_config.yaml` — FPS, trigger, HP/TQ calculation config
- `logs/csv/` — parameter definition CSV files
