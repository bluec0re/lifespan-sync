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
import random

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
STEP_SETTLE_COUNT = 5  # Consecutive identical readings needed to "settle"
STEP_STATE_FILE = os.path.join(os.path.dirname(__file__), "step_accumulator.json")
STEP_STATE_MAX_AGE_SEC = 6 * 3600  # 6 hours

STOPPED_STATES = ["Idling", "Summary", "Edit", "Safe Key Loss", "Error"]
ACTIVE_STATES = ["Run", "Pause"]


class MetricBase:
    """Base class for UI metrics wrappers."""
    def __init__(self, name, default_value=""):
        self.name = name
        self._raw = default_value
        self.var = ctk.StringVar()
        self.update_ui()

    @property
    def value(self):
        return self._raw

    def update(self, value):
        self._raw = value
        self.update_ui()

    def get_display_string(self):
        return f"{self.name}: {self._raw}"

    def update_ui(self):
        self.var.set(self.get_display_string())

class UnitMetric(MetricBase):
    def __init__(self, name, default_value=0.0, unit="", is_float=False):
        self._unit = unit
        self._is_float = is_float
        super().__init__(name, default_value)

    @property
    def unit(self):
        return self._unit

    def update(self, value, unit=None):
        if value is not None:
            self._raw = value
        if unit is not None:
            self._unit = unit
        self.update_ui()

    def get_display_string(self):
        if self._is_float and isinstance(self._raw, (int, float)):
            return f"{self.name}: {self._raw:.2f} {self._unit}"
        return f"{self.name}: {self._raw} {self._unit}"

class AvgStepMetric(UnitMetric):
    def get_display_string(self):
        if isinstance(self._raw, (int, float)):
            return f"{self.name}: {self._raw:.1f} {self._unit}"
        return f"{self.name}: {self._raw} {self._unit}"

class StepsMetric(MetricBase):
    def __init__(self, name, default_value=0):
        self._base = 0
        super().__init__(name, default_value)

    def update(self, total, effective_base=0):
        self._raw = total
        self._base = effective_base
        self.update_ui()

    def get_display_string(self):
        if self._base > 0:
            return f"{self.name}: {self._raw} (base: {self._base})"
        return f"{self.name}: {self._raw}"

class MissingStepsMetric(MetricBase):
    def __init__(self, name, default_value=0):
        self._goal = 0
        self._initial = 0
        super().__init__(name, default_value)

    def update(self, missing, goal, initial):
        self._raw = missing
        self._goal = goal
        self._initial = initial
        self.update_ui()

    def get_display_string(self):
        return f"{self.name}: {self._raw} (Goal: {self._goal}, Initial: {self._initial})"


