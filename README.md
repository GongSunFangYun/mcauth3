# mcauth3 - Minecraft Microsoft Authentication

[![GitHub release](https://img.shields.io/github/v/release/GongSunFangYun/mcauth3?style=flat-square)]()
[![Downloads](https://img.shields.io/github/downloads/GongSunFangYun/mcauth3/total?style=flat-square)]()
[![Stars](https://img.shields.io/github/stars/GongSunFangYun/mcauth3?style=flat-square)]()
[![Forks](https://img.shields.io/github/forks/GongSunFangYun/mcauth3?style=flat-square)]()
[![Issues](https://img.shields.io/github/issues/GongSunFangYun/mcauth3?style=flat-square)]()
[![License](https://img.shields.io/github/license/GongSunFangYun/mcauth3?style=flat-square)]()

A minimalist Python library for Minecraft Microsoft account authentication that provides a clean, focused API for developers.

## Features

### 1. Single Dependency
Only requires `requests` library - no unnecessary dependencies that complicate deployment or conflict with existing environments.

### 2. Clean API Design
Three small methods cover the entire authentication lifecycle:
- `start_auth()` - Start the authentication process (get a device code)
- `finish_auth()` - Complete the authentication process
- `refresh_auth()` - Refresh an existing session without any user interaction

You can also plug in your own Azure App ID with `MCMSA(client_id=...)` — see below.

### 3. Full OAuth2 Device Flow Support
Implements Microsoft's OAuth2 device code flow, allowing authentication without exposing credentials in client applications.

### 4. Complete Minecraft Integration
Handles the entire chain: Microsoft → Xbox Live → XSTS → Minecraft Services → Player Profile.

## Installation

```bash
pip install mcauth3
```

## Using Your Own Azure App ID

By default `MCMSA()` uses mcauth3's built-in client ID, so you can authenticate with zero setup. If you would rather run the flow under your own Azure application — for example to see sign-ins under your own app in the Azure portal, or to apply your own app-level policies — override it in the constructor:

1. Register an application in the [Azure portal](https://portal.azure.com) → **App registrations**
2. Under **Authentication**, enable **"Allow public client flows"** (the device-code flow is a public client flow)
3. Pass the **Application (client) ID** to `MCMSA()`:

```python
from mcauth3 import MCMSA

auth = MCMSA(client_id="your-azure-application-id")
```

Every request in the flow will now use *your* client ID.

## Quick Start

```python
from mcauth3 import MCMSA

# Initialize the authenticator (uses mcauth3's built-in client ID by default)
auth = MCMSA()

# 1. Start authentication - get device code
device_info = auth.start_auth()
print(f"Visit: {device_info['verification_uri']}")
print(f"Code: {device_info['user_code']}")

# 2. After user verifies, finish authentication
result = auth.finish_auth(device_info)

# 3. Use the authentication result
print(f"Player: {result['profile']['name']}")
print(f"Access Token: {result['tokens']['minecraft_access_token']}")

# 4. Keep the refresh token so you can re-authenticate silently later
#    (see "Long-Term Authentication" below)
refresh_token = result['tokens']['microsoft_refresh_token']
```

## API Reference

### `MCMSA` Class

The main class that handles Minecraft Microsoft authentication.

#### Constructor
```python
from mcauth3 import MCMSA

# Create an authenticator using mcauth3's built-in client ID
authenticator = MCMSA()

# Override with your own Azure application (client) ID
authenticator = MCMSA(client_id="your-azure-app-id")
```
- **Parameters**:
  - `client_id` (str, optional): Your Azure application (client) ID. Defaults to mcauth3's built-in client ID. Pass your own value to run the flow under your own Azure App registration.
- **Returns**: `MCMSA` instance
- **Note**: Each instance maintains its own HTTP session with a 30-second timeout.

### Core Methods

#### `start_auth()`
Initiates the authentication process by requesting a device code from Microsoft.

```python
device_data = authenticator.start_auth()
```

**Returns**:
```json
{
    "user_code": "ABCDEFGH",   
    "device_code": "device_code_string", 
    "verification_uri": "https://www.microsoft.com/link",
    "expires_in": 900,           
    "interval": 5,           
    "message": "To sign in..."     
}
```

**Usage Example**:
```python
device_data = authenticator.start_auth()
print(f"Please visit: {device_data['verification_uri']}")
print(f"And enter code: {device_data['user_code']}")
```

#### `finish_auth(device_code_data)`
Completes the authentication process using the device code data obtained from `start_auth()`.

```python
result = authenticator.finish_auth(device_data)
```

**Parameters**:
- `device_code_data` (dict): The dictionary returned by `start_auth()`

**Returns**:
```json
{
    "tokens": {
        "microsoft_access_token": "eyJ...",    
        "microsoft_refresh_token": "0.A...",   
        "xbl_token": "eyJ...",            
        "xsts_token": "eyJ...",   
        "minecraft_access_token": "eyJ...",    
        "expires_in": 86400    
    },
    "profile": {
        "id": "1234567890abcdef1234567890abcdef", 
        "name": "PlayerName",           
        "skins": [                  
            {
                "id": "skin-id",
                "state": "ACTIVE",
                "url": "https://...",
                "variant": "CLASSIC"
            }
        ],
        "capes": [                         
            {
                "id": "cape-id",
                "state": "ACTIVE",
                "url": "https://...",
                "alias": "MIGRATOR"
            }
        ]
    }
}
```

#### `refresh_auth(refresh_token)`
Refreshes an existing Microsoft session and re-runs the full auth chain — no device-code flow or user interaction needed. Use this for long-term authentication once you have a stored refresh token.

```python
result = authenticator.refresh_auth("0.A...refresh_token")
```

**Parameters**:
- `refresh_token` (str): The `microsoft_refresh_token` obtained from a previous `finish_auth()` or `refresh_auth()` call

**Returns**: The same `{"tokens": ..., "profile": ...}` structure as `finish_auth()`.

**Usage Example**:
```python
# First, get a refresh token via the device-code flow
device_data = authenticator.start_auth()
result = authenticator.finish_auth(device_data)
refresh_token = result['tokens']['microsoft_refresh_token']

# Later, refresh without any user interaction
refreshed = authenticator.refresh_auth(refresh_token)
print(f"Player: {refreshed['profile']['name']}")
```

**Note**: Microsoft rotates refresh tokens — the refreshed result always carries the latest one, so store `result['tokens']['microsoft_refresh_token']` again after each refresh.

## Complete Usage Example

```python
from mcauth3 import MCMSA
import json

# 1. Initialize the authenticator
auth = MCMSA()

# 2. Get device code for user verification
print("=== Minecraft Authentication ===")
device_info = auth.start_auth()

print(f"\n1. Open your browser and go to:")
print(f"   {device_info['verification_uri']}")
print(f"\n2. Enter this code:")
print(f"   {device_info['user_code']}")
print(f"\n3. The code expires in {device_info['expires_in']//60} minutes")

# 3. Wait for user to complete verification
input("\nPress Enter after you've completed the verification in your browser...")

# 4. Complete authentication
try:
    result = auth.finish_auth(device_info)
    
    # 5. Use the authentication result
    print(f"\n[!] Authentication Successful!")
    print(f"   Player: {result['profile']['name']}")
    print(f"   UUID: {result['profile']['id']}")
    
    # Save tokens for later use
    with open('auth_tokens.json', 'w') as f:
        json.dump(result['tokens'], f, indent=2)
    
    # Save profile information
    with open('player_profile.json', 'w') as f:
        json.dump(result['profile'], f, indent=2)
        
except Exception as e:
    print(f"\n[X] Authentication failed: {e}")
```

## Long-Term Authentication (Refresh Tokens)

`finish_auth()` hands you a `microsoft_refresh_token` that stays valid for months. Store it somewhere safe, then call `refresh_auth()` later to get a brand-new session with **no user interaction** — ideal for launchers and long-running services.

One thing to remember: Microsoft **rotates** refresh tokens, so always save the token returned by the *latest* call.

```python
from mcauth3 import MCMSA
import json
import os

TOKEN_FILE = "auth_tokens.json"
auth = MCMSA()


def load_refresh_token():
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE) as f:
            return json.load(f).get("refresh_token")
    return None


def save_refresh_token(token):
    with open(TOKEN_FILE, "w") as f:
        json.dump({"refresh_token": token}, f)


# First run — full device-code flow, then keep the refresh token
if load_refresh_token() is None:
    device = auth.start_auth()
    print(f"Visit {device['verification_uri']} and enter code {device['user_code']}")
    result = auth.finish_auth(device)
    save_refresh_token(result["tokens"]["microsoft_refresh_token"])
    print(f"Signed in as {result['profile']['name']}")

# Later runs — silent refresh, no user interaction
else:
    result = auth.refresh_auth(load_refresh_token())
    save_refresh_token(result["tokens"]["microsoft_refresh_token"])  # rotation!
    print(f"Session refreshed for {result['profile']['name']}")
```

If the stored token is revoked or expires, `refresh_auth()` raises `OAuthError` — catch it and fall back to a fresh `start_auth()` / `finish_auth()`.

## Practical Examples

### Basic Script with Error Handling
```python
from mcauth3 import MCMSA
import time

auth = MCMSA()

try:
    # Start authentication
    device_data = auth.start_auth()
    
    print(f"Verification URL: {device_data['verification_uri']}")
    print(f"User Code: {device_data['user_code']}")
    
    # Give user time to verify (60 seconds)
    print("Waiting for verification... (60 seconds)")
    time.sleep(60)
    
    # Finish authentication
    result = auth.finish_auth(device_data)
    
    # Access specific data
    access_token = result['tokens']['minecraft_access_token']
    player_name = result['profile']['name']
    player_uuid = result['profile']['id']
    
    print(f"Success! Player: {player_name}, UUID: {player_uuid}")
    
except Exception as e:
    print(f"Authentication error: {e}")
```

### Web Application Integration (Flask Example)
```python
from flask import Flask, jsonify, request
from mcauth3 import MCMSA
import time

app = Flask(__name__)
auth_sessions = {}

@app.route('/api/auth/start', methods=['POST'])
def start_auth():
    auth = MCMSA()
    device_data = auth.start_auth()
    
    # Store session
    session_id = request.json.get('session_id')
    auth_sessions[session_id] = {
        'authenticator': auth,
        'device_data': device_data,
        'timestamp': time.time()
    }
    
    return jsonify({
        'verification_uri': device_data['verification_uri'],
        'user_code': device_data['user_code'],
        'session_id': session_id
    })

@app.route('/api/auth/finish', methods=['POST'])
def finish_auth():
    session_id = request.json.get('session_id')
    session = auth_sessions.get(session_id)
    
    if not session:
        return jsonify({'error': 'Session not found'}), 404
    
    try:
        result = session['authenticator'].finish_auth(session['device_data'])
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/auth/refresh', methods=['POST'])
def refresh_auth():
    # Silent re-authentication: the client sends its stored refresh token and
    # gets a brand-new session back without any user interaction.
    refresh_token = request.json.get('refresh_token')
    if not refresh_token:
        return jsonify({'error': 'refresh_token required'}), 400
    try:
        result = MCMSA().refresh_auth(refresh_token)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 401
```

## Error Handling

The library defines a small exception hierarchy (all subclasses of `mcauth3.MCAuthError`):

- **`MCAuthError`** — base class for all library-specific errors
- **`OAuthError`** — the OAuth token endpoint rejected the request (user denied the device code, the code expired, the refresh token is invalid, ...)
- **`AuthTimeoutError`** — the user did not finish verification within the device-code lifetime
- **`XboxAuthError`** — the Xbox Live / XSTS chain failed (no Xbox profile, banned account, underage account, ...)

Network-level failures still surface as `requests.exceptions.RequestException`, which is *not* a subclass of `MCAuthError`.

```python
from mcauth3 import MCMSA, OAuthError, AuthTimeoutError, XboxAuthError

try:
    auth = MCMSA()
    device_info = auth.start_auth()
    result = auth.finish_auth(device_info)
except AuthTimeoutError as e:
    print(f"User did not verify in time: {e}")
except OAuthError as e:
    print(f"User denied or code expired: {e}")
except XboxAuthError as e:
    print(f"Xbox issue: {e}")
except Exception as e:
    print(f"Other error (e.g. network): {e}")
```

## Best Practices

1. **Reuse Instances**: Create one `MCMSA` instance per authentication session and reuse it
2. **Timing**: Call `finish_auth()` within 15 minutes of `start_auth()` (device code expiry)
3. **Error Handling**: Always wrap authentication calls in try-except blocks
4. **User Instructions**: Provide clear, step-by-step instructions for the verification process
5. **Token Storage**: Securely store `microsoft_refresh_token` and use `refresh_auth()` for long-term authentication without repeating the device-code flow
6. **Refresh Rotation**: Microsoft rotates refresh tokens — always store the `microsoft_refresh_token` returned by the *latest* `finish_auth()` / `refresh_auth()` call

## Requirements

- Python 3.10+
- requests>=2.28.0

## License

MIT License

## Source Code

Available at: https://github.com/GongSunFangYun/mcauth3

## Support

For issues, questions, or contributions, please visit the GitHub repository.
