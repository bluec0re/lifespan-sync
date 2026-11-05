import datetime
import json
import os
import threading
import time
import urllib.parse
import webbrowser
import cherrypy
from requests_oauthlib import OAuth2Session

# Allow OAuthlib to use HTTP for local testing and relax scope ordering differences from Google OAuth
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"

TOKEN_FILE = "google_health_tokens.json"

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_HEALTH_API_BASE = "https://health.googleapis.com/v4"

GOOGLE_HEALTH_SCOPES = [
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.writeonly",
    "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
    "https://www.googleapis.com/auth/googlehealth.profile.readonly",
    "https://www.googleapis.com/auth/googlehealth.settings.readonly",
]

DEFAULT_STEP_GOAL = 10000


class OAuth2Server:
    def __init__(self, client_id, client_secret):
        self.success_html = """
            <h1>You are now authorized with Google Health!</h1>
            <p>Please close this browser window and return to the application.</p>
            """
        self.failure_html = """
            <h1>ERROR: Authorization failed</h1>
            <p>Please close this browser window and return to the application.</p>
            """
        self.client_id = client_id
        self.client_secret = client_secret
        self.oauth = OAuth2Session(
            client_id,
            redirect_uri="http://127.0.0.1:8080/",
            scope=GOOGLE_HEALTH_SCOPES,
            pkce="S256",
        )
        self.auth_url_base = GOOGLE_AUTH_URL
        self.token_url = GOOGLE_TOKEN_URL
        self.token = None

    def get_authorization_url(self):
        url, _ = self.oauth.authorization_url(
            self.auth_url_base,
            access_type="offline",
            prompt="consent",
            include_granted_scopes="true",
        )
        return url

    def browser_authorize(self):
        url = self.get_authorization_url()
        print("Opening browser to authorize Google Health...")
        webbrowser.open(url)
        cherrypy.quickstart(self)

    @cherrypy.expose
    def index(self, state="", code=None, error=None, **params):
        if error or not code:
            threading.Timer(1, cherrypy.engine.exit).start()
            return self.failure_html

        try:
            kwargs = {
                "authorization_response": cherrypy.url()
                + "?"
                + cherrypy.request.query_string,
            }
            if self.client_secret:
                kwargs["client_secret"] = self.client_secret
            self.token = self.oauth.fetch_token(
                self.token_url,
                **kwargs,
            )
            threading.Timer(1, cherrypy.engine.exit).start()
            return self.success_html
        except Exception as e:
            print(f"Error fetching token: {e}")
            threading.Timer(1, cherrypy.engine.exit).start()
            return self.failure_html


