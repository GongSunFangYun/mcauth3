# MCMSA - Minecraft Microsoft Authentication

A minimalist Python library for Minecraft Microsoft account authentication that provides a clean, focused API for developers.

## Features

### 1. Single Dependency
Only requires `requests` library - no unnecessary dependencies that complicate deployment or conflict with existing environments.

### 2. Clean API Design
Two simple methods cover the entire authentication flow:
- `start_auth()` - Start the authentication process
- `finish_auth()` - Complete the authentication process

### 3. Full OAuth2 Device Flow Support
Implements Microsoft's OAuth2 device code flow, allowing authentication without exposing credentials in client applications.

### 4. Complete Minecraft Integration
Handles the entire chain: Microsoft → Xbox Live → XSTS → Minecraft Services → Player Profile.

## Installation

```bash
pip install mcauth
```

## Quick Start

```python
from mcauth import MCMSA

# Initialize the authenticator
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
```

## API Reference

### `MCMSA` Class

The main class that handles Minecraft Microsoft authentication.

#### Constructor
```python
from mcauth import MCMSA

# Create an authenticator instance
authenticator = MCMSA()
```
- **Parameters**: None
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

## Complete Usage Example

```python
from mcauth import MCMSA
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

## Practical Examples

### Basic Script with Error Handling
```python
from mcauth import MCMSA
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
from mcauth import MCMSA
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
```

## Error Handling

The library may raise the following exceptions:

1. **Network Issues**: `requests.exceptions.RequestException`
2. **Invalid Device Code**: `Exception` with specific error message
3. **Authentication Timeout**: `Exception` if user doesn't verify within the timeout period
4. **Authentication Denied**: `Exception` if user denies permission

Example error handling:
```python
from mcauth import MCMSA
import requests

try:
    auth = MCMSA()
    device_info = auth.start_auth()
    result = auth.finish_auth(device_info)
    
except requests.exceptions.RequestException as e:
    print(f"Network error: {e}")
except Exception as e:
    print(f"Authentication error: {e}")
```

## Best Practices

1. **Reuse Instances**: Create one `MCMSA` instance per authentication session and reuse it
2. **Timing**: Call `finish_auth()` within 15 minutes of `start_auth()` (device code expiry)
3. **Error Handling**: Always wrap authentication calls in try-except blocks
4. **User Instructions**: Provide clear, step-by-step instructions for the verification process
5. **Token Storage**: Securely store `microsoft_refresh_token` if you need long-term authentication

## Requirements

- Python 3.7+
- requests>=2.28.0

## License

MIT License

## Source Code

Available at: https://github.com/GongSunFangYun/mcauth

## Support

For issues, questions, or contributions, please visit the GitHub repository.