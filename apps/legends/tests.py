from datetime import timedelta
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.legends.models import Legend, LegendCategory, LegendShare, LegendShareRedemption
from apps.users.models import UserInfo


class LegendApiTests(TestCase):
    def setUp(self):
        self.owner = UserInfo.objects.create(username="legend-owner", password="x")
        self.other = UserInfo.objects.create(username="legend-other", password="x")
        self.system_category = LegendCategory.objects.create(name="通用", is_system=True)
        self.owner_category = LegendCategory.objects.create(name="我的设备", owner=self.owner)
        self.other_category = LegendCategory.objects.create(name="其他用户", owner=self.other)
        self.system_legend = self.make_legend("系统图例", None, self.system_category, is_system=True)
        self.owner_legend = self.make_legend("我的图例", self.owner, self.owner_category)
        self.other_legend = self.make_legend("不可见图例", self.other, self.other_category)
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    @staticmethod
    def make_legend(name, owner, category, is_system=False):
        return Legend.objects.create(
            owner=owner,
            category=category,
            name=name,
            original_filename=f"{name}.dwg",
            source_file=b"dwg-bytes",
            source_size=9,
            parsed_data={"workspace": {"version": 2}},
            preview_svg="<svg></svg>",
            source_type="system" if is_system else "dwg",
            is_system=is_system,
        )

    def test_list_only_returns_system_and_current_users_legends(self):
        response = self.client.get("/api/v1/legends/")
        self.assertEqual(response.status_code, 200)
        ids = {item["id"] for item in response.data["data"]}
        self.assertEqual(ids, {self.system_legend.id, self.owner_legend.id})

    @patch("apps.legends.views.parse_dwg_workspace.parse_dwg_workspace")
    def test_import_saves_original_file_and_parsed_workspace(self, parse):
        parse.return_value = {"workspace": {"version": 2}, "stats": {"paths": 1}}
        upload = SimpleUploadedFile("自定义图例.dwg", b"one-legend-dwg", content_type="application/acad")
        response = self.client.post(
            "/api/v1/legends/import/",
            {
                "category_id": self.owner_category.id,
                "file": upload,
                "name": "自定义图例",
                "preview_svg": "<svg viewBox='0 0 10 10'></svg>",
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 201)
        legend = Legend.objects.get(id=response.data["data"]["id"])
        self.assertEqual(legend.owner, self.owner)
        self.assertEqual(bytes(legend.source_file), b"one-legend-dwg")
        self.assertEqual(legend.parsed_data["workspace"]["version"], 2)

    @patch("apps.legends.views.parse_dwg_workspace.parse_dwg_workspace")
    def test_import_allows_same_name_and_file_in_different_category(self, parse):
        parse.return_value = {"workspace": {"version": 2}, "stats": {"paths": 1}}
        second_category = LegendCategory.objects.create(name="122", owner=self.owner, sort_order=2)
        upload = SimpleUploadedFile("风机.dwg", b"fan-dwg", content_type="application/acad")
        response = self.client.post(
            "/api/v1/legends/import/",
            {
                "category_id": second_category.id,
                "file": upload,
                "name": "风机",
                "preview_svg": "<svg viewBox='0 0 10 10'></svg>",
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["code"], 201)
        created = Legend.objects.get(id=response.data["data"]["id"])
        self.assertEqual(created.name, "风机")
        self.assertEqual(created.category_id, second_category.id)
        self.assertEqual(created.original_filename, "风机.dwg")

    @patch("apps.legends.views.parse_dwg_workspace.parse_dwg_workspace")
    def test_import_rejects_same_name_in_same_category(self, parse):
        parse.return_value = {"workspace": {"version": 2}, "stats": {"paths": 1}}
        upload = SimpleUploadedFile("重复.dwg", b"dup-dwg", content_type="application/acad")
        first = self.client.post(
            "/api/v1/legends/import/",
            {
                "category_id": self.owner_category.id,
                "file": upload,
                "name": "重复图例",
                "preview_svg": "<svg viewBox='0 0 10 10'></svg>",
            },
            format="multipart",
        )
        self.assertEqual(first.status_code, 201)
        second_upload = SimpleUploadedFile("重复.dwg", b"dup-dwg-2", content_type="application/acad")
        second = self.client.post(
            "/api/v1/legends/import/",
            {
                "category_id": self.owner_category.id,
                "file": second_upload,
                "name": "重复图例",
                "preview_svg": "<svg viewBox='0 0 10 10'></svg>",
            },
            format="multipart",
        )
        self.assertEqual(second.status_code, 200)
        self.assertNotEqual(second.data["code"], 201)
        self.assertIn("同名图例", second.data["message"])

    def test_redeem_creates_an_independent_copy(self):
        share = LegendShare.objects.create(
            legend=self.other_legend,
            creator=self.other,
            code="ABCD-2345",
            expires_at=timezone.now() + timedelta(days=7),
        )
        response = self.client.post(
            "/api/v1/legends/shares/redeem/",
            {"share_code": "abcd2345", "category_id": self.owner_category.id},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        copied = Legend.objects.get(id=response.data["data"]["id"])
        self.assertEqual(copied.owner, self.owner)
        self.assertEqual(copied.source_legend, self.other_legend)
        self.assertNotEqual(copied.id, self.other_legend.id)
        self.assertEqual(bytes(copied.source_file), bytes(self.other_legend.source_file))
        self.assertTrue(
            LegendShareRedemption.objects.filter(share=share, recipient=self.owner, copied_legend=copied).exists()
        )

    def test_expired_share_cannot_be_redeemed(self):
        LegendShare.objects.create(
            legend=self.other_legend,
            creator=self.other,
            code="WXYZ-6789",
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        response = self.client.post(
            "/api/v1/legends/shares/redeem/",
            {"share_code": "WXYZ-6789"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["message"], "分享码已过期")
        self.assertFalse(Legend.objects.filter(owner=self.owner, source_legend=self.other_legend).exists())

    def test_other_users_private_file_is_not_accessible(self):
        response = self.client.get(f"/api/v1/legends/{self.other_legend.id}/file/")
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(response.data["code"], 200)

    def test_owner_can_update_and_delete_own_legend(self):
        update = self.client.put(
            f"/api/v1/legends/{self.owner_legend.id}/",
            {"name": "我的图例已改名"},
            format="json",
        )
        self.assertEqual(update.status_code, 200)
        self.assertEqual(update.data["code"], 200)
        self.assertEqual(update.data["data"]["name"], "我的图例已改名")

        delete = self.client.delete(f"/api/v1/legends/{self.owner_legend.id}/")
        self.assertEqual(delete.status_code, 200)
        self.assertEqual(delete.data["code"], 200)
        self.owner_legend.refresh_from_db()
        self.assertTrue(self.owner_legend.is_delete)

    def test_regular_user_cannot_modify_system_legend(self):
        update = self.client.put(
            f"/api/v1/legends/{self.system_legend.id}/",
            {"name": "普通用户改系统图例"},
            format="json",
        )
        self.assertEqual(update.status_code, 200)
        self.assertEqual(update.data["code"], 403)
        self.assertIn("无权修改", update.data["message"])

        delete = self.client.delete(f"/api/v1/legends/{self.system_legend.id}/")
        self.assertEqual(delete.status_code, 200)
        self.assertEqual(delete.data["code"], 403)
        self.system_legend.refresh_from_db()
        self.assertFalse(self.system_legend.is_delete)

    def test_admin_can_update_and_delete_system_legend(self):
        admin = UserInfo.objects.create(username="legend-admin", password="x", is_admin=True)
        self.client.force_authenticate(admin)

        update = self.client.put(
            f"/api/v1/legends/{self.system_legend.id}/",
            {"name": "管理员改系统图例"},
            format="json",
        )
        self.assertEqual(update.status_code, 200)
        self.assertEqual(update.data["code"], 200)
        self.assertEqual(update.data["data"]["name"], "管理员改系统图例")

        delete = self.client.delete(f"/api/v1/legends/{self.system_legend.id}/")
        self.assertEqual(delete.status_code, 200)
        self.assertEqual(delete.data["code"], 200)
        self.system_legend.refresh_from_db()
        self.assertTrue(self.system_legend.is_delete)

    def test_admin_cannot_modify_other_users_private_legend(self):
        admin = UserInfo.objects.create(username="legend-admin-2", password="x", is_admin=True)
        self.client.force_authenticate(admin)
        response = self.client.put(
            f"/api/v1/legends/{self.other_legend.id}/",
            {"name": "管理员改别人的图例"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["code"], 403)

    def test_system_category_cannot_be_deleted(self):
        response = self.client.delete(f"/api/v1/legends/categories/{self.system_category.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(response.data["code"], 200)
        self.system_category.refresh_from_db()
        self.assertFalse(self.system_category.is_delete)
