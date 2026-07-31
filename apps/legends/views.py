import copy
import re
import secrets
from datetime import timedelta
from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.legends.models import Legend, LegendCategory, LegendShare, LegendShareRedemption
from apps.wordsite.scripts import parse_dwg_workspace
from generate_report.utils.auth import TokenAuthenticate


SHARE_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def category_payload(category):
    return {
        "id": category.id,
        "name": category.name,
        "is_system": category.is_system,
        "owner_id": category.owner_id,
        "sort_order": category.sort_order,
    }


def legend_payload(legend, include_data=False):
    data = {
        "id": legend.id,
        "name": legend.name,
        "category_id": legend.category_id,
        "owner_id": legend.owner_id,
        "is_system": legend.is_system,
        "source_type": legend.source_type,
        "original_filename": legend.original_filename,
        "source_size": legend.source_size,
        "preview_svg": legend.preview_svg,
        "file_url": f"/api/v1/legends/{legend.id}/file/",
        "create_time": legend.create_time,
        "update_time": legend.update_time,
    }
    if include_data:
        data["parsed_data"] = legend.parsed_data
        data["source_legend_id"] = legend.source_legend_id
    return data


def share_payload(share):
    return {
        "id": share.id,
        "share_code": share.code,
        "legend_id": share.legend_id,
        "legend_name": share.legend.name,
        "expires_at": share.expires_at,
        "max_uses": share.max_uses,
        "used_count": share.used_count,
        "is_revoked": share.is_revoked,
    }


def accessible_categories(user):
    return LegendCategory.objects.filter(is_delete=False).filter(Q(is_system=True) | Q(owner=user))


def accessible_legends(user):
    return Legend.objects.select_related("category").filter(is_delete=False).filter(Q(is_system=True) | Q(owner=user))


def get_accessible_legend(user, legend_id):
    legend = accessible_legends(user).filter(id=legend_id).first()
    if not legend:
        raise NotFound("图例不存在")
    return legend


def can_manage_legend(user, legend):
    """本人可改删自己的图例；管理员还可改删「通用」系统图例。"""
    if legend.owner_id == user.id:
        return True
    return bool(getattr(user, "is_admin", False) and legend.is_system)


def get_writable_legend(user, legend_id):
    legend = Legend.objects.select_related("category").filter(id=legend_id, is_delete=False).first()
    if not legend:
        raise NotFound("图例不存在")
    if not can_manage_legend(user, legend):
        raise PermissionDenied("无权修改该图例")
    return legend


def get_target_category(user, category_id=None):
    if category_id not in (None, ""):
        category = accessible_categories(user).filter(id=category_id).first()
        if not category:
            raise ValidationError("图例分类不存在")
        return category
    category = LegendCategory.objects.filter(is_system=True, is_delete=False).order_by("sort_order", "id").first()
    if category:
        return category
    return LegendCategory.objects.create(name="通用", is_system=True, sort_order=0)


def generate_share_code():
    for _ in range(20):
        raw = "".join(secrets.choice(SHARE_CODE_ALPHABET) for _ in range(8))
        code = f"{raw[:4]}-{raw[4:]}"
        if not LegendShare.objects.filter(code=code).exists():
            return code
    raise RuntimeError("无法生成唯一分享码")


def normalized_share_code(value):
    compact = re.sub(r"[^A-Z0-9]", "", str(value or "").upper())
    return f"{compact[:4]}-{compact[4:8]}" if len(compact) == 8 else str(value or "").strip().upper()


class LegendAccessMixin:
    authentication_classes = [TokenAuthenticate]
    permission_classes = [IsAuthenticated]


class LegendCategoryListView(LegendAccessMixin, APIView):
    def get(self, request):
        categories = accessible_categories(request.user).order_by("-is_system", "sort_order", "id")
        return Response({"code": 200, "data": [category_payload(item) for item in categories]})

    def post(self, request):
        name = str(request.data.get("name") or "").strip()
        if not name or len(name) > 80:
            raise ValidationError("请输入 1-80 位分类名称")
        if LegendCategory.objects.filter(owner=request.user, name=name, is_delete=False).exists():
            raise ValidationError("分类名称已存在")
        next_order = LegendCategory.objects.filter(owner=request.user, is_delete=False).count() + 1
        category = LegendCategory.objects.create(owner=request.user, name=name, sort_order=next_order)
        return Response(
            {"code": 201, "data": category_payload(category), "msg": "分类已创建"},
            status=status.HTTP_201_CREATED,
        )


