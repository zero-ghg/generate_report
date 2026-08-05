from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.db.models import Q
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.users.models import UserInfo
from apps.users.serializers.users import UserInfoLoginSerializer, UserInfoSerializer
from generate_report.utils.auth import TokenAuthenticate


def user_payload(user):
    return {
        "id": user.id,
        "username": user.username,
        "is_admin": user.is_admin,
        "create_time": user.create_time,
        "update_time": user.update_time,
    }


class UserInfoLoginView(GenericAPIView):
    queryset = UserInfo.objects.filter(is_delete=False)
    serializer_class = UserInfoLoginSerializer
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = UserInfoLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        return Response({"code": 200, "data": data, "msg": "登录成功"})


class CurrentUserView(APIView):
    authentication_classes = [TokenAuthenticate]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({"code": 200, "data": user_payload(request.user)})


class ChangePasswordView(APIView):
    authentication_classes = [TokenAuthenticate]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        old_password = str(request.data.get("old_password") or "")
        new_password = str(request.data.get("new_password") or "")
        if len(new_password) < 6:
            raise ValidationError("新密码至少需要 6 位")
        try:
            valid = check_password(old_password, request.user.password)
        except (ValueError, TypeError):
            valid = old_password == request.user.password
        if not valid:
            raise ValidationError("当前密码错误")
        request.user.password = make_password(new_password)
        request.user.save(update_fields=["password", "update_time"])
        return Response({"code": 200, "msg": "密码已修改"})


class UserListView(APIView):
    authentication_classes = [TokenAuthenticate]
    permission_classes = [IsAuthenticated]

    def _require_admin(self, request):
        if not request.user.is_admin:
            raise PermissionDenied("仅管理员可管理用户")

    def get(self, request):
        self._require_admin(request)
        users = UserInfo.objects.filter(is_delete=False).order_by("id")
        return Response({"code": 200, "data": [user_payload(user) for user in users]})

    def post(self, request):
        self._require_admin(request)
        username = str(request.data.get("username") or "").strip()
        password = str(request.data.get("password") or "")
        if not username or len(username) > 32:
            raise ValidationError("请输入 1-32 位用户名")
        if len(password) < 6:
            raise ValidationError("密码至少需要 6 位")
        if UserInfo.objects.filter(username=username, is_delete=False).exists():
            raise ValidationError("用户名已存在")
        user = UserInfo.objects.create(
            username=username,
            password=make_password(password),
            is_admin=bool(request.data.get("is_admin", False)),
        )
        return Response({"code": 201, "data": user_payload(user), "msg": "用户已创建"}, status=status.HTTP_201_CREATED)

    def delete(self, request, user_id):
        self._require_admin(request)
        if user_id == request.user.id:
            raise ValidationError("不能删除当前登录用户")
        user = UserInfo.objects.filter(id=user_id, is_delete=False).first()
        if not user:
            raise ValidationError("用户不存在")

        # 用户删除改为物理删除。Legend.category 使用 PROTECT，因此需先删除
        # 用户拥有的图例以及其个人分类中的图例，然后再由 ORM 级联删除
        # 分类、分享记录、项目和用户本身。事务可防止只删掉一部分数据。
        from apps.legends.models import Legend, LegendCategory

        with transaction.atomic():
            owned_categories = LegendCategory.objects.filter(owner=user)
            Legend.objects.filter(Q(owner=user) | Q(category__in=owned_categories)).delete()
            owned_categories.delete()
            user.delete()
        return Response({"code": 200, "msg": "用户已删除"})


class AdminPasswordResetView(APIView):
    """Administrative password reset endpoint.

    It deliberately requires an administrator access token.  This keeps the
    simple username + new-password payload useful to operational tools without
    exposing a public endpoint that could reset any account.
    """

    authentication_classes = [TokenAuthenticate]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not request.user.is_admin:
            raise PermissionDenied("仅管理员可重置用户密码")
        username = str(request.data.get("username") or "").strip()
        password = str(request.data.get("password") or "")
        if not username:
            raise ValidationError("请输入用户名")
        if len(password) < 6:
            raise ValidationError("新密码至少需要 6 位")
        user = UserInfo.objects.filter(username=username, is_delete=False).first()
        if not user:
            raise ValidationError("用户不存在")
        user.password = make_password(password)
        user.save(update_fields=["password", "update_time"])
        return Response({"code": 200, "data": user_payload(user), "msg": "密码已重置"})
