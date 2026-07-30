from django.urls import path

from apps.projects.views import ProjectDetailView, ProjectListView

urlpatterns = [
    path("", ProjectListView.as_view()),
    path("<int:project_id>/", ProjectDetailView.as_view()),
]