class LegendCategoryDetailView(LegendAccessMixin, APIView):
    def put(self, request, category_id):
        category = LegendCategory.objects.filter(id=category_id, owner=request.user, is_delete=False).first()
        if not category:
            raise NotFound("分类不存在")
        name = str(request.data.get("name") or "").strip()
        if not name or len(name) > 80:
            raise ValidationError("请输入 1-80 位分类名称")
        duplicate = LegendCategory.objects.filter(owner=request.user, name=name, is_delete=False).exclude(id=category.id)
        if duplicate.exists():
            raise ValidationError("分类名称已存在")
        category.name = name
        category.save(update_fields=["name", "update_time"])
        return Response({"code": 200, "data": category_payload(category), "msg": "分类已更新"})

    def delete(self, request, category_id):
        category = LegendCategory.objects.filter(id=category_id, owner=request.user, is_delete=False).first()
        if not category:
            raise NotFound("分类不存在")
        category.is_delete = True
        category.save(update_fields=["is_delete", "update_time"])
        Legend.objects.filter(owner=request.user, category=category, is_delete=False).update(is_delete=True)
        return Response({"code": 200, "msg": "分类已删除"})


class LegendListView(LegendAccessMixin, APIView):
    def get(self, request):
        legends = accessible_legends(request.user)
        category_id = request.query_params.get("category_id")
        if category_id:
            legends = legends.filter(category_id=category_id)
        query = str(request.query_params.get("q") or "").strip()
        if query:
            legends = legends.filter(Q(name__icontains=query) | Q(original_filename__icontains=query))
        return Response({"code": 200, "data": [legend_payload(item) for item in legends]})


class LegendImportView(LegendAccessMixin, APIView):
    def post(self, request):
        upload = request.FILES.get("file") or request.FILES.get("dwg")
        if not upload:
            raise ValidationError("请选择 DWG 图例文件")
        filename = Path(upload.name or "").name
        if Path(filename).suffix.lower() != ".dwg":
            raise ValidationError("仅支持 DWG 格式的图例文件")
        max_size = int(getattr(settings, "DWG_UPLOAD_MAX_SIZE", 50 * 1024 * 1024))
        if upload.size > max_size:
            raise ValidationError(f"DWG 文件不能超过 {max_size // (1024 * 1024)}MB")
        name = str(request.data.get("name") or Path(filename).stem).strip()
        if not name or len(name) > 120:
            raise ValidationError("请输入 1-120 位图例名称")
        category = get_target_category(request.user, request.data.get("category_id"))
        # 同名仅限制在同一分类内；不同图例库可入库相同 DWG / 相同名称。
        if Legend.objects.filter(owner=request.user, category=category, name=name, is_delete=False).exists():
            raise ValidationError("当前分类下已存在同名图例")

        file_bytes = upload.read()
        try:
            parsed = parse_dwg_workspace.parse_dwg_workspace(file_bytes, filename)
        except parse_dwg_workspace.DwgParseError as exc:
            raise ValidationError(str(exc)) from exc

        preview_svg = str(request.data.get("preview_svg") or "")
        if preview_svg and "<svg" not in preview_svg[:500].lower():
            raise ValidationError("图例预览格式不正确")
        legend = Legend.objects.create(
            owner=request.user,
            category=category,
            name=name,
            original_filename=filename,
            content_type=str(getattr(upload, "content_type", "") or "application/acad")[:100],
            source_file=file_bytes,
            source_size=len(file_bytes),
            parsed_data=parsed,
            preview_svg=preview_svg,
            source_type="dwg",
        )
        return Response(
            {"code": 201, "data": legend_payload(legend, include_data=True), "msg": "图例已入库"},
            status=status.HTTP_201_CREATED,
        )


class LegendDetailView(LegendAccessMixin, APIView):
    def get(self, request, legend_id):
        return Response({"code": 200, "data": legend_payload(get_accessible_legend(request.user, legend_id), True)})

    def put(self, request, legend_id):
        legend = get_writable_legend(request.user, legend_id)
        update_fields = ["update_time"]
        if "name" in request.data:
            name = str(request.data.get("name") or "").strip()
            if not name or len(name) > 120:
                raise ValidationError("请输入 1-120 位图例名称")
            legend.name = name
            update_fields.append("name")
        if "category_id" in request.data:
            legend.category = get_target_category(request.user, request.data.get("category_id"))
            update_fields.append("category")
        if "name" in request.data or "category_id" in request.data:
            duplicate = (
                Legend.objects.filter(
                    owner=legend.owner,
                    category=legend.category,
                    name=legend.name,
                    is_delete=False,
                )
                .exclude(id=legend.id)
            )
            if duplicate.exists():
                raise ValidationError("当前分类下已存在同名图例")
        legend.save(update_fields=update_fields)
        return Response({"code": 200, "data": legend_payload(legend, True), "msg": "图例已更新"})

    def delete(self, request, legend_id):
        legend = get_writable_legend(request.user, legend_id)
        legend.is_delete = True
        legend.save(update_fields=["is_delete", "update_time"])
        LegendShare.objects.filter(legend=legend, is_delete=False).update(is_revoked=True)
        return Response({"code": 200, "msg": "图例已删除"})


