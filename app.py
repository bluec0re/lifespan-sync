import tkinter as tk
import tkinter.messagebox as messagebox
import customtkinter as ctk
import asyncio
import threading
from treadmill_client import TreadmillClient
from fitbit_client import FitbitClient
import datetime

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        
        self.title("Lifespan TR1200 Dashboard")
        self.geometry("600x400")
        
        self.treadmill = TreadmillClient(update_callback=self._on_metric_update)
        self.loop = asyncio.new_event_loop()
        
        self.fitbit_client = None
        # Start Fitbit Auth in background to not freeze UI
        threading.Thread(target=self._init_fitbit, daemon=True).start()
        
        # Connection status
        self.status_label = ctk.CTkLabel(self, text="Status: Disconnected", font=("Helvetica", 16))
        self.status_label.pack(pady=10)
        
        self.connect_btn = ctk.CTkButton(self, text="Connect to Treadmill", command=self.connect_treadmill)
        self.connect_btn.pack(pady=10)
        
        # Controls Frame
        self.controls_frame = ctk.CTkFrame(self)
        self.controls_frame.pack(pady=10, padx=20, fill="x")
        
        self.start_btn = ctk.CTkButton(self.controls_frame, text="Start", command=self.start_treadmill, state="disabled")
        self.start_btn.pack(side="left", padx=10, pady=10, expand=True)
        
        self.stop_btn = ctk.CTkButton(self.controls_frame, text="Stop", command=self.stop_treadmill, state="disabled")
        self.stop_btn.pack(side="left", padx=10, pady=10, expand=True)

        self.speed_down_btn = ctk.CTkButton(self.controls_frame, text="Speed -", command=self.decrease_speed, state="disabled")
        self.speed_down_btn.pack(side="left", padx=10, pady=10, expand=True)

        self.speed_up_btn = ctk.CTkButton(self.controls_frame, text="Speed +", command=self.increase_speed, state="disabled")
        self.speed_up_btn.pack(side="left", padx=10, pady=10, expand=True)

        self.test_sync_btn = ctk.CTkButton(self.controls_frame, text="Test Sync", command=self.test_fitbit_sync)
        self.test_sync_btn.pack(side="left", padx=10, pady=10, expand=True)

        self.target_speed = 1.0 # default starting speed in km/h

        # Metrics Frame
        self.metrics_frame = ctk.CTkFrame(self)
        self.metrics_frame.pack(pady=20, padx=20, fill="both", expand=True)
        
        # Columns inside metrics
        self.metrics_frame.grid_columnconfigure((0, 1), weight=1)
        
        # Variables
        self.metrics = {
            "steps": ctk.StringVar(value="Steps: 0"),
            "distance": ctk.StringVar(value="Distance: 0.00 km"),
            "speed": ctk.StringVar(value="Speed: 0.00 km/h"),
            "time": ctk.StringVar(value="Time: 0:00:00"),
            "calories": ctk.StringVar(value="Calories: 0"),
            "state": ctk.StringVar(value="State: Unknown")
        }
        
        # Labels
        ctk.CTkLabel(self.metrics_frame, textvariable=self.metrics["steps"], font=("Helvetica", 24)).grid(row=0, column=0, pady=10, padx=10)
        ctk.CTkLabel(self.metrics_frame, textvariable=self.metrics["distance"], font=("Helvetica", 24)).grid(row=0, column=1, pady=10, padx=10)
        ctk.CTkLabel(self.metrics_frame, textvariable=self.metrics["speed"], font=("Helvetica", 24)).grid(row=1, column=0, pady=10, padx=10)
        ctk.CTkLabel(self.metrics_frame, textvariable=self.metrics["time"], font=("Helvetica", 24)).grid(row=1, column=1, pady=10, padx=10)
        ctk.CTkLabel(self.metrics_frame, textvariable=self.metrics["calories"], font=("Helvetica", 24)).grid(row=2, column=0, pady=10, padx=10)
        ctk.CTkLabel(self.metrics_frame, textvariable=self.metrics["state"], font=("Helvetica", 24)).grid(row=2, column=1, pady=10, padx=10)
        
        # Start BLE event loop thread
        self.ble_thread = threading.Thread(target=self._start_loop, daemon=True)
        self.ble_thread.start()

    def _init_fitbit(self):
        try:
            # Requires fitbit_tokens.json to exist from a prior run, or will hang waiting for auth
            self.fitbit_client = FitbitClient("23TY9D", "c1226d3f046f89708e82e1cca028ee6d") 
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
                    self.connect_btn.configure(state="normal", text="Connect to Treadmill")
                    self.start_btn.configure(state="disabled")
                    self.stop_btn.configure(state="disabled")
                    self.speed_up_btn.configure(state="disabled")
                    self.speed_down_btn.configure(state="disabled")
                    self.test_btn.configure(state="disabled")
                elif value == "connected":
                    self.start_btn.configure(state="normal")
                    self.stop_btn.configure(state="normal")
                    self.speed_up_btn.configure(state="normal")
                    self.speed_down_btn.configure(state="normal")
            elif key in self.metrics:
                if key == "steps" or key == "calories":
                    self.metrics[key].set(f"{key.capitalize()}: {int(value)}")
                elif key == "distance":
                    self.metrics[key].set(f"{key.capitalize()}: {value:.2f} km")
                elif key == "speed":
                    self.metrics[key].set(f"{key.capitalize()}: {value:.2f} km/h")
                elif key == "time":
                    self.metrics[key].set(f"{key.capitalize()}: {value}")
                elif key == "state":
                    old_state = self.metrics["state"].get().split(": ")[1]
                    self.metrics[key].set(f"{key.capitalize()}: {value}")
                    
                    # Transition from Running -> Stopped cleanly logs steps
                    if old_state == "Running" and value == "Stopped":
                        self._trigger_fitbit_sync()
                        
        # Schedule the UI update on the main Tkinter loop
        self.after(0, _update)

    def connect_treadmill(self):
        self.status_label.configure(text="Status: Connecting...")
        self.connect_btn.configure(state="disabled", text="Connecting...")
        
        async def _connect_task():
            try:
                await self.treadmill.connect()
                self._on_metric_update("status", "connected")
                # start polling
                await self.treadmill.start_polling(interval=2.0)
            except Exception as e:
                print(f"Connection failed: {e}")
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
            dist_str = self.metrics["distance"].get().split(": ")[1].replace(" km", "")
            time_str = self.metrics["time"].get().split(": ")[1]
            
            steps = int(steps_str)
            if steps <= 10:
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
                
            if steps > 10 and ms > 0:
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
        threading.Thread(target=self.fitbit_client.log_treadmill_activity, args=(steps, dist_km, ms), daemon=True).start()

    def increase_speed(self):
        self.target_speed = min(6.4, self.target_speed + 0.1)
        print(f"Setting speed to {self.target_speed} km/h")
        asyncio.run_coroutine_threadsafe(self.treadmill.set_speed(self.target_speed), self.loop)

    def decrease_speed(self):
        self.target_speed = max(0.4, self.target_speed - 0.1)
        print(f"Setting speed to {self.target_speed} km/h")
        asyncio.run_coroutine_threadsafe(self.treadmill.set_speed(self.target_speed), self.loop)

    def test_fitbit_sync(self):
        if not self.fitbit_client or not self.fitbit_client.client:
            print("Fitbit client not authenticated. Cannot test.")
            return
        
        # Log a dummy 15-minute 2000-step walk to see what the API trace says
        print("Triggering Manual API Test Sync...")
        threading.Thread(target=self.fitbit_client.log_treadmill_activity, args=(2000, 1.5, 900000), daemon=True).start()

    def on_closing(self):
        # Check for unsynced data
        workout = self._get_unsynced_workout()
        if workout:
            steps, dist_km, ms, sync_key, time_str = workout
            should_sync = messagebox.askyesno(
                "Unsynced Workout", 
                f"You have an unsynced workout of {steps} steps ({time_str}).\n\nWould you like to log this to Fitbit before closing?"
            )
            if should_sync:
                self.last_sync_key = sync_key
                print("Blocking exit to sync to Fitbit...")
                success = self.fitbit_client.log_treadmill_activity(steps, dist_km, ms)
                if success:
                    messagebox.showinfo("Success", "Workout successfully synced to Fitbit!")
                else:
                    messagebox.showerror("Error", "Failed to sync to Fitbit. Check console for details.")
    
        # Disconnect gracefully
        async def _disconnect():
            await self.treadmill.disconnect()
            self.loop.call_soon_threadsafe(self.loop.stop)
            
        if self.treadmill.is_connected:
             asyncio.run_coroutine_threadsafe(_disconnect(), self.loop)
        else:
             self.loop.call_soon_threadsafe(self.loop.stop)
             
        self.destroy()

if __name__ == "__main__":
    app = App()
    app.protocol("WM_DELETE_WINDOW", app.on_closing)
    app.mainloop()
