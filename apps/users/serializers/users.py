from rest_framework import serializers
from apps.users.models import UserInfo
from django.contrib.auth.hashers import check_password, identify_hasher, make_password
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
        user = UserInfo.objects.filter(username=username, is_delete=False).first()
        if user:
            try:
                valid = check_password(password, user.password)
            except (ValueError, TypeError):
                # Compatibility with the legacy plaintext field.  Upgrade it
                # immediately after a successful legacy login.
                valid = user.password == password
            if not valid:
                raise AuthenticationFailed("用户名或密码错误")
            try:
                identify_hasher(user.password)
            except (ValueError, TypeError):
                user.password = make_password(password)
                user.save(update_fields=["password", "update_time"])
            refresh = RefreshToken.for_user(user)
            return {
                'user_id': user.id,
                'username': username,
                'is_admin': user.is_admin,
                'refresh': str(refresh),
                'access': str(refresh.access_token),
            }
        raise AuthenticationFailed("用户名或密码错误")


class UserInfoSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserInfo
        # fields = "__all__"
        exclude = ['password', 'is_delete']
