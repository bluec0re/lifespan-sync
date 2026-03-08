import fitbit
import json
import os
import requests
import datetime
import time
from requests_oauthlib import OAuth2Session
import webbrowser
import cherrypy
import threading

# Allow OAuthlib to use HTTP for local testing
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

TOKEN_FILE = "fitbit_tokens.json"

class OAuth2Server:
    def __init__(self, client_id, client_secret):
        self.success_html = """
            <h1>You are now authorized!</h1>
            <p>Please close this browser window and return to the application.</p>
            """
        self.failure_html = """
            <h1>ERROR: Authorization failed</h1>
            <p>Please close this browser window and return to the application.</p>
            """
        self.client_id = client_id
        self.client_secret = client_secret
        self.fitbit = OAuth2Session(
            client_id,
            redirect_uri="http://127.0.0.1:8080/",
            scope=["activity"]
        )
        self.auth_url_base = "https://www.fitbit.com/oauth2/authorize"
        self.token_url = "https://api.fitbit.com/oauth2/token"
        self.access_token = None
        self.refresh_token = None
        self.expires_at = None

    def browser_authorize(self):
        url, _ = self.fitbit.authorization_url(self.auth_url_base)
        print("Opening browser to authorize Fitbit...")
        webbrowser.open(url)
        cherrypy.quickstart(self)

    @cherrypy.expose
    def index(self, state='', code=None, error=None):
        if error or not code:
            threading.Timer(1, cherrypy.engine.exit).start()
            return self.failure_html
        
        try:
            self.fitbit.fetch_token(
                self.token_url,
                client_secret=self.client_secret,
                authorization_response=cherrypy.url() + "?" + cherrypy.request.query_string
            )
            self.access_token = self.fitbit.token['access_token']
            self.refresh_token = self.fitbit.token['refresh_token']
            self.expires_at = self.fitbit.token['expires_at']
            threading.Timer(1, cherrypy.engine.exit).start()
            return self.success_html
        except Exception as e:
            print(f"Error fetching token: {e}")
            threading.Timer(1, cherrypy.engine.exit).start()
            return self.failure_html

class FitbitClient:
    def __init__(self, client_id, client_secret):
        self.client_id = client_id
        self.client_secret = client_secret
        self.client = None
        self._load_or_authorize()

    def _update_tokens(self, token):
        with open(TOKEN_FILE, 'w') as f:
            json.dump(token, f)

    def _load_or_authorize(self):
        if os.path.exists(TOKEN_FILE):
            try:
                with open(TOKEN_FILE, 'r') as f:
                    tokens = json.load(f)
                
                # Check expiration explicitly (expires_at is a unix timestamp in seconds)
                expires_at = tokens.get('expires_at', 0)
                if time.time() > expires_at:
                    print(f"Token expired at {expires_at}. Fitbit library will attempt automatic refresh.")
                    
                self.client = fitbit.Fitbit(
                    self.client_id, 
                    self.client_secret, 
                    access_token=tokens.get('access_token'), 
                    refresh_token=tokens.get('refresh_token'),
                    expires_at=expires_at,
                    refresh_cb=self._update_tokens
                )
                # Token loaded successfully
                print("Fitbit client loaded via saved tokens.")
                return
            except Exception as e:
                print(f"Saved tokens failed or expired without refresh: {e}. Re-authorizing...")

        # Run OAuth 2.0 Web Server Flow
        server = OAuth2Server(self.client_id, self.client_secret)
        print("\n\n################################################################################")
        print("FITBIT AUTH REQUIRED.")
        url, _ = server.fitbit.authorization_url(server.auth_url_base)
        print(f"Opening Browser to: {url}")
        print("################################################################################\n\n")
        webbrowser.open(url)
        cherrypy.quickstart(server)
        
        if server.access_token:
            tokens = {
                'access_token': server.access_token,
                'refresh_token': server.refresh_token,
                'expires_at': server.expires_at
            }
            self._update_tokens(tokens)
            self.client = fitbit.Fitbit(
                self.client_id, 
                self.client_secret, 
                access_token=server.access_token, 
                refresh_token=server.refresh_token,
                expires_at=server.expires_at,
                refresh_cb=self._update_tokens
            )
            print("Successfully authorized and saved new tokens.")
        else:
            print("Authorization failed. Could not retrieve tokens.")

    def log_treadmill_activity(self, steps, distance_km, duration_ms):
        if not self.client:
            print("Cannot log to Fitbit: Not authorized.")
            return False

        date_str = datetime.datetime.now().strftime('%Y-%m-%d')
        start_time_str = (datetime.datetime.now() - datetime.timedelta(milliseconds=duration_ms)).strftime('%H:%M:%S')
        
        # Fitbit requires using 'distanceUnit=steps' and pushing steps into the distance field to manually log daily steps
        activity_id = 90013 # Walking
        
        print(f"Logging {steps} steps to Fitbit (Walking activity)...")
        try:
            response = self.client.log_activity({
                "activityId": activity_id,
                "startTime": start_time_str,
                "durationMillis": int(duration_ms),
                "date": date_str,
                "distance": steps,
                "distanceUnit": "steps"
            })
            print(f"Successfully logged to Fitbit. Response: {response}")
            return True
        except fitbit.exceptions.HTTPBadRequest as e:
            print(f"Fitbit API rejected request (Bad Request 400): {e}")
            return False
        except Exception as e:
            print(f"Failed to log to Fitbit: {e}")
            if hasattr(e, 'response') and getattr(e, 'response', None):
                print(f"API Error Response: {e.response.text}")
            return False

if __name__ == "__main__":
    CLIENT_ID = input("Enter your Fitbit Client ID: ").strip()
    CLIENT_SECRET = input("Enter your Fitbit Client Secret: ").strip()
    client = FitbitClient(CLIENT_ID, CLIENT_SECRET)
