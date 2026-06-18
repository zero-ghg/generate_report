from rest_framework.generics import GenericAPIView, ListAPIView
from rest_framework.response import Response
from apps.users.serializers.users import UserInfoLoginSerializer, UserInfoSerializer
from apps.users.models import UserInfo
# TODO: 修改包名
from generate_report.utils.auth import TokenAuthenticate


class UserInfoLoginView(GenericAPIView):
    queryset = UserInfo.objects.filter(is_delete=False)
    serializer_class = UserInfoLoginSerializer
    authentication_classes = []
    permission_classes = []
    def post(self, request, *args, **kwargs):
        serializer = UserInfoLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        return Response(serializer.data)


class UserListView(ListAPIView):
    queryset = UserInfo.objects.filter(is_delete=False)
    serializer_class = UserInfoSerializer
    permission_classes = []
