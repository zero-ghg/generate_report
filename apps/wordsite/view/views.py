import logging
import uuid
from datetime import datetime
from pathlib import Path

from django.conf import settings
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.wordsite.scripts.generate_formatted_report import build_formatted_report_docx

logger = logging.getLogger(__name__)


class ReportDataError(APIException):
    status_code = 500
    default_detail = "数据处理失败，请稍后重试"
    default_code = "report_data_error"


class ReportTemplateError(APIException):
    status_code = 500
    default_detail = "报告模板不存在，请联系管理员配置"
    default_code = "report_template_error"


def _parse_report_payload(payload):
    if not payload:
        return None, None

    if not isinstance(payload, dict):
        return None, None

    if "report" in payload:
        report = payload.get("report")
        filename = payload.get("filename")
        if not report or not isinstance(report, dict):
            return None, None
        return report, filename

    return payload, payload.get("filename")


def _resolve_report_filename(report, filename=None):
    if filename:
        return filename

    general = report.get("assistant", {}).get("general", {})
    project_name = general.get("projectName") or "防雷检测报告"
    return f"{project_name}.docx"


def _save_report_docx(buffer, filename):
    sub_dir = Path(getattr(settings, "REPORT_DOCX_DIR", "reports/word"))
    save_dir = Path(settings.MEDIA_ROOT) / sub_dir
    save_dir.mkdir(parents=True, exist_ok=True)

    stem = Path(filename).stem
    suffix = Path(filename).suffix or ".docx"
    safe_name = f"{stem}_{datetime.now():%Y%m%d%H%M%S}_{uuid.uuid4().hex[:8]}{suffix}"

    file_path = save_dir / safe_name
    file_path.write_bytes(buffer.getvalue())

    relative_path = str(sub_dir / safe_name).replace("\\", "/")
    file_url = f"{relative_path}"

    return {
        "filename": safe_name,
        "relative_path": relative_path,
        "url": file_url,
    }


class ExportReportDocxView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        report, filename = _parse_report_payload(request.data)
        if not report:
            return Response({"code": 400, "msg": "请传递 JSON 数据"}, status=400)

        template_path = Path(getattr(settings, "REPORT_TEMPLATE_PATH", ""))
        if not template_path.exists():
            raise ReportTemplateError(detail=f"报告模板不存在，请将模板放到: {template_path}")

        try:
            buffer = build_formatted_report_docx(report, template_path)
            filename = _resolve_report_filename(report, filename)
            saved = _save_report_docx(buffer, filename)
            return Response(
                {
                    "code": 200,
                    "msg": "生成成功",
                    "data": {
                        "url": request.build_absolute_uri(saved["url"]),
                        "filename": saved["filename"],
                    },
                }
            )
        except FileNotFoundError as exc:
            logger.exception("报告模板缺失")
            raise ReportTemplateError(detail=str(exc))
        except Exception:
            logger.exception("导出报告 DOCX 失败")
            raise ReportDataError(detail="数据处理失败，请稍后重试")
