import base64
import copy
import json
import logging
import uuid
from datetime import datetime
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

from django.conf import settings
from PIL import Image
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.wordsite.scripts import generate_formatted_report
from apps.wordsite.scripts import parse_dwg_workspace
from apps.wordsite.scripts import parse_formatted_report

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


def _is_valid_report(report):
    if not report or not isinstance(report, dict):
        return False
    if isinstance(report.get("assistant"), dict):
        return True
    if report.get("reportTables") or report.get("legend"):
        return True
    return False


def _payload_from_request_data(request_data):
    if isinstance(request_data, dict) and not hasattr(request_data, "getlist"):
        return request_data

    if not hasattr(request_data, "get"):
        return None

    for key in ("report", "json", "data"):
        raw = request_data.get(key)
        if not raw or not isinstance(raw, str):
            continue
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            continue
    return None


def _resolve_report_payload(request):
    payload = _payload_from_request_data(request.data)
    if payload:
        report, filename = _parse_report_payload(payload)
        if _is_valid_report(report):
            return report, filename

    upload = request.FILES.get("file") or request.FILES.get("json") or request.FILES.get("report")
    if upload:
        try:
            payload = json.loads(upload.read().decode("utf-8-sig"))
            report, json_filename = _parse_report_payload(payload)
            if _is_valid_report(report):
                form_filename = request.data.get("filename") if hasattr(request.data, "get") else None
                return report, form_filename or json_filename
        except (json.JSONDecodeError, UnicodeDecodeError, UnicodeError):
            pass

    return None, None


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

    suffix = Path(filename).suffix or ".docx"
    safe_name = f"{datetime.now():%Y%m%d}_{uuid.uuid4().hex[:8]}{suffix}"

    file_path = save_dir / safe_name
    file_path.write_bytes(buffer.getvalue())

    relative_path = str(sub_dir / safe_name).replace("\\", "/")
    media_url = getattr(settings, "MEDIA_URL", "/media/")
    file_url = f"{media_url.rstrip('/')}/{quote(relative_path)}"

    return {
        "filename": safe_name,
        "relative_path": relative_path,
        "url": file_url,
    }


def _save_report_json(report):
    sub_dir = Path(getattr(settings, "REPORT_JSON_DIR", "reports/json"))
    save_dir = Path(settings.MEDIA_ROOT) / sub_dir
    save_dir.mkdir(parents=True, exist_ok=True)

    safe_name = f"{datetime.now():%Y%m%d}_{uuid.uuid4().hex[:8]}.json"
    file_path = save_dir / safe_name
    file_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    relative_path = str(sub_dir / safe_name).replace("\\", "/")
    media_url = getattr(settings, "MEDIA_URL", "/media/")
    file_url = f"{media_url.rstrip('/')}/{quote(relative_path)}"

    return {
        "filename": safe_name,
        "relative_path": relative_path,
        "url": file_url,
    }


