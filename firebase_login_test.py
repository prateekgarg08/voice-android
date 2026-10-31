#!/usr/bin/env python3
"""Sign in with Firebase email/password and verify the local API without printing the token."""
import getpass
import json
import sys
import urllib.error
import urllib.parse
import urllib.request


def test_login(api_key, email, password, api_base="http://127.0.0.1:8000"):
    endpoint = "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=" + urllib.parse.quote(api_key, safe="")
    payload = json.dumps({"email": email, "password": password, "returnSecureToken": True}).encode()
    request = urllib.request.Request(endpoint, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        token = json.load(response)["idToken"]
    request = urllib.request.Request(api_base.rstrip("/") + "/v1/me", headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def main():
    try:
        api_key = input("Firebase Web API key: ").strip()
        email = input("Firebase user email: ").strip()
        password = getpass.getpass("Firebase user password: ")
        if not api_key or not email or not password:
            print("API key, email, and password are required.", file=sys.stderr)
            return 1
        result = test_login(api_key, email, password)
        print("Authenticated. Firebase UID:", result.get("uid", "(missing)"))
        return 0
    except (EOFError, KeyboardInterrupt):
        print("\nTest cancelled.", file=sys.stderr)
        return 1
    except urllib.error.HTTPError as exc:
        if exc.url.startswith('https://identitytoolkit.googleapis.com/'):
            print(f"Firebase sign-in failed (HTTP {exc.code}). Check the Web API key, sign-in provider, and user credentials.", file=sys.stderr)
        else:
            print(f"Agent API rejected the request (HTTP {exc.code}). Check the Firebase project, server credentials, and allowed UIDs.", file=sys.stderr)
        return 1
    except (urllib.error.URLError, TimeoutError):
        print("Could not reach Firebase or the agent API. Check your connection and ensure the API is running on port 8000.", file=sys.stderr)
        return 1
    except (KeyError, ValueError):
        print("Received an unexpected response from Firebase or the agent API.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
