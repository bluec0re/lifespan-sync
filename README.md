# Lifespan TR1200 Dashboard

A Python-based dashboard for connecting to and controlling a Lifespan TR1200 treadmill via Bluetooth Low Energy (BLE), with automatic Google Health API (Fitbit) activity syncing.

## Features
- **Live Metrics**: View steps, distance, speed, time, and calories in real time.
- **Treadmill Control**: Start, stop, and adjust the speed of your treadmill directly from the dashboard.
- **Google Health Integration**: Automatically logs your walking sessions to the [Google Health API](https://developers.google.com/health) when the treadmill stops.
- **Taskbar Widgets**:
  - Live stats appear right in the Windows Taskbar title.
  - Floating transparent mini-widget that always stays on top.
  - System Tray icon for quick access.

## Prerequisites

1. **Python 3.9+**
2. A Bluetooth-enabled computer (Windows 10/11 recommended).
3. A Google Cloud Project with the [Google Health API](https://developers.google.com/health) enabled and OAuth 2.0 credentials configured (`http://127.0.0.1:8080/` authorized redirect URI).

## Installation

1. Create a virtual environment:
   ```bash
   python -m venv .venv
   ```

2. Activate the virtual environment:
   - **Windows:** `.venv\Scripts\activate`
   - **Mac/Linux:** `source .venv/bin/activate`

3. Install the required dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Configuration

To enable Google Health syncing, you need to create a `config.json` file in the root directory.

1. Set up a project and OAuth 2.0 Client ID in the Google Cloud Console following the [Google Health API Getting Started Guide](https://developers.google.com/health).
2. Create a file named `config.json` with your credentials:
   ```json
   {
       "google_client_id": "YOUR_GOOGLE_CLIENT_ID",
       "google_client_secret": "YOUR_GOOGLE_CLIENT_SECRET",
       "step_goal": 10000
   }
   ```
   *(Legacy keys `fitbit_client_id` and `fitbit_client_secret` are also accepted for backwards compatibility.)*
*Note: The first time you run the app, it will open a browser window asking you to authorize the app with your Google account. It will then generate a `google_health_tokens.json` file to save your session.*

## Running the App

### Standard Run
You can run the app directly from your terminal:
```bash
python app.py
```

### Windows Shortcut Launcher
A `run.bat` file is included to easily launch the application without leaving a background command prompt window open. You can double-click this or create a shortcut to it on your desktop.

## License

This project is licensed under the MIT License - see the `LICENSE` file for details.
