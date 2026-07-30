from django.urls import path
from apps.users.view.users import AdminPasswordResetView, ChangePasswordView, CurrentUserView, UserInfoLoginView, UserListView
urlpatterns = [
    path('login/', UserInfoLoginView.as_view()),
    path('me/', CurrentUserView.as_view()),
    path('me/password/', ChangePasswordView.as_view()),
    path('users/', UserListView.as_view()),
    path('users/<int:user_id>/', UserListView.as_view()),
    # Explicit aliases for administration tools.  Both endpoints enforce an
    # authenticated administrator token in their views.
    path('admin/users/', UserListView.as_view()),
    path('admin/users/password/', AdminPasswordResetView.as_view()),
]