class GoogleHealthClient:
    def __init__(self, client_id, client_secret, step_goal=DEFAULT_STEP_GOAL):
        self.client_id = client_id
        self.client_secret = client_secret
        self.step_goal = step_goal if step_goal is not None else DEFAULT_STEP_GOAL
        self.client = None
        self._tokens = None
        self._load_or_authorize()

    def _update_tokens(self, token):
        # Google's token refresh response often omits refresh_token; preserve the existing one
        if self._tokens and "refresh_token" in self._tokens and not token.get("refresh_token"):
            token["refresh_token"] = self._tokens["refresh_token"]
        self._tokens = dict(token)
        with open(TOKEN_FILE, "w") as f:
            json.dump(self._tokens, f)

    def _create_session(self, tokens):
        refresh_kwargs = {"client_id": self.client_id}
        if self.client_secret:
            refresh_kwargs["client_secret"] = self.client_secret
        return OAuth2Session(
            self.client_id,
            token=tokens,
            auto_refresh_url=GOOGLE_TOKEN_URL,
            auto_refresh_kwargs=refresh_kwargs,
            token_updater=self._update_tokens,
        )

    def _load_or_authorize(self):
        if os.path.exists(TOKEN_FILE):
            try:
                with open(TOKEN_FILE, "r") as f:
                    tokens = json.load(f)

                self._tokens = tokens
                session = self._create_session(tokens)

                expires_at = tokens.get("expires_at", 0)
                if time.time() > expires_at - 60:
                    print(
                        f"Token expired at {expires_at}. Refreshing Google OAuth token..."
                    )
                    refreshed = session.refresh_token(
                        GOOGLE_TOKEN_URL,
                        refresh_token=tokens.get("refresh_token"),
                        client_id=self.client_id,
                        client_secret=self.client_secret,
                    )
                    self._update_tokens(refreshed)
                    session = self._create_session(self._tokens)

                self.client = session
                print("Google Health client loaded via saved tokens.")
                return
            except Exception as e:
                print(
                    f"Saved tokens failed or expired without refresh: {e}. Re-authorizing..."
                )

        # Run OAuth 2.0 Web Server Flow
        server = OAuth2Server(self.client_id, self.client_secret)
        print(
            "\n\n################################################################################"
        )
        print("GOOGLE HEALTH AUTH REQUIRED.")
        url = server.get_authorization_url()
        print(f"Opening Browser to: {url}")
        print(
            "################################################################################\n\n"
        )
        webbrowser.open(url)
        cherrypy.quickstart(server)

        if server.token and server.token.get("access_token"):
            self._update_tokens(server.token)
            self.client = self._create_session(self._tokens)
            print("Successfully authorized and saved new Google Health tokens.")
        else:
            print("Authorization failed. Could not retrieve tokens.")

    def _request(self, method, path, **kwargs):
        if not self.client:
            raise RuntimeError("Google Health client is not authorized.")
        url = f"{GOOGLE_HEALTH_API_BASE}/{path.lstrip('/')}"
        response = self.client.request(method, url, **kwargs)
        if response.status_code == 401 and self._tokens and self._tokens.get("refresh_token"):
            print("Received 401 from Google Health API; attempting token refresh...")
            refreshed = self.client.refresh_token(
                GOOGLE_TOKEN_URL,
                refresh_token=self._tokens.get("refresh_token"),
                client_id=self.client_id,
                client_secret=self.client_secret,
            )
            self._update_tokens(refreshed)
            response = self.client.request(method, url, **kwargs)
        response.raise_for_status()
        if response.content:
            return response.json()
        return {}

    @staticmethod
    def _format_utc_and_offset(dt):
        local_dt = dt.astimezone()
        utc_dt = local_dt.astimezone(datetime.timezone.utc)
        offset_td = local_dt.utcoffset()
        offset_sec = int(offset_td.total_seconds()) if offset_td is not None else 0
        return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ"), f"{offset_sec}s"

    def log_treadmill_activity(self, steps, distance_km, duration_ms, calories_kcal=None):
        if not self.client:
            print("Cannot log to Google Health: Not authorized.")
            return False

        end_time_dt = datetime.datetime.now()
        start_time_dt = end_time_dt - datetime.timedelta(milliseconds=duration_ms)

        total_distance_mm = float(distance_km) * 1_000_000.0

        activities_to_log = []
        current_time = start_time_dt
        remaining_ms = duration_ms
        remaining_steps = int(steps)
        remaining_dist_mm = total_distance_mm
        remaining_calories = float(calories_kcal) if calories_kcal is not None else None

        while current_time.date() < end_time_dt.date():
            next_midnight = datetime.datetime.combine(
                current_time.date() + datetime.timedelta(days=1), datetime.time.min
            )
            duration_part_ms = (next_midnight - current_time).total_seconds() * 1000
            fraction = duration_part_ms / duration_ms if duration_ms > 0 else 0
            steps_part = int(steps * fraction)
            dist_part_mm = total_distance_mm * fraction
            cal_part = (
                float(calories_kcal) * fraction
                if calories_kcal is not None
                else None
            )

            activities_to_log.append(
                {
                    "start_dt": current_time,
                    "end_dt": next_midnight,
                    "steps": steps_part,
                    "distance_mm": dist_part_mm,
                    "calories_kcal": cal_part,
                }
            )

            current_time = next_midnight
            remaining_ms -= duration_part_ms
            remaining_steps -= steps_part
            remaining_dist_mm -= dist_part_mm
            if remaining_calories is not None and cal_part is not None:
                remaining_calories -= cal_part

        if remaining_ms > 0:
            activities_to_log.append(
                {
                    "start_dt": current_time,
                    "end_dt": end_time_dt,
                    "steps": max(0, remaining_steps),
                    "distance_mm": max(0.0, remaining_dist_mm),
                    "calories_kcal": (
                        max(0.0, remaining_calories)
                        if remaining_calories is not None
                        else None
                    ),
                }
            )

        success_all = True

        for idx, act in enumerate(activities_to_log):
            start_time_str, start_offset_str = self._format_utc_and_offset(
                act["start_dt"]
            )
            end_time_str, end_offset_str = self._format_utc_and_offset(act["end_dt"])

            metrics_summary = {
                "steps": str(int(act["steps"])),
                "distanceMillimeters": round(float(act["distance_mm"]), 2),
            }
            if act["calories_kcal"] is not None and act["calories_kcal"] > 0:
                metrics_summary["caloriesKcal"] = round(float(act["calories_kcal"]), 2)

            payload = {
                "exercise": {
                    "interval": {
                        "startTime": start_time_str,
                        "startUtcOffset": start_offset_str,
                        "endTime": end_time_str,
                        "endUtcOffset": end_offset_str,
                    },
                    "exerciseType": "WALKING",
                    "displayName": "Walking",
                    "metricsSummary": metrics_summary,
                }
            }

            print(
                f"Logging part {idx + 1}/{len(activities_to_log)}: {act['steps']} steps to Google Health "
                f"({start_time_str} -> {end_time_str})..."
            )
            try:
                response = self._request(
                    "POST",
                    "users/me/dataTypes/exercise/dataPoints",
                    json=payload,
                )
                print(f"Successfully logged to Google Health. Response: {response}")
            except Exception as e:
                print(f"Failed to log to Google Health: {e}")
                if hasattr(e, "response") and getattr(e, "response", None) is not None:
                    print(f"API Error Response: {e.response.text}")
                success_all = False

        return success_all

    def get_weight(self):
        if not self.client:
            print("Cannot get weight from Google Health: Not authorized.")
            return None
        print("Fetching user weight from Google Health...")
        try:
            end_utc = datetime.datetime.now(datetime.timezone.utc)
            start_utc = end_utc - datetime.timedelta(days=90)
            filter_str = (
                f'weight.sample_time.physical_time >= "{start_utc.strftime("%Y-%m-%dT%H:%M:%SZ")}" '
                f'AND weight.sample_time.physical_time < "{end_utc.strftime("%Y-%m-%dT%H:%M:%SZ")}"'
            )
            data = self._request(
                "GET",
                "users/me/dataTypes/weight/dataPoints:reconcile",
                params={"filter": filter_str},
            )
            points = (
                data.get("reconciledDataPoints")
                or data.get("dataPoints")
                or []
            )

            # Fallback to unfiltered list if no weight logged in the last 90 days
            if not points:
                data = self._request(
                    "GET",
                    "users/me/dataTypes/weight/dataPoints",
                )
                points = data.get("dataPoints") or []

            latest_weight_grams = None
            latest_time = ""
            for dp in points:
                weight_obj = dp.get("weight", {})
                grams = weight_obj.get("weightGrams")
                if grams is None:
                    continue
                sample_time = weight_obj.get("sampleTime", {})
                phys_time = sample_time.get("physicalTime") or dp.get("sampleTime") or ""
                if latest_weight_grams is None or phys_time >= latest_time:
                    latest_weight_grams = float(grams)
                    latest_time = phys_time

            if latest_weight_grams is not None and latest_weight_grams > 0:
                weight_kg = round(latest_weight_grams / 1000.0, 2)
                print(f"Successfully retrieved weight from Google Health: {weight_kg} kg")
                return weight_kg
            else:
                print("Weight not found in Google Health data points.")
                return None
        except Exception as e:
            print(f"Failed to fetch weight from Google Health: {e}")
            if hasattr(e, "response") and getattr(e, "response", None) is not None:
                print(f"API Error Response: {e.response.text}")
            return None

    def get_step_goal(self):
        # The Google Health API v4 does not expose daily step goals via server-side REST endpoints;
        # use the configured step_goal (defaulting to 10,000 steps).
        print(f"Using configured daily step goal: {self.step_goal}")
        return self.step_goal

    def get_current_steps(self):
        if not self.client:
            print("Cannot get current steps from Google Health: Not authorized.")
            return None
        print("Fetching current steps from Google Health...")
        try:
            today = datetime.date.today()
            tomorrow = today + datetime.timedelta(days=1)
            payload = {
                "range": {
                    "start": {
                        "date": {
                            "year": today.year,
                            "month": today.month,
                            "day": today.day,
                        }
                    },
                    "end": {
                        "date": {
                            "year": tomorrow.year,
                            "month": tomorrow.month,
                            "day": tomorrow.day,
                        }
                    },
                },
                "windowSizeDays": 1,
            }
            response = self._request(
                "POST",
                "users/me/dataTypes/steps/dataPoints:dailyRollUp",
                json=payload,
            )
            rollup_points = (
                response.get("rollupDataPoints")
                or response.get("dailyRollupDataPoints")
                or []
            )
            current_steps = 0
            for dp in rollup_points:
                steps_obj = dp.get("steps", {})
                count_sum = steps_obj.get("countSum")
                if count_sum is not None:
                    current_steps += int(count_sum)

            print(
                f"Successfully retrieved current steps from Google Health: {current_steps}"
            )
            return current_steps
        except Exception as e:
            print(f"Failed to fetch current steps from Google Health: {e}")
            if hasattr(e, "response") and getattr(e, "response", None) is not None:
                print(f"API Error Response: {e.response.text}")
            return None

    def get_steps_and_goal(self):
        if not self.client:
            print("Cannot get steps and goal from Google Health: Not authorized.")
            return None
        current_steps = self.get_current_steps()
        step_goal = self.get_step_goal()
        if current_steps is not None and step_goal is not None:
            return current_steps, step_goal
        return None


# Backwards-compatible alias
FitbitClient = GoogleHealthClient


if __name__ == "__main__":
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    client_id = None
    client_secret = None
    step_goal = None
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            cfg = json.load(f)
            client_id = cfg.get("google_client_id") or cfg.get("fitbit_client_id")
            client_secret = cfg.get("google_client_secret") or cfg.get("fitbit_client_secret")
            step_goal = cfg.get("step_goal")

    if not client_id:
        client_id = input("Enter your Google OAuth Client ID: ").strip()
    if not client_secret:
        client_secret = input("Enter your Google OAuth Client Secret: ").strip()

    client = GoogleHealthClient(client_id, client_secret, step_goal=step_goal)
    client.get_weight()
    client.get_steps_and_goal()
