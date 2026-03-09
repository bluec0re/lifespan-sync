import tkinter as tk
import tkinter.messagebox as messagebox
import traceback
import customtkinter as ctk
import asyncio
import threading
from treadmill_client import TreadmillClient
from fitbit_client import FitbitClient
import datetime
import pystray
from PIL import Image, ImageDraw
import json
import os

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

# Constants
MAX_SPEED_METRIC = 6.4
MAX_SPEED_IMPERIAL = 4.0
MIN_SPEED_METRIC = 0.6
MIN_SPEED_IMPERIAL = 0.4
BTN_DEFAULT_SPEED_METRIC = 2.0
BTN_DEFAULT_SPEED_IMPERIAL = 1.2
SPEED_INCREMENT = 0.1
MIN_STEPS_TO_SYNC = 10
POLL_INTERVAL_SEC = 2.0
AUTO_CONNECT_DELAY_MS = 500

STOPPED_STATES = ["Idling", "Summary", "Edit", "Safe Key Loss", "Error"]
ACTIVE_STATES = ["Run", "Pause"]


class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Lifespan TR1200 Dashboard")
        self.geometry("800x500")

        self.initial_weight = 70
        try:
            config_path = os.path.join(os.path.dirname(__file__), "config.json")
            if os.path.exists(config_path):
                with open(config_path, "r") as f:
                    config = json.load(f)
                    if "initial_weight" in config:
                        self.initial_weight = config["initial_weight"]
        except Exception as e:
            print(f"Could not load initial weight from config: {e}")

        self.treadmill = TreadmillClient(
            update_callback=self._on_metric_update, initial_weight=self.initial_weight
        )
        self.loop = asyncio.new_event_loop()

        self.fitbit_client = None
        # Start Fitbit Auth in background to not freeze UI
        threading.Thread(target=self._init_fitbit, daemon=True).start()

        # Auto-connect on start
        self.after(AUTO_CONNECT_DELAY_MS, self.connect_treadmill)

        # Connection status
        self.status_label = ctk.CTkLabel(
            self, text="Status: Disconnected", font=("Helvetica", 16)
        )
        self.status_label.pack(pady=10)

        self.connect_btn = ctk.CTkButton(
            self, text="Connect to Treadmill", command=self.connect_treadmill
        )
        self.connect_btn.pack(pady=10)

        # Controls Frame
        self.controls_frame = ctk.CTkFrame(self)
        self.controls_frame.pack(pady=10, padx=20, fill="x")

        self.start_btn = ctk.CTkButton(
            self.controls_frame,
            text="Start",
            command=self.start_treadmill,
            state="disabled",
        )
        self.start_btn.pack(side="left", padx=10, pady=10, expand=True)

        self.stop_btn = ctk.CTkButton(
            self.controls_frame,
            text="Stop",
            command=self.stop_treadmill,
            state="disabled",
        )
        self.stop_btn.pack(side="left", padx=10, pady=10, expand=True)

        self.speed_down_btn = ctk.CTkButton(
            self.controls_frame,
            text="Speed -",
            command=self.decrease_speed,
            state="disabled",
        )
        self.speed_down_btn.pack(side="left", padx=10, pady=10, expand=True)

        self.speed_up_btn = ctk.CTkButton(
            self.controls_frame,
            text="Speed +",
            command=self.increase_speed,
            state="disabled",
        )
        self.speed_up_btn.pack(side="left", padx=10, pady=10, expand=True)

        self.default_speed_btn = ctk.CTkButton(
            self.controls_frame,
            text="Default Target (2.0/1.2)",
            command=self.set_default_speed,
            state="disabled",
            fg_color="#44aa44",
            hover_color="#338833"
        )
        self.default_speed_btn.pack(side="left", padx=10, pady=10, expand=True)

        self.target_speed = None  # Tracked from `metrics` upon first connect
        self.step_goal = None
        self.fitbit_steps = None

        self.unit_system = "metric"
        self.dist_unit = "km"
        self.speed_unit = "km/h"

        # Metrics Frame
        self.metrics_frame = ctk.CTkFrame(self)
        self.metrics_frame.pack(pady=20, padx=20, fill="both", expand=True)

        # Columns inside metrics
        self.metrics_frame.grid_columnconfigure((0, 1), weight=1)

        # Variables
        self.metrics = {
            "steps": ctk.StringVar(value="Steps: 0"),
            "distance": ctk.StringVar(value=f"Distance: 0.00 {self.dist_unit}"),
            "speed": ctk.StringVar(value=f"Speed: 0.00 {self.speed_unit}"),
            "time": ctk.StringVar(value="Time: 0:00:00"),
            "calories": ctk.StringVar(value="Calories: 0"),
            "state": ctk.StringVar(value="State: Unknown"),
            "weight": ctk.StringVar(value=f"Weight: {self.initial_weight} kg"), # Initial weight
            "missing_steps": ctk.StringVar(value="Missing Steps: 0 (Goal: 0, Initial: 0)"),
        }

        # Labels
        ctk.CTkLabel(
            self.metrics_frame,
            textvariable=self.metrics["steps"],
            font=("Helvetica", 24),
        ).grid(row=0, column=0, pady=10, padx=10)
        ctk.CTkLabel(
            self.metrics_frame,
            textvariable=self.metrics["distance"],
            font=("Helvetica", 24),
        ).grid(row=0, column=1, pady=10, padx=10)
        ctk.CTkLabel(
            self.metrics_frame,
            textvariable=self.metrics["speed"],
            font=("Helvetica", 24),
        ).grid(row=1, column=0, pady=10, padx=10)
        ctk.CTkLabel(
            self.metrics_frame,
            textvariable=self.metrics["time"],
            font=("Helvetica", 24),
        ).grid(row=1, column=1, pady=10, padx=10)
        ctk.CTkLabel(
            self.metrics_frame,
            textvariable=self.metrics["calories"],
            font=("Helvetica", 24),
        ).grid(row=2, column=0, pady=10, padx=10)
        ctk.CTkLabel(
            self.metrics_frame,
            textvariable=self.metrics["state"],
            font=("Helvetica", 24),
        ).grid(row=2, column=1, pady=10, padx=10)
        ctk.CTkLabel(
            self.metrics_frame,
            textvariable=self.metrics["weight"],
            font=("Helvetica", 24),
        ).grid(row=3, column=0, columnspan=2, pady=10, padx=10)
        ctk.CTkLabel(
            self.metrics_frame,
            textvariable=self.metrics["missing_steps"],
            font=("Helvetica", 24),
        ).grid(row=4, column=0, columnspan=2, pady=10, padx=10)

        # Start BLE event loop thread
        self.ble_thread = threading.Thread(target=self._start_loop, daemon=True)
        self.ble_thread.start()

        # Floating Widget Setup
        self.widget = None
        self._create_floating_widget()

        # System Tray Setup
        self.tray_icon = None
        self._setup_tray_icon()

    def _create_floating_widget(self):
        self.widget = tk.Toplevel(self)
        self.widget.title("Treadmill Widget")
        self.widget.overrideredirect(True)  # Remove title bar
        self.widget.attributes("-topmost", True)  # Keep on top
        self.widget.configure(bg="#1a1a1a")  # Dark background to match app

        # Position in bottom right corner
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        # Estimate taskbar height around 40px, widget size 250x60
        x = screen_width - 260
        y = screen_height - 100
        self.widget.geometry(f"250x60+{x}+{y}")

        self.widget_label = tk.Label(
            self.widget,
            text="Treadmill: Disconnected",
            font=("Helvetica", 10, "bold"),
            bg="#1a1a1a",
            fg="white",
            justify="center",
        )
        self.widget_label.pack(expand=True, fill="both", padx=10, pady=5)

        # Allow moving the widget by dragging
        self.widget.bind("<ButtonPress-1>", self._start_move_widget)
        self.widget.bind("<B1-Motion>", self._do_move_widget)

    def _start_move_widget(self, event):
        self._widget_x = event.x
        self._widget_y = event.y

    def _do_move_widget(self, event):
        x = self.widget.winfo_x() - self._widget_x + event.x
        y = self.widget.winfo_y() - self._widget_y + event.y
        self.widget.geometry(f"+{x}+{y}")

    def _setup_tray_icon(self):
        # Create a simple icon image
        image = Image.new("RGB", (64, 64), color=(0, 100, 200))
        d = ImageDraw.Draw(image)
        d.text((10, 25), "TR", fill=(255, 255, 255))

        menu = pystray.Menu(
            pystray.MenuItem("Show App", self._show_app_from_tray),
            pystray.MenuItem("Exit", self._exit_from_tray),
        )

        self.tray_icon = pystray.Icon(
            "treadmill_icon", image, "Treadmill Dashboard", menu
        )
        # Start tray in a separate thread so it doesn't block tkinter
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def _show_app_from_tray(self, icon, item):
        # The callback is called from the tray thread, so we must schedule the UI update
        self.after(0, self.deiconify)
        self.after(0, self.lift)

    def _exit_from_tray(self, icon, item):
        # Schedule closing on the main thread
        self.after(0, self.on_closing)

    def _init_fitbit(self):
        try:
            # Load config
            config_path = os.path.join(os.path.dirname(__file__), "config.json")
            with open(config_path, "r") as f:
                config = json.load(f)

            # Requires fitbit_tokens.json to exist from a prior run, or will hang waiting for auth
            self.fitbit_client = FitbitClient(
                config["fitbit_client_id"], config["fitbit_client_secret"]
            )

            # Retrieve current weight from Fitbit if possible
            fitbit_weight = self.fitbit_client.get_weight()
            if fitbit_weight is not None:
                # Fitbit returns floats, treadmill protocol wants integer
                weight_int = int(fitbit_weight)
                print(
                    f"Updating treadmill initial weight to Fitbit weight: {weight_int}"
                )
                self.initial_weight = weight_int
                self.treadmill.initial_weight = weight_int
                
                # Update UI
                self.after(0, lambda: self.metrics["weight"].set(f"Weight: {self.initial_weight} kg"))

                # If treadmill already connected (since fitbit auth is in background), set it right away
                if (
                    hasattr(self.treadmill, "is_connected")
                    and self.treadmill.is_connected
                ):
                    asyncio.run_coroutine_threadsafe(
                        self.treadmill.set_weight(weight_int), self.loop
                    )

            # Retrieve current steps and step goal from Fitbit if possible
            steps_and_goal = self.fitbit_client.get_steps_and_goal()
            if steps_and_goal is not None:
                self.fitbit_steps, self.step_goal = steps_and_goal
                self.metrics["missing_steps"].set(f"Missing Steps: {self.step_goal - (int(self.metrics['steps'].get().split(': ')[1]) + self.fitbit_steps)} (Goal: {self.step_goal}, Initial: {self.fitbit_steps})")

        except Exception as e:
            print(f"Fitbit init skipped or failed: {e}")

    def _start_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def _on_metric_update(self, key, value):
        # Update UI from thread-safe callback
        def _update():
            if key == "status":
                self.status_label.configure(text=f"Status: {value.capitalize()}")
                if value in ["disconnected", "failed"]:
                    self._trigger_fitbit_sync()
                    self.connect_btn.configure(
                        state="normal", text="Connect to Treadmill"
                    )
                    self.start_btn.configure(state="disabled")
                    self.stop_btn.configure(state="disabled")
                    self.speed_up_btn.configure(state="disabled")
                    self.speed_down_btn.configure(state="disabled")
                elif value == "connected":
                    self.start_btn.configure(state="normal")
                    self.stop_btn.configure(state="normal")
                    self.speed_up_btn.configure(state="normal")
                    self.speed_down_btn.configure(state="normal")
                    self.default_speed_btn.configure(state="normal")
            elif key == "unit":
                if value == "metric":
                    self.unit_system = "metric"
                    self.dist_unit = "km"
                    self.speed_unit = "km/h"
                elif value == "imperial":
                    self.unit_system = "imperial"
                    self.dist_unit = "mi"
                    self.speed_unit = "mi/h"
                try:
                    dist_val = (
                        self.metrics["distance"].get().split(": ")[1].split(" ")[0]
                    )
                    speed_val = self.metrics["speed"].get().split(": ")[1].split(" ")[0]
                    
                    # Update label to show appropriate kg/lbs
                    weight_val = self.metrics["weight"].get().split(": ")[1].split(" ")[0]
                    weight_unit_str = "kg" if value == "metric" else "lbs"

                    self.metrics["distance"].set(
                        f"Distance: {dist_val} {self.dist_unit}"
                    )
                    self.metrics["speed"].set(f"Speed: {speed_val} {self.speed_unit}")
                    self.metrics["weight"].set(f"Weight: {weight_val} {weight_unit_str}")
                except Exception:
                    pass
                self._update_window_title()
            elif key in self.metrics:
                if key == "calories":
                    int_value = int(value)
                    self.metrics[key].set(f"{key.capitalize()}: {int_value}")
                elif key == "steps":
                    int_value = int(value)
                    self.metrics[key].set(f"{key.capitalize()}: {int_value}")
                    if self.fitbit_steps is not None and self.step_goal is not None:
                        missing_steps = self.step_goal - (int_value + self.fitbit_steps)
                        self.metrics["missing_steps"].set(f"Missing Steps: {missing_steps} (Goal: {self.step_goal}, Initial: {self.fitbit_steps})")
                elif key == "distance":
                    self.metrics[key].set(
                        f"{key.capitalize()}: {value:.2f} {self.dist_unit}"
                    )
                elif key == "speed":
                    self.metrics[key].set(
                        f"{key.capitalize()}: {value:.2f} {self.speed_unit}"
                    )
                    # Initialize target speed on first connect directly from treadmill values
                    if self.target_speed is None:
                        self.target_speed = value
                elif key == "time":
                    self.metrics[key].set(f"{key.capitalize()}: {value}")
                elif key == "state":
                    old_state = self.metrics["state"].get().split(": ")[1]
                    self.metrics[key].set(f"{key.capitalize()}: {value}")

                    # Transition from Run -> Stopped cleanly logs steps
                    if old_state in ACTIVE_STATES and value in STOPPED_STATES:
                        self._trigger_fitbit_sync()

                self._update_window_title()

        # Schedule the UI update on the main Tkinter loop
        self.after(0, _update)

    def _update_window_title(self):
        try:
            steps = self.metrics["steps"].get().split(": ")[1]
            dist = self.metrics["distance"].get().split(": ")[1]
            speed = self.metrics["speed"].get().split(": ")[1]
            time = (
                getattr(self, "metrics", {}).get("time").get().split(": ")[1]
                if getattr(self, "metrics", {}).get("time")
                else "0:00:00"
            )
            state = self.metrics["state"].get().split(": ")[1]

            # Format display string
            display_str = f"TR1200 | {time} | {speed} | {dist} | {steps} steps"

            # 1. Update Window Title (Taskbar)
            if state in ACTIVE_STATES or (
                state in STOPPED_STATES and float(dist.split(" ")[0]) > 0
            ):
                self.title(display_str)
            else:
                self.title("Lifespan TR1200 Dashboard")
                display_str = "Lifespan TR1200 Dashboard"

            # 2. Update Hover Text for Tray Icon
            if self.tray_icon:
                try:
                    self.tray_icon.title = display_str[
                        :64
                    ]  # Windows limits tooltip length
                except Exception:
                    pass

            # 3. Update Floating Widget Text
            if self.widget and self.widget.winfo_exists():
                if state in ACTIVE_STATES:
                    self.widget_label.configure(
                        text=f"{time}\n{speed} | {dist} | {steps} steps", fg="#00ff00"
                    )
                elif state in STOPPED_STATES and float(dist.split(" ")[0]) > 0:
                    self.widget_label.configure(
                        text=f"{time}\n{speed} | {dist} | {steps} steps", fg="#ffaa00"
                    )
                else:
                    self.widget_label.configure(
                        text="Treadmill: Disconnected", fg="white"
                    )

        except Exception as e:
            print(f"Error updating widget displays: {e}")
            self.title("Lifespan TR1200 Dashboard")

    def connect_treadmill(self):
        self.status_label.configure(text="Status: Connecting...")
        self.connect_btn.configure(state="disabled", text="Connecting...")

        async def _connect_task():
            try:
                await self.treadmill.connect()
                self._on_metric_update("status", "connected")
                # start polling
                await self.treadmill.start_polling(interval=POLL_INTERVAL_SEC)
            except Exception as e:
                print(f"Connection failed: {e}")
                traceback.print_exception(e)
                self._on_metric_update("status", "failed")

        asyncio.run_coroutine_threadsafe(_connect_task(), self.loop)

    def start_treadmill(self):
        asyncio.run_coroutine_threadsafe(self.treadmill.start_treadmill(), self.loop)

    def stop_treadmill(self):
        asyncio.run_coroutine_threadsafe(self.treadmill.stop_treadmill(), self.loop)

    def _get_unsynced_workout(self):
        """Returns (steps, dist_km, ms) if there is a valid, unsynced workout, otherwise None"""
        if not self.fitbit_client or not self.fitbit_client.client:
            return None

        try:
            steps_str = self.metrics["steps"].get().split(": ")[1]
            dist_str = (
                self.metrics["distance"]
                .get()
                .split(": ")[1]
                .replace(f" {self.dist_unit}", "")
            )
            time_str = self.metrics["time"].get().split(": ")[1]

            steps = int(steps_str)
            if steps <= MIN_STEPS_TO_SYNC:
                return None

            dist_km = float(dist_str)

            # parse "H:M:S" to pure milliseconds
            parts = time_str.split(":")
            if len(parts) == 3:
                ms = (int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])) * 1000
            else:
                ms = 0

            sync_key = (steps, dist_km, ms)
            if getattr(self, "last_sync_key", None) == sync_key:
                return None

            if steps > MIN_STEPS_TO_SYNC and ms > 0:
                return (steps, dist_km, ms, sync_key, time_str)

        except Exception as e:
            print(f"Error parsing metrics for Fitbit sync parsing: {e}")

        return None

    def _trigger_fitbit_sync(self):
        workout = self._get_unsynced_workout()
        if not workout:
            print("No valid unsynced workout data to sync.")
            return

        steps, dist_km, ms, sync_key, time_str = workout
        self.last_sync_key = sync_key
        print(f"Syncing workout to Fitbit ({steps} steps over {time_str})")
        threading.Thread(
            target=self.fitbit_client.log_treadmill_activity,
            args=(steps, dist_km, ms),
            daemon=True,
        ).start()

    def set_default_speed(self):
        default_speed = (
            BTN_DEFAULT_SPEED_IMPERIAL
            if getattr(self, "unit_system", "metric") == "imperial"
            else BTN_DEFAULT_SPEED_METRIC
        )
        self.target_speed = default_speed
        print(f"Setting default speed to {self.target_speed:.1f} {self.speed_unit}")
        asyncio.run_coroutine_threadsafe(
            self.treadmill.set_speed(self.target_speed), self.loop
        )

    def increase_speed(self):
        max_speed = (
            MAX_SPEED_IMPERIAL
            if getattr(self, "unit_system", "metric") == "imperial"
            else MAX_SPEED_METRIC
        )
        if self.target_speed is None:
            self.target_speed = float(self.metrics["speed"].get().split(": ")[1].split(" ")[0])
            
        self.target_speed = min(max_speed, self.target_speed + SPEED_INCREMENT)
        print(f"Setting speed to {self.target_speed:.1f} {self.speed_unit}")
        asyncio.run_coroutine_threadsafe(
            self.treadmill.set_speed(self.target_speed), self.loop
        )

    def decrease_speed(self):
        min_speed = (
            MIN_SPEED_IMPERIAL
            if getattr(self, "unit_system", "metric") == "imperial"
            else MIN_SPEED_METRIC
        )
        if self.target_speed is None:
            self.target_speed = float(self.metrics["speed"].get().split(": ")[1].split(" ")[0])
            
        self.target_speed = max(min_speed, self.target_speed - SPEED_INCREMENT)
        print(f"Setting speed to {self.target_speed:.1f} {self.speed_unit}")
        asyncio.run_coroutine_threadsafe(
            self.treadmill.set_speed(self.target_speed), self.loop
        )

    def on_closing(self):
        # Check for unsynced data
        workout = self._get_unsynced_workout()
        if workout:
            steps, dist_km, ms, sync_key, time_str = workout
            should_sync = messagebox.askyesno(
                "Unsynced Workout",
                f"You have an unsynced workout of {steps} steps ({time_str}).\n\nWould you like to log this to Fitbit before closing?",
            )
            if should_sync:
                self.last_sync_key = sync_key
                print("Blocking exit to sync to Fitbit...")
                success = self.fitbit_client.log_treadmill_activity(steps, dist_km, ms)
                if success:
                    messagebox.showinfo(
                        "Success", "Workout successfully synced to Fitbit!"
                    )
                else:
                    messagebox.showerror(
                        "Error", "Failed to sync to Fitbit. Check console for details."
                    )

        # Disconnect gracefully
        async def _disconnect():
            await self.treadmill.disconnect()
            self.loop.call_soon_threadsafe(self.loop.stop)

        if self.treadmill.is_connected:
            asyncio.run_coroutine_threadsafe(_disconnect(), self.loop)
        else:
            self.loop.call_soon_threadsafe(self.loop.stop)

        if self.tray_icon:
            self.tray_icon.stop()

        self.destroy()


if __name__ == "__main__":
    app = App()
    app.protocol("WM_DELETE_WINDOW", app.on_closing)
    app.mainloop()
