from django.urls import path

from apps.legends.views import (
    LegendCategoryDetailView,
    LegendCategoryListView,
    LegendDetailView,
    LegendFileView,
    LegendImportView,
    LegendListView,
    LegendShareDetailView,
    LegendShareRedeemView,
    LegendShareView,
)


urlpatterns = [
    path("", LegendListView.as_view()),
    path("import/", LegendImportView.as_view()),
    path("categories/", LegendCategoryListView.as_view()),
    path("categories/<int:category_id>/", LegendCategoryDetailView.as_view()),
    path("shares/redeem/", LegendShareRedeemView.as_view()),
    path("shares/<int:share_id>/", LegendShareDetailView.as_view()),
    path("<int:legend_id>/", LegendDetailView.as_view()),
    path("<int:legend_id>/file/", LegendFileView.as_view()),
    path("<int:legend_id>/share/", LegendShareView.as_view()),
]