# 导出 Word 报告接口：接收前端传入的 JSON 报告数据，按模板生成 Word 文档并返回下载地址。
class ExportReportDocxView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        report, filename = _resolve_report_payload(request)
        if not report:
            return Response({"code": 400, "msg": "请传递 JSON 数据或上传 JSON 文件"}, status=400)

        template_path = Path(getattr(settings, "REPORT_TEMPLATE_PATH", ""))
        if not template_path.exists():
            raise ReportTemplateError(detail=f"报告模板不存在，请将模板放到: {template_path}")

        try:
            buffer = generate_formatted_report.build_formatted_report_docx(report, template_path)
            filename = _resolve_report_filename(report, filename)
            saved = _save_report_docx(buffer, filename)
            return Response(
                {
                    "code": 200,
                    "msg": "生成成功",
                    "data": {
                        "url": saved["url"],
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


# 导入 Word 解析接口：接收上传的 Word 文档，解析成系统可用的 JSON 数据并返回 JSON 文件地址。
class ImportReportDocxView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        upload = (
            request.FILES.get("file")
            or request.FILES.get("docx")
            or request.FILES.get("word")
            or request.FILES.get("report")
        )
        if not upload:
            return Response({"code": 400, "msg": "请上传 Word 文档（.docx）"}, status=400)

        filename = upload.name or ""
        if not filename.lower().endswith(".docx"):
            return Response({"code": 400, "msg": "仅支持 .docx 格式的 Word 文档"}, status=400)

        try:
            report = parse_formatted_report.parse_formatted_report_docx(upload.read())
            saved = _save_report_json(report)
            return Response(
                {
                    "code": 200,
                    "msg": "解析成功",
                    "data": {
                        "url": saved["url"],
                        "filename": saved["filename"],
                    },
                }
            )
        except Exception:
            logger.exception("解析报告 DOCX 失败")
            raise ReportDataError(detail="文档解析失败，请确认是否为系统生成的报告")


# 导入 DWG/DXF 接口：解析图形实体为可编辑工作区 JSON，并按 id、检测点编号和源句柄绑定检测表数据。
def _existing_report_image_layout(upload):
    """读取校对图片尺寸，并生成与前端报告图一致的 DWG 映射区域。"""
    image_bytes = upload.read()
    upload.seek(0)
    try:
        with Image.open(BytesIO(image_bytes)) as image:
            width, height = image.size
            rotated = height > width
            if rotated:
                image = image.transpose(Image.Transpose.ROTATE_270)
                width, height = image.size
                output = BytesIO()
                image.convert("RGB").save(output, format="JPEG", quality=95)
                image_bytes = output.getvalue()
                mime_type = "image/jpeg"
            else:
                mime_type = str(getattr(upload, "content_type", "") or Image.MIME.get(image.format, "image/png"))
    except Exception as exc:
        raise ReportDataError(detail="校对图片无法读取，请上传 JPG、PNG 或 WEBP 图片") from exc

    return {
        "width": width,
        "height": height,
        "rotated": rotated,
        "_dataUrl": f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}",
        "targetArea": parse_dwg_workspace.report_image_target_area(width, height),
    }


def _remap_model_binding(binding, id_map):
    if not isinstance(binding, dict) or binding.get("id") not in id_map:
        return binding
    return {**binding, "id": id_map[binding["id"]]}


def _combine_drawing_workspaces(drawing_results, report):
    """Keep each imported DWG in an independent, tab-local model space."""
    canvases = [parse_dwg_workspace._active_canvas(item["result"]["workspace"]) for item in drawing_results]
    if len(canvases) == 1:
        return drawing_results[0]["result"]["workspace"]

    # Do not place sheets next to each other.  Every tab is a complete DWG
    # sheet with its own origin; only the IDs need to be merged for React
    # state.  Side-by-side placement made a second sheet inherit an enormous
    # x-offset and then get squeezed when any consumer fitted the whole board.
    board_width = max(int(canvas.get("boardWidth") or 1600) for canvas in canvases)
    board_height = max(int(canvas.get("boardHeight") or 1280) for canvas in canvases)

    combined = {
        "blocks": [],
        "boardHeight": board_height,
        "boardWidth": board_width,
        "nativePreviewChrome": {"fromImportedDwg": True, "hasLegend": True, "hasTitleBlock": True},
        "nextId": 1,
        "paths": [],
        "testPoints": [],
        "texts": [],
        "autoBoardSize": False,
        "coverReportInfo": copy.deepcopy(canvases[0].get("coverReportInfo") or {}),
        "reportChapterOrder": copy.deepcopy(canvases[0].get("reportChapterOrder") or []),
        "reportTables": copy.deepcopy(report.get("reportTables") or {}),
        "drawingGroups": [],
    }
    next_id = 1
    for index, (item, canvas) in enumerate(zip(drawing_results, canvases)):
        source_items = [
            *(canvas.get("paths") or []),
            *(canvas.get("texts") or []),
            *(canvas.get("blocks") or []),
            *(canvas.get("testPoints") or []),
        ]
        id_map = {}
        for source_item in source_items:
            old_id = source_item.get("id")
            if old_id is not None and old_id not in id_map:
                id_map[old_id] = next_id
                next_id += 1

        group_id = f"drawing-{index + 1}"
        for path in copy.deepcopy(canvas.get("paths") or []):
            path["id"] = id_map[path["id"]]
            if path.get("polygonBasePoints"):
                path["polygonBasePoints"] = list(path["polygonBasePoints"])
            if path.get("interactionGroupId"):
                path["interactionGroupId"] = f"{group_id}:{path['interactionGroupId']}"
            path["drawingId"] = group_id
            combined["paths"].append(path)
        for text in copy.deepcopy(canvas.get("texts") or []):
            text["id"] = id_map[text["id"]]
            text["boundTarget"] = _remap_model_binding(text.get("boundTarget"), id_map)
            if text.get("interactionGroupId"):
                text["interactionGroupId"] = f"{group_id}:{text['interactionGroupId']}"
            text["drawingId"] = group_id
            combined["texts"].append(text)
        for block in copy.deepcopy(canvas.get("blocks") or []):
            block["id"] = id_map[block["id"]]
            block["parentBinding"] = _remap_model_binding(block.get("parentBinding"), id_map)
            if block.get("interactionGroupId"):
                block["interactionGroupId"] = f"{group_id}:{block['interactionGroupId']}"
            block["drawingId"] = group_id
            combined["blocks"].append(block)
        for point in copy.deepcopy(canvas.get("testPoints") or []):
            point["id"] = id_map[point["id"]]
            point["binding"] = _remap_model_binding(point.get("binding"), id_map)
            point["placeBinding"] = _remap_model_binding(point.get("placeBinding"), id_map)
            for field in ("blockId", "reportBlockId", "visualTestPointId"):
                if point.get(field) in id_map:
                    point[field] = id_map[point[field]]
            point["sourceElementIds"] = [id_map.get(value, value) for value in point.get("sourceElementIds") or []]
            if point.get("interactionGroupId"):
                point["interactionGroupId"] = f"{group_id}:{point['interactionGroupId']}"
            point["drawingId"] = group_id
            combined["testPoints"].append(point)

        drawing_name = str(item["dwg"].name or f"drawing-{index + 1}.dwg")
        image_upload = item.get("image")
        combined["drawingGroups"].append({
            "id": group_id,
            "name": drawing_name,
            "x": 0,
            "y": 0,
            "width": int(canvas.get("boardWidth") or 1600),
            "height": int(canvas.get("boardHeight") or 1280),
            "imageName": str(image_upload.name or "") if image_upload else "",
        })
    combined["nextId"] = next_id
    workspace = copy.deepcopy(drawing_results[0]["result"]["workspace"])
    active_id = str(workspace.get("activeTabId") or 1)
    workspace["tabData"][active_id] = combined
    return workspace


# 导入已有报告接口：统一解析 DOCX、DWG 和校对图片，返回已绑定报告及可编辑工作区。
class ImportExistingReportView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        docx_upload = request.FILES.get("docx") or request.FILES.get("report")
        dwg_uploads = request.FILES.getlist("dwgs") or [request.FILES.get("dwg") or request.FILES.get("file")]
        image_uploads = request.FILES.getlist("images") or [request.FILES.get("image") or request.FILES.get("preview")]
        dwg_uploads = [upload for upload in dwg_uploads if upload]
        image_uploads = [upload for upload in image_uploads if upload]
        if not docx_upload or not str(docx_upload.name or "").lower().endswith(".docx"):
            return Response({"code": 400, "msg": "请上传已有报告 DOCX 文件"}, status=400)
        if not dwg_uploads or any(Path(str(upload.name or "")).suffix.lower() not in {".dwg", ".dxf"} for upload in dwg_uploads):
            return Response({"code": 400, "msg": "请上传对应的 DWG 或 DXF 文件"}, status=400)
        try:
            report = parse_formatted_report.parse_formatted_report_docx(docx_upload.read())
            dwg_upload = dwg_uploads[0]
            image_upload = image_uploads[0] if image_uploads else None
            image_layout = _existing_report_image_layout(image_upload) if image_upload else None
            result = parse_dwg_workspace.parse_dwg_workspace(
                dwg_upload.read(),
                str(dwg_upload.name or "drawing.dwg"),
                binding_data=report,
                **({
                    "board_width": image_layout["width"],
                    "board_height": image_layout["height"],
                    "target_area": image_layout["targetArea"],
                } if image_layout else {}),
            )
            parse_dwg_workspace.finalize_existing_report_workspace(
                result["workspace"],
                image_data_url=image_layout["_dataUrl"] if image_layout else None,
                image_filename=str(image_upload.name or "校对图片") if image_upload else "校对图片",
            )
            drawing_results = [{"dwg": dwg_upload, "image": image_upload, "result": result}]
            image_layouts = [image_layout] if image_layout else []
            for index, extra_dwg in enumerate(dwg_uploads[1:], start=1):
                extra_image = image_uploads[index] if index < len(image_uploads) else None
                extra_layout = _existing_report_image_layout(extra_image) if extra_image else None
                extra_result = parse_dwg_workspace.parse_dwg_workspace(
                    extra_dwg.read(),
                    str(extra_dwg.name or "drawing.dwg"),
                    binding_data=report,
                    **({
                        "board_width": extra_layout["width"],
                        "board_height": extra_layout["height"],
                        "target_area": extra_layout["targetArea"],
                    } if extra_layout else {}),
                )
                parse_dwg_workspace.finalize_existing_report_workspace(
                    extra_result["workspace"],
                    image_data_url=extra_layout["_dataUrl"] if extra_layout else None,
                    image_filename=str(extra_image.name or "校对图片") if extra_image else "校对图片",
                )
                drawing_results.append({"dwg": extra_dwg, "image": extra_image, "result": extra_result})
                if extra_layout:
                    image_layouts.append(extra_layout)
            workspace = _combine_drawing_workspaces(drawing_results, report)
            canvas = parse_dwg_workspace._active_canvas(workspace)
            result["stats"].update({
                "paths": len(canvas.get("paths") or []),
                "texts": len(canvas.get("texts") or []),
                "blocks": len(canvas.get("blocks") or []),
                "testPoints": len(canvas.get("testPoints") or []),
            })
            if result.get("report") is not None:
                result["report"]["reportTables"] = canvas.get("reportTables") or {}
            merged_report = result["report"] or report
            saved = _save_report_json(merged_report)
            return Response(
                {
                    "code": 200,
                    "msg": "已有报告解析成功",
                    "data": {
                        # "url": saved["url"],
                        # "filename": saved["filename"],
                        "report": merged_report,
                        "workspace": workspace,
                        "image": {key: value for key, value in image_layout.items() if not key.startswith("_")} if image_layout else {},
                        "images": [
                            {key: value for key, value in layout.items() if not key.startswith("_")}
                            for layout in image_layouts
                        ],
                        "drawingGroups": canvas.get("drawingGroups") or [],
                        # "source": result["source"],
                        # "stats": result["stats"],
                        # "unmatched": result["unmatched"],
                        # "warnings": result["warnings"],
                    },
                }
            )
        except parse_dwg_workspace.DwgConverterError as exc:
            logger.exception("导入已有报告时 DWG 转 DXF 失败")
            return Response({"code": 503, "msg": str(exc)}, status=503)
        except parse_dwg_workspace.DwgParseError as exc:
            logger.exception("导入已有报告时 DWG/DXF 解析失败")
            return Response({"code": 400, "msg": str(exc)}, status=400)
        except ReportDataError:
            raise
        except Exception:
            logger.exception("导入已有报告失败")
            raise ReportDataError(detail="已有报告解析失败，请检查 DOCX、DWG 和校对图片")
