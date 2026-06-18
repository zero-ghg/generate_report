from rest_framework import serializers
from apps.users.models import UserInfo
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework.exceptions import AuthenticationFailed


class UserInfoLoginSerializer(serializers.ModelSerializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True)
    user_id = serializers.IntegerField(read_only=True)
    refresh = serializers.CharField(read_only=True)
    access = serializers.CharField(read_only=True)

    class Meta:
        model = UserInfo
        fields = (
            "user_id",
            "username",
            "password",
            "refresh",
            "access",
        )

    def validate(self, attrs):
        username = attrs.get('username')
        password = attrs.get('password')
        user = UserInfo.objects.filter(username=username, password=password).first()
        if user:
            refresh = RefreshToken.for_user(user)
            return {
                'user_id': user.id,
                'username': username,
                'refresh': str(refresh),
                'access': str(refresh.access_token),
            }
        else:
            raise AuthenticationFailed("用户权限认证失败")


class UserInfoSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserInfo
        # fields = "__all__"
        exclude = ['password', 'is_delete']