class LegendFileView(LegendAccessMixin, APIView):
    def get(self, request, legend_id):
        legend = get_accessible_legend(request.user, legend_id)
        response = FileResponse(
            BytesIO(bytes(legend.source_file)),
            content_type=legend.content_type or "application/acad",
            filename=legend.original_filename,
        )
        response["Cache-Control"] = "private, max-age=3600"
        return response


class LegendShareView(LegendAccessMixin, APIView):
    def post(self, request, legend_id):
        legend = get_writable_legend(request.user, legend_id)
        if legend.is_system or legend.owner_id != request.user.id:
            raise PermissionDenied("只能分享自己的图例")
        try:
            expires_in_days = int(request.data.get("expires_in_days", 7))
        except (TypeError, ValueError) as exc:
            raise ValidationError("分享有效期格式不正确") from exc
        if expires_in_days < 1 or expires_in_days > 365:
            raise ValidationError("分享有效期必须在 1-365 天之间")
        max_uses = request.data.get("max_uses")
        if max_uses not in (None, ""):
            try:
                max_uses = int(max_uses)
            except (TypeError, ValueError) as exc:
                raise ValidationError("最大领取次数格式不正确") from exc
            if max_uses < 1 or max_uses > 10000:
                raise ValidationError("最大领取次数必须在 1-10000 之间")
        else:
            max_uses = None
        share = LegendShare.objects.create(
            legend=legend,
            creator=request.user,
            code=generate_share_code(),
            expires_at=timezone.now() + timedelta(days=expires_in_days),
            max_uses=max_uses,
        )
        return Response(
            {"code": 201, "data": share_payload(share), "msg": "分享码已生成"},
            status=status.HTTP_201_CREATED,
        )


class LegendShareDetailView(LegendAccessMixin, APIView):
    def delete(self, request, share_id):
        share = LegendShare.objects.filter(id=share_id, creator=request.user, is_delete=False).first()
        if not share:
            raise NotFound("分享记录不存在")
        share.is_revoked = True
        share.save(update_fields=["is_revoked", "update_time"])
        return Response({"code": 200, "msg": "分享码已撤销"})


class LegendShareRedeemView(LegendAccessMixin, APIView):
    @transaction.atomic
    def post(self, request):
        code = normalized_share_code(request.data.get("share_code"))
        share = (
            LegendShare.objects.select_for_update()
            .select_related("legend", "creator")
            .filter(code=code, is_delete=False)
            .first()
        )
        if not share:
            raise NotFound("分享码不存在")
        if share.is_revoked:
            raise ValidationError("分享码已被撤销")
        if timezone.now() >= share.expires_at:
            raise ValidationError("分享码已过期")
        if share.legend.is_delete:
            raise ValidationError("原图例已被删除")
        if share.max_uses is not None and share.used_count >= share.max_uses:
            raise ValidationError("分享码领取次数已用完")
        if share.creator_id == request.user.id:
            raise ValidationError("不能领取自己创建的分享码")
        existing = LegendShareRedemption.objects.select_related("copied_legend").filter(
            share=share,
            recipient=request.user,
            is_delete=False,
        ).first()
        if existing and not existing.copied_legend.is_delete:
            raise ValidationError("你已经领取过这个分享码")

        category = get_target_category(request.user, request.data.get("category_id"))
        copy_name = str(request.data.get("name") or share.legend.name).strip()[:120] or share.legend.name
        if Legend.objects.filter(owner=request.user, category=category, name=copy_name, is_delete=False).exists():
            copy_name = f"{copy_name}（分享）"[:120]
        copied = Legend.objects.create(
            owner=request.user,
            category=category,
            name=copy_name,
            original_filename=share.legend.original_filename,
            content_type=share.legend.content_type,
            source_file=bytes(share.legend.source_file),
            source_size=share.legend.source_size,
            parsed_data=copy.deepcopy(share.legend.parsed_data),
            preview_svg=share.legend.preview_svg,
            source_type="shared",
            source_legend=share.legend,
        )
        LegendShareRedemption.objects.create(share=share, recipient=request.user, copied_legend=copied)
        share.used_count += 1
        share.save(update_fields=["used_count", "update_time"])
        return Response(
            {"code": 201, "data": legend_payload(copied, True), "msg": "图例已添加到你的图例库"},
            status=status.HTTP_201_CREATED,
        )
