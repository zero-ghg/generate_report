from django.urls import path
from apps.users.view.users import UserInfoLoginView, UserListView
urlpatterns = [
    path('login/', UserInfoLoginView.as_view()),
    path('user/', UserListView.as_view())
]