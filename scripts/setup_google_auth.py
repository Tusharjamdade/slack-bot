#!/usr/bin/env python3
"""
Google Workspace OAuth Setup Script
Generates token.json with permissions for Google Calendar and Gmail.
Supports both desktop browsers and WSL / headless environments.
"""

import os
import sys
import json
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]

CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
TOKEN_FILE = os.getenv("GOOGLE_TOKEN_FILE", "token.json")


def main():
    print("==========================================================")
    print(" Google Workspace OAuth Setup (Calendar, Gmail)")
    print("==========================================================")

    if not os.path.exists(CREDENTIALS_FILE):
        print(f"\n❌ Error: '{CREDENTIALS_FILE}' not found!")
        print("\nTo generate credentials.json:")
        print("1. Go to Google Cloud Console: https://console.cloud.google.com/")
        print("2. Create a new project or select an existing one.")
        print("3. Enable APIs in 'APIs & Services' > 'Library':")
        print("   - Google Calendar API")
        print("   - Gmail API")
        print("4. Go to 'APIs & Services' > 'OAuth consent screen':")
        print("   - Choose 'External' (or 'Internal' for Workspace)")
        print("   - Add your email under 'Test users'")
        print("5. Go to 'APIs & Services' > 'Credentials':")
        print("   - Click 'Create Credentials' > 'OAuth client ID'")
        print("   - Application type: 'Desktop app'")
        print(f"6. Download the client JSON and save it as '{CREDENTIALS_FILE}' in the project root.")
        print("==========================================================")
        sys.exit(1)

    print(f"\nLoading OAuth secrets from '{CREDENTIALS_FILE}'...")
    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)

    try:
        # Try local server first (works on desktop and WSL with browser routing)
        creds = flow.run_local_server(port=0, open_browser=True)
    except Exception as e:
        print(f"\nBrowser launch notice: {e}")
        print("Falling back to console-based authorization...")
        try:
            creds = flow.run_console()
        except AttributeError:
            # Newer versions deprecated run_console; provide redirect URI instructions
            flow.redirect_uri = "urn:ietf:wg:oauth:2.0:oob"
            auth_url, _ = flow.authorization_url(prompt="consent")
            print("\nPlease visit this URL to authorize this application:")
            print(f"\n{auth_url}\n")
            code = input("Enter the authorization code: ").strip()
            flow.fetch_token(code=code)
            creds = flow.credentials

    # Save token.json
    with open(TOKEN_FILE, "w") as token:
        token.write(creds.to_json())

    print(f"\n Saved credentials to '{TOKEN_FILE}'!")
    print("\nFor Docker or CI/CD deployments without mounting files, you can also set these in .env:")
    print(f"GOOGLE_CLIENT_ID={creds.client_id}")
    print(f"GOOGLE_CLIENT_SECRET={creds.client_secret}")
    print(f"GOOGLE_REFRESH_TOKEN={creds.refresh_token}")
    print("==========================================================")


if __name__ == "__main__":
    main()
