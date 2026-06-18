import requests
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed


class RemoteTokenAuthentication(BaseAuthentication):
    def authenticate(self, request):
        auth = request.headers.get("Authorization", "")

        if not auth.startswith("Bearer "):
            raise AuthenticationFailed("缺少Token")

        token = auth.replace("Bearer ", "", 1).strip()
        if not token:
            raise AuthenticationFailed("缺少Token")

        try:
            resp = requests.post(
                "http://127.0.0.1:8080/api/v1/verify/",
                json={"token": token},
                timeout=5,
            )
            data = resp.json()
        except requests.RequestException:
            raise AuthenticationFailed("认证服务不可用")
        except ValueError:
            raise AuthenticationFailed("认证服务返回异常")

        if resp.status_code != 200 or data.get("code") != 200:
            raise AuthenticationFailed(data.get("msg", "Token无效"))

        return data["data"], token

    def authenticate_header(self, request):
        return 'Bearer realm="api"'