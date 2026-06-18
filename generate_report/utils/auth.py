from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed, NotAuthenticated, APIException
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.tokens import AccessToken
from rest_framework import status
from apps.users.models import UserInfo


class TokenAuthenticate(BaseAuthentication):
    def authenticate(self, request):
        auth_header = request.META.get('HTTP_AUTHORIZATION')
        if auth_header:
            prefix = 'Bearer '
            if auth_header.startswith(prefix):
                token = auth_header[len(prefix):]
                print(token)
            else:
                raise AuthenticationFailed("无效的认证前缀")

            try:
                validated_token = AccessToken(token)
            except Exception as e:
                raise AuthenticationFailed("鉴权失败")

            user = UserInfo.objects.filter(pk=validated_token['user_id']).first()
            if user is None:
                raise AuthenticationFailed("用户不存在")

            return user, validated_token
        else:
            raise NotAuthenticated("没有找到Token")

    def authenticate_header(self, request):
        # 返回认证方案，指示客户端如何进行认证
        return 'Bearer realm="api"'
