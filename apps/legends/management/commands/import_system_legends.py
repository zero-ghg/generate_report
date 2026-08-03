from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.legends.models import Legend, LegendCategory
from apps.wordsite.scripts import parse_dwg_workspace


class Command(BaseCommand):
    help = "把一个目录中的单图例 DWG 文件导入为系统公共图例"

    def add_arguments(self, parser):
        parser.add_argument("source_dir", help="包含 DWG 图例文件的目录")
        parser.add_argument("--replace", action="store_true", help="覆盖同名系统图例")

    def handle(self, *args, **options):
        source_dir = Path(options["source_dir"]).expanduser().resolve()
        if not source_dir.is_dir():
            raise CommandError(f"图例目录不存在：{source_dir}")
        files = sorted(source_dir.glob("*.dwg"), key=lambda path: path.name)
        if not files:
            raise CommandError(f"目录中没有 DWG 文件：{source_dir}")

        category, _ = LegendCategory.objects.get_or_create(
            owner=None,
            is_system=True,
            is_delete=False,
            defaults={"name": "通用", "sort_order": 0},
        )
        imported = 0
        skipped = 0
        for file_path in files:
            name = file_path.stem
            existing = Legend.objects.filter(is_system=True, name=name, is_delete=False).first()
            if existing and not options["replace"]:
                skipped += 1
                self.stdout.write(f"跳过已存在图例：{name}")
                continue
            file_bytes = file_path.read_bytes()
            try:
                parsed = parse_dwg_workspace.parse_dwg_workspace(file_bytes, file_path.name)
            except parse_dwg_workspace.DwgParseError as exc:
                raise CommandError(f"{file_path.name} 解析失败：{exc}") from exc
            values = {
                "owner": None,
                "category": category,
                "original_filename": file_path.name,
                "content_type": "application/acad",
                "parsed_data": parsed,
                "source_type": "system",
                "is_system": True,
                "is_delete": False,
            }
            if existing:
                for field, value in values.items():
                    setattr(existing, field, value)
                existing.save()
                self.stdout.write(f"已更新：{name}")
            else:
                Legend.objects.create(name=name, **values)
                self.stdout.write(f"已导入：{name}")
            imported += 1
        self.stdout.write(self.style.SUCCESS(f"完成：导入/更新 {imported} 个，跳过 {skipped} 个"))
