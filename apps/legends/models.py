from django.db import models

from apps.users.models import UserInfo
from generate_report.utils.models import BaseModel


class LegendCategory(BaseModel):
    owner = models.ForeignKey(
        UserInfo,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="legend_categories",
        verbose_name="所属用户",
    )
    name = models.CharField(max_length=80, verbose_name="分类名称")
    is_system = models.BooleanField(default=False, verbose_name="系统分类")
    sort_order = models.PositiveIntegerField(default=0, verbose_name="排序")

    class Meta:
        db_table = "tb_legend_category"
        ordering = ("sort_order", "id")
        verbose_name = "图例分类"


class Legend(BaseModel):
    SOURCE_CHOICES = (
        ("dwg", "DWG 导入"),
        ("shared", "分享复制"),
        ("system", "系统预置"),
    )

    owner = models.ForeignKey(
        UserInfo,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="legends",
        verbose_name="所属用户",
    )
    category = models.ForeignKey(
        LegendCategory,
        on_delete=models.PROTECT,
        related_name="legends",
        verbose_name="图例分类",
    )
    name = models.CharField(max_length=120, verbose_name="图例名称")
    original_filename = models.CharField(max_length=255, verbose_name="原始文件名")
    content_type = models.CharField(max_length=100, blank=True, default="application/acad", verbose_name="文件类型")
    parsed_data = models.JSONField(default=dict, blank=True, verbose_name="解析后的图例数据")
    preview_svg = models.TextField(blank=True, default="", verbose_name="预览 SVG")
    source_type = models.CharField(max_length=20, choices=SOURCE_CHOICES, default="dwg", verbose_name="来源")
    is_system = models.BooleanField(default=False, verbose_name="系统图例")
    source_legend = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="shared_copies",
        verbose_name="分享来源图例",
    )

    class Meta:
        db_table = "tb_legend"
        ordering = ("-update_time", "-id")
        verbose_name = "图例"


class LegendCategoryShare(BaseModel):
    """A shareable complete legend-library tab (category and all its items)."""

    category = models.ForeignKey(LegendCategory, on_delete=models.CASCADE, related_name="shares", verbose_name="图例分类")
    creator = models.ForeignKey(UserInfo, on_delete=models.CASCADE, related_name="legend_category_shares", verbose_name="分享人")
    code = models.CharField(max_length=24, unique=True, db_index=True, verbose_name="分享码")
    expires_at = models.DateTimeField(verbose_name="过期时间")
    max_uses = models.PositiveIntegerField(null=True, blank=True, verbose_name="最大领取次数")
    used_count = models.PositiveIntegerField(default=0, verbose_name="已领取次数")
    is_revoked = models.BooleanField(default=False, verbose_name="已撤销")

    class Meta:
        db_table = "tb_legend_category_share"
        ordering = ("-create_time", "-id")
        verbose_name = "图例库分类分享"


class LegendCategoryShareRedemption(BaseModel):
    share = models.ForeignKey(LegendCategoryShare, on_delete=models.CASCADE, related_name="redemptions", verbose_name="图例库分享")
    recipient = models.ForeignKey(UserInfo, on_delete=models.CASCADE, related_name="legend_category_share_redemptions", verbose_name="领取用户")
    copied_category = models.OneToOneField(LegendCategory, on_delete=models.CASCADE, related_name="share_redemption", verbose_name="复制后的分类")

    class Meta:
        db_table = "tb_legend_category_share_redemption"
        constraints = [
            models.UniqueConstraint(fields=("share", "recipient"), name="uniq_legend_category_share_recipient"),
        ]
        verbose_name = "图例库分类分享领取"
