import time
import requests

CLIENT_ID = "4a07b708-b86d-4365-a55f-f4f23ecb85ab"
SCOPE = "XboxLive.signin offline_access openid profile email"

DEVICE_CODE_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode"
TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
XBL_AUTH_URL = "https://user.auth.xboxlive.com/user/authenticate"
XSTS_AUTH_URL = "https://xsts.auth.xboxlive.com/xsts/authorize"
MC_LOGIN_URL = "https://api.minecraftservices.com/authentication/login_with_xbox"
PROFILE_URL = "https://api.minecraftservices.com/minecraft/profile"

class MCMSA:
    def __init__(self):
        self.session = requests.Session()
        self.session.timeout = 30

    def start_auth(self):
        data = {
            "client_id": CLIENT_ID,
            "scope": SCOPE
        }

        headers = {
            "Content-Type": "application/x-www-form-urlencoded"
        }

        response = self.session.post(DEVICE_CODE_URL, data=data, headers=headers)
        response.raise_for_status()

        device_code_data = response.json()

        return device_code_data

    def poll_microsoft_token(self, device_code_data):

        device_code = device_code_data['device_code']
        poll_interval = max(device_code_data.get('interval', 5), 5)
        max_attempts = 180

        for attempt in range(max_attempts):
            try:
                data = {
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "client_id": CLIENT_ID,
                    "device_code": device_code
                }

                headers = {
                    "Content-Type": "application/x-www-form-urlencoded"
                }

                response = self.session.post(TOKEN_URL, data=data, headers=headers)

                if response.status_code == 200:
                    return response.json()
                else:
                    error_data = response.json()
                    if error_data.get('error') == 'authorization_pending':
                        time.sleep(poll_interval)
                        continue
                    else:
                        raise Exception(f"Token polling error: {error_data}")

            except Exception:
                if attempt == max_attempts - 1:
                    raise Exception("Authentication timeout - please try again")
                time.sleep(poll_interval)

        raise Exception("Authentication timeout - please try again")

    def auth_xbox_live(self, microsoft_access_token):
        attempts = [f"d={microsoft_access_token}", microsoft_access_token]

        for rps_ticket in attempts:
            try:
                request_body = {
                    "Properties": {
                        "AuthMethod": "RPS",
                        "SiteName": "user.auth.xboxlive.com",
                        "RpsTicket": rps_ticket
                    },
                    "RelyingParty": "http://auth.xboxlive.com",
                    "TokenType": "JWT"
                }

                headers = {
                    "Content-Type": "application/json",
                    "Accept": "application/json"
                }

                response = self.session.post(XBL_AUTH_URL, json=request_body, headers=headers)
                response.raise_for_status()
                return response.json()

            except Exception:
                continue

        raise Exception("Xbox Live authentication failed with both RPS ticket formats")

    def auth_xsts(self, xbl_token):
        request_body = {
            "Properties": {
                "SandboxId": "RETAIL",
                "UserTokens": [xbl_token]
            },
            "RelyingParty": "rp://api.minecraftservices.com/",
            "TokenType": "JWT"
        }

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

        response = self.session.post(XSTS_AUTH_URL, json=request_body, headers=headers)
        response.raise_for_status()
        return response.json()

    def login_minecraft(self, user_hash, xsts_token):
        request_body = {
            "identityToken": f"XBL3.0 x={user_hash};{xsts_token}"
        }

        headers = {
            "Content-Type": "application/json"
        }

        response = self.session.post(MC_LOGIN_URL, json=request_body, headers=headers)
        response.raise_for_status()
        return response.json()

    def get_minecraft_pf(self, minecraft_access_token):
        headers = {
            "Authorization": f"Bearer {minecraft_access_token}"
        }

        response = self.session.get(PROFILE_URL, headers=headers)
        response.raise_for_status()
        return response.json()

    def _auth_flow(self, device_code_data):
        try:
            token_response = self.poll_microsoft_token(device_code_data)

            xbl_data = self.auth_xbox_live(token_response['access_token'])
            xbl_token = xbl_data['Token']
            user_hash = xbl_data['DisplayClaims']['xui'][0]['uhs']

            xsts_data = self.auth_xsts(xbl_token)
            xsts_token = xsts_data['Token']

            mc_token_data = self.login_minecraft(user_hash, xsts_token)
            mc_access_token = mc_token_data['access_token']

            profile = self.get_minecraft_pf(mc_access_token)

            return {
                "tokens": {
                    "microsoft_access_token": token_response['access_token'],
                    "microsoft_refresh_token": token_response['refresh_token'],
                    "xbl_token": xbl_token,
                    "xsts_token": xsts_token,
                    "minecraft_access_token": mc_access_token,
                    "expires_in": mc_token_data['expires_in']
                },
                "profile": profile
            }

        except Exception as e:
            print(f"Authentication failed: {e}")
            raise

    def finish_auth(self, device_code_data):
        return self._auth_flow(device_code_data)