class StepAccumulator:
    """Tracks cumulative steps across treadmill pause/resume cycles.

    The treadmill resets its step counter to 0 on each resume. This class
    accumulates the step count from previous segments so the total is
    preserved. A debounce mechanism requires STEP_SETTLE_COUNT consecutive
    identical readings while paused before locking in the value, filtering
    out jitter at state transition boundaries.

    State is persisted to disk so it survives app crashes.
    """

    def __init__(self, state_file=STEP_STATE_FILE, settle_count=STEP_SETTLE_COUNT,
                 max_age_sec=STEP_STATE_MAX_AGE_SEC):
        self.settle_count = settle_count
        self._state_file = state_file
        self._max_age_sec = max_age_sec
        self._base = 0          # Sum of steps from completed segments
        self._raw = 0           # Latest raw reading from treadmill
        self._settled = 0       # Debounced step count locked during pause
        self._candidate = None  # Current candidate for settling
        self._streak = 0        # Consecutive identical readings
        self._is_settled = False
        self._load()

    @property
    def total(self):
        """Accumulated total steps (base from prior segments + current raw)."""
        return self._base + self._raw

    @property
    def base(self):
        """Steps accumulated from completed (pre-pause) segments."""
        return self._base

    @property
    def settled(self):
        """Debounced step count locked during the current pause."""
        return self._settled

    def update(self, raw_value, is_paused):
        """Feed a new raw step reading. Returns the accumulated total."""
        self._raw = max(0, int(raw_value))
        if is_paused and not self._is_settled:
            if self._raw == self._candidate:
                self._streak += 1
                if self._streak >= self.settle_count:
                    self._settled = self._raw
                    self._is_settled = True
                    print(f"Pause steps settled at {self._raw}")
                    self._save()
            else:
                self._candidate = self._raw
                self._streak = 1
        return self.total

    def on_resume(self):
        """Call when transitioning from Pause -> Run."""
        self._base += self._settled
        self._raw = 0
        self._settled = 0
        self._candidate = None
        self._streak = 0
        self._is_settled = False
        print(f"Resuming: accumulated step base now {self._base}")
        self._save()

    def clear(self):
        """Reset all state and remove the persisted file."""
        self._base = 0
        self._raw = 0
        self._settled = 0
        self._candidate = None
        self._streak = 0
        self._is_settled = False
        try:
            if os.path.exists(self._state_file):
                os.remove(self._state_file)
                print("Step accumulator state cleared.")
        except Exception as e:
            print(f"Failed to remove step state file: {e}")

    def _save(self):
        """Persist base and settled to disk."""
        try:
            data = {
                "base": self._base,
                "settled": self._settled,
                "timestamp": datetime.datetime.now().isoformat(),
            }
            with open(self._state_file, "w") as f:
                json.dump(data, f)
            print(f"Step accumulator state saved: {data}")
        except Exception as e:
            print(f"Failed to save step state: {e}")

    def _load(self):
        """Load persisted state, discarding if older than max_age_sec."""
        try:
            if not os.path.exists(self._state_file):
                return
            with open(self._state_file, "r") as f:
                data = json.load(f)
            saved_time = datetime.datetime.fromisoformat(data["timestamp"])
            age = (datetime.datetime.now() - saved_time).total_seconds()
            if age > self._max_age_sec:
                print(f"Step accumulator state too old ({age:.0f}s > {self._max_age_sec}s), discarding.")
                os.remove(self._state_file)
                return
            self._base = data.get("base", 0)
            self._settled = data.get("settled", 0)
            if self._settled > 0:
                self._is_settled = True
            print(f"Step accumulator state loaded: base={self._base}, settled={self._settled}, age={age:.0f}s")
        except Exception as e:
            print(f"Failed to load step state: {e}")

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
            hover_color="#338833",
        )
        self.default_speed_btn.pack(side="left", padx=10, pady=10, expand=True)

        self.target_speed = None  # Tracked from `metrics` upon first connect
        self.step_goal = None
        self.fitbit_steps = None
        self.goal_reached_notified = False

        # Step accumulation across pause/resume cycles
        self.step_accumulator = StepAccumulator()

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
            "steps": StepsMetric("Steps", 0),
            "distance": UnitMetric("Distance", 0.00, self.dist_unit, is_float=True),
            "speed": UnitMetric("Speed", 0.00, self.speed_unit, is_float=True),
            "time": MetricBase("Time", "0:00:00"),
            "calories": MetricBase("Calories", 0),
            "state": MetricBase("State", "Unknown"),
            "weight": UnitMetric("Weight", self.initial_weight, "kg", is_float=False),
            "missing_steps": MissingStepsMetric("Missing Steps", 0),
            "eta": MetricBase("ETA", "N/A"),
            "avg_step_length": AvgStepMetric("Avg Step Length", 0.0, "cm", is_float=True),
        }

        # Labels
        ctk.CTkLabel(
            self.metrics_frame,
            textvariable=self.metrics["steps"].var,
            font=("Helvetica", 24),
        ).grid(row=0, column=0, pady=10, padx=10)
        ctk.CTkLabel(
            self.metrics_frame,
            textvariable=self.metrics["distance"].var,
            font=("Helvetica", 24),
        ).grid(row=0, column=1, pady=10, padx=10)
        ctk.CTkLabel(
            self.metrics_frame,
            textvariable=self.metrics["speed"].var,
            font=("Helvetica", 24),
        ).grid(row=1, column=0, pady=10, padx=10)
        ctk.CTkLabel(
            self.metrics_frame,
            textvariable=self.metrics["time"].var,
            font=("Helvetica", 24),
        ).grid(row=1, column=1, pady=10, padx=10)
        ctk.CTkLabel(
            self.metrics_frame,
            textvariable=self.metrics["calories"].var,
            font=("Helvetica", 24),
        ).grid(row=2, column=0, pady=10, padx=10)
        ctk.CTkLabel(
            self.metrics_frame,
            textvariable=self.metrics["state"].var,
            font=("Helvetica", 24),
        ).grid(row=2, column=1, pady=10, padx=10)
        ctk.CTkLabel(
            self.metrics_frame,
            textvariable=self.metrics["weight"].var,
            font=("Helvetica", 24),
        ).grid(row=3, column=0, pady=10, padx=10)
        ctk.CTkLabel(
            self.metrics_frame,
            textvariable=self.metrics["avg_step_length"].var,
            font=("Helvetica", 24),
        ).grid(row=3, column=1, pady=10, padx=10)
        ctk.CTkLabel(
            self.metrics_frame,
            textvariable=self.metrics["missing_steps"].var,
            font=("Helvetica", 24),
        ).grid(row=4, column=0, columnspan=2, pady=10, padx=10)
        ctk.CTkLabel(
            self.metrics_frame,
            textvariable=self.metrics["eta"].var,
            font=("Helvetica", 24),
        ).grid(row=5, column=0, columnspan=2, pady=10, padx=10)

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
        # Estimate taskbar height around 40px, widget size 280x85
        x = screen_width - 290
        y = screen_height - 125
        self.widget.geometry(f"280x85+{x}+{y}")

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

            client_id = config.get("google_client_id") or config.get("fitbit_client_id")
            client_secret = config.get("google_client_secret") or config.get("fitbit_client_secret")
            step_goal = config.get("step_goal")

            # Requires google_health_tokens.json to exist from a prior run, or will wait for auth
            self.fitbit_client = FitbitClient(
                client_id, client_secret, step_goal=step_goal
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
                self.after(
                    0,
                    lambda: self.metrics["weight"].update(self.initial_weight, "kg"),
                )

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

                def _update_missing_steps():
                    steps = self.metrics['steps'].value
                    missing = self.step_goal - (steps + self.fitbit_steps)
                    self.metrics["missing_steps"].update(
                        missing, self.step_goal, self.fitbit_steps
                    )
                    self._update_eta()

                self.after(0, _update_missing_steps)

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
                    dist_val = self.metrics["distance"].value
                    speed_val = self.metrics["speed"].value

                    # Update label to show appropriate kg/lbs
                    weight_val = self.metrics["weight"].value
                    weight_unit_str = "kg" if value == "metric" else "lbs"

                    self.metrics["distance"].update(dist_val, self.dist_unit)
                    self.metrics["speed"].update(speed_val, self.speed_unit)
                    self.metrics["weight"].update(weight_val, weight_unit_str)
                except Exception:
                    pass
                self._update_avg_step_length()
                self._update_window_title()
            elif key in self.metrics:
                if key == "calories":
                    self.metrics[key].update(int(value))
                elif key == "steps":
                    current_state = self.metrics["state"].value
                    is_paused = current_state == "Pause"
                    total_steps = self.step_accumulator.update(value, is_paused)
                    # Show effective base: prior segments + settled value from current pause
                    effective_base = self.step_accumulator.base + self.step_accumulator.settled
                    self.metrics[key].update(total_steps, effective_base)
                    if self.fitbit_steps is not None and self.step_goal is not None:
                        missing_steps = self.step_goal - (total_steps + self.fitbit_steps)
                        self.metrics["missing_steps"].update(
                            missing_steps, self.step_goal, self.fitbit_steps
                        )
                    self._update_eta()
                    self._update_avg_step_length()
                elif key == "distance":
                    self.metrics[key].update(float(value), self.dist_unit)
                    self._update_avg_step_length()
                elif key == "speed":
                    self.metrics[key].update(float(value), self.speed_unit)
                    # Initialize target speed on first connect directly from treadmill values
                    if self.target_speed is None:
                        self.target_speed = float(value)
                elif key == "time":
                    self.metrics[key].update(value)
                    self._update_eta()
                elif key == "state":
                    old_state = self.metrics[key].value
                    self.metrics[key].update(value)

                    # Transition from Pause -> Run: save steps before counter resets
                    # This also handles the case where the app starts while already paused
                    if old_state == "Pause" and value == "Run":
                        self.step_accumulator.on_resume()

                    # Transition from Run -> Stopped cleanly logs steps
                    if old_state in ACTIVE_STATES and value in STOPPED_STATES:
                        self._trigger_fitbit_sync()

                self._update_window_title()

        # Schedule the UI update on the main Tkinter loop
        self.after(0, _update)

    def _update_eta(self):
        if self.step_goal is None or self.fitbit_steps is None:
            return

        try:
            steps = self.metrics["steps"].value
            time_str = self.metrics["time"].value

            parts = time_str.split(":")
            if len(parts) == 3:
                seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            else:
                seconds = 0

            missing_steps = self.step_goal - (steps + self.fitbit_steps)
            if missing_steps > 0:
                self.goal_reached_notified = False

            if steps > 0 and seconds > 0 and missing_steps > 0:
                steps_per_sec = steps / seconds
                eta_seconds = int(missing_steps / steps_per_sec)

                eta_td = datetime.timedelta(seconds=eta_seconds)
                finish_time = datetime.datetime.now() + eta_td

                self.metrics["eta"].update(f"{eta_td} ({finish_time.strftime('%H:%M:%S')})")
            elif missing_steps <= 0:
                self.metrics["eta"].update("Goal Reached!")
                self._play_goal_reached_animation()
            else:
                self.metrics["eta"].update("N/A")

        except Exception as e:
            pass

    def _play_goal_reached_animation(self):
        if getattr(self, "goal_reached_notified", False):
            return
        self.goal_reached_notified = True

        anim_win = tk.Toplevel(self)
        anim_win.overrideredirect(True)
        anim_win.attributes("-topmost", True)
        
        trans_color = "#000001"
        try:
            anim_win.attributes("-transparentcolor", trans_color)
        except Exception:
            pass
        anim_win.config(bg=trans_color)
        
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        anim_win.geometry(f"{screen_width}x{screen_height}+0+0")
        
        canvas = tk.Canvas(anim_win, bg=trans_color, highlightthickness=0)
        canvas.pack(fill="both", expand=True)

        colors = ["#f2b900", "#ff4a4a", "#00d288", "#00a2ff", "#ff00dd", "#ffffff"]
        confetti = []
        for _ in range(250):
            cx = random.randint(0, screen_width)
            cy = random.randint(-screen_height, 0)
            size = random.randint(8, 16)
            color = random.choice(colors)
            if random.random() > 0.5:
                item = canvas.create_rectangle(cx, cy, cx+size, cy+size, fill=color, outline="")
            else:
                item = canvas.create_oval(cx, cy, cx+size, cy+size, fill=color, outline="")
            
            confetti.append({
                "item": item, 
                "y": cy, 
                "vy": random.uniform(6, 12),
                "vx": random.uniform(-2, 2)
            })

        # Add text drop shadow
        canvas.create_text(
            screen_width // 2 + 3, screen_height // 2 + 3,
            text="GOAL REACHED!",
            font=("Helvetica", 64, "bold"),
            fill="#000000",
            justify="center"
        )
        # Add main text
        canvas.create_text(
            screen_width // 2, screen_height // 2,
            text="GOAL REACHED!",
            font=("Helvetica", 64, "bold"),
            fill="#00ff00",
            justify="center"
        )

        def animate():
            if not anim_win.winfo_exists():
                return
            try:
                active_particles = False
                for c in confetti:
                    canvas.move(c["item"], c["vx"], c["vy"])
                    c["y"] += c["vy"]
                    if c["y"] < screen_height:
                        active_particles = True
                if active_particles:
                    self.after(20, animate)
                else:
                    anim_win.destroy()
            except Exception:
                pass

        animate()

    def _update_avg_step_length(self):
        try:
            steps = self.metrics["steps"].value
            dist = self.metrics["distance"].value
            
            unit = "cm" if self.unit_system == "metric" else "in"
            
            if steps > 0 and dist > 0:
                if self.unit_system == "metric":
                    # dist is km, want cm (1 km = 100,000 cm)
                    avg = (dist * 100000) / steps
                else:
                    # dist is miles, want inches (1 mile = 63,360 inches)
                    avg = (dist * 63360) / steps
                self.metrics["avg_step_length"].update(avg, unit)
            else:
                self.metrics["avg_step_length"].update(0.0, unit)
        except Exception:
            pass

    def _update_window_title(self):
        try:
            steps = str(self.metrics["steps"].value)
            dist_val = self.metrics["distance"].value
            dist = f"{dist_val:.2f} {self.dist_unit}"
            speed = f"{self.metrics['speed'].value:.2f} {self.speed_unit}"
            time = getattr(self, "metrics", {}).get("time").value if getattr(self, "metrics", {}).get("time") else "0:00:00"
            state = self.metrics["state"].value

            # Format display string
            display_str = f"TR1200 | {time} | {speed} | {dist} | {steps} steps"

            # 1. Update Window Title (Taskbar)
            if state in ACTIVE_STATES or (
                state in STOPPED_STATES and dist_val > 0
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
                eta_str = self.metrics["eta"].value
                missing_val = self.metrics["missing_steps"].value
                    
                text_to_show = f"{time}\n{speed} | {dist} | {steps} steps\n{missing_val} left | ETA: {eta_str}"

                if state in ACTIVE_STATES:
                    self.widget_label.configure(
                        text=text_to_show, fg="#00ff00"
                    )
                elif state in STOPPED_STATES and dist_val > 0:
                    self.widget_label.configure(
                        text=text_to_show, fg="#ffaa00"
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
            steps_str = self.metrics["steps"].value
            dist_str = self.metrics["distance"].value
            time_str = self.metrics["time"].value

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

        def _sync_and_clear():
            calories = self.metrics["calories"].value if "calories" in self.metrics else None
            success = self.fitbit_client.log_treadmill_activity(
                steps, dist_km, ms, calories_kcal=calories
            )
            if success:
                self.step_accumulator.clear()

        threading.Thread(
            target=_sync_and_clear,
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
            self.target_speed = float(self.metrics["speed"].value)

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
            self.target_speed = float(self.metrics["speed"].value)

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
                print("Blocking exit to sync to Google Health...")
                calories = self.metrics["calories"].value if "calories" in self.metrics else None
                success = self.fitbit_client.log_treadmill_activity(
                    steps, dist_km, ms, calories_kcal=calories
                )
                if success:
                    self.step_accumulator.clear()
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
