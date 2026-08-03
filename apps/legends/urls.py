from django.urls import path

from apps.legends.view.views import (
    LegendCategoryDetailView,
    LegendCategoryListView,
    LegendCategoryShareRedeemView,
    LegendCategoryShareView,
    LegendDetailView,
    LegendImportView,
    LegendListView,
)


urlpatterns = [
    path("", LegendListView.as_view()),
    path("import/", LegendImportView.as_view()),
    path("categories/", LegendCategoryListView.as_view()),
    path("categories/<int:category_id>/", LegendCategoryDetailView.as_view()),
    path("categories/<int:category_id>/share/", LegendCategoryShareView.as_view()),
    path("category-shares/redeem/", LegendCategoryShareRedeemView.as_view()),
    path("<int:legend_id>/", LegendDetailView.as_view()),
]
