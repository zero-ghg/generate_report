from django.urls import path

from apps.wordsite.view.views import ExportReportDocxView

urlpatterns = [
    path("export/", ExportReportDocxView.as_view()),
]
