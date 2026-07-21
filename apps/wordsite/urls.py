from django.urls import path

from apps.wordsite.view.views import (
    ExportReportDocxView,
    ImportExistingReportView,
    ImportReportDocxView,
)

urlpatterns = [
    path("export/", ExportReportDocxView.as_view()),
    path("import/", ImportReportDocxView.as_view()),
    path("import-existing/", ImportExistingReportView.as_view()),
]
