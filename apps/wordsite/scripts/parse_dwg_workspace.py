import copy
import math
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from django.conf import settings


DEFAULT_REPORT_TABLES = {
    "grounding": [],
    "transition": [],
    "spd": [],
    "spdTest": [],
}


class DwgParseError(RuntimeError):
    """DWG/DXF 文件无法解析。"""


class DwgConverterError(DwgParseError):
    """DWG 转 DXF 工具不可用或转换失败。"""


class DwgDependencyError(DwgParseError):
    """CAD 解析依赖没有安装。"""


def parse_dwg_workspace(
    file_bytes,
    filename,
    binding_data=None,
    board_width=1600,
    board_height=1280,
    target_area=None,
):
    """将 DWG/DXF 转换为前端图例绘制工作区 JSON，并绑定检测表数据。"""
    suffix = Path(filename or "").suffix.lower()
    if suffix not in {".dwg", ".dxf"}:
        raise DwgParseError("仅支持 .dwg 或 .dxf 文件")

    try:
        import ezdxf
    except ImportError as exc:
        raise DwgDependencyError("缺少 ezdxf，请先安装 requirements.txt 中的依赖") from exc

    with TemporaryDirectory(prefix="dwg-workspace-") as temp_dir:
        temp_path = Path(temp_dir)
        source_path = temp_path / f"source{suffix}"
        source_path.write_bytes(file_bytes)
        dxf_path, converted = _ensure_dxf(source_path, temp_path)

        try:
            document = ezdxf.readfile(dxf_path)
        except Exception as exc:
            raise DwgParseError(f"DXF 文件解析失败：{exc}") from exc

        parser = _DxfWorkspaceParser(document)
        parsed = parser.parse()

    width = _positive_int(board_width, 1600)
    height = _positive_int(board_height, 1280)
    _normalize_canvas(parsed, width, height, target_area=target_area)

    binding_source = _extract_binding_source(binding_data)
    report_tables = binding_source["reportTables"]
    binding_result = _bind_report_rows(
        parsed,
        report_tables,
        binding_source["testPoints"],
        binding_source["boardWidth"],
        binding_source["boardHeight"],
        width,
        height,
    )
    _assign_interaction_groups(parsed, binding_result["testPoints"])
    _assign_sheet_frame_interaction_group(parsed)

    canvas = {
        "blocks": parsed["blocks"],
        "boardHeight": height,
        "boardWidth": width,
        "nativePreviewChrome": {
            "fromImportedDwg": True,
            "hasLegend": False,
            "hasTitleBlock": False,
        },
        "nextId": binding_result["nextId"],
        "paths": parsed["paths"],
        "testPoints": binding_result["testPoints"],
        "texts": parsed["texts"],
        "autoBoardSize": False,
        "coverReportInfo": binding_source["coverReportInfo"],
        "reportChapterOrder": binding_source["reportChapterOrder"],
        "reportTables": binding_result["reportTables"],
    }

    tab_id = 1
    title = Path(filename).stem or "DWG图纸"
    workspace = {
        "activeTabId": tab_id,
        "exportedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "nextTabId": 2,
        "schema": "dwg-legend-designer-workspace",
        "tabData": {str(tab_id): canvas},
        "tabs": [{"id": tab_id, "title": title}],
        "version": 2,
    }

    return {
        "workspace": workspace,
        "report": _build_report_payload(binding_data, workspace, binding_result["reportTables"]),
        "source": {
            "filename": filename,
            "format": suffix.lstrip("."),
            "convertedToDxf": converted,
        },
        "stats": {
            "paths": len(parsed["paths"]),
            "texts": len(parsed["texts"]),
            "blocks": len(parsed["blocks"]),
            "testPoints": len(binding_result["testPoints"]),
            "boundRows": binding_result["boundRows"],
            "unmatchedRows": len(binding_result["unmatched"]),
            "skippedEntities": parsed["skippedEntities"],
        },
        "unmatched": binding_result["unmatched"],
        "warnings": parsed["warnings"],
    }


def _ensure_dxf(source_path, temp_path):
    if source_path.suffix.lower() == ".dxf":
        return source_path, False

    converter = _find_oda_converter()
    if not converter:
        raise DwgConverterError(
            "未找到 ODA File Converter，请安装后在 settings 中配置 DWG_CONVERTER_PATH"
        )

    input_dir = temp_path / "input"
    output_dir = temp_path / "output"
    input_dir.mkdir()
    output_dir.mkdir()
    dwg_path = input_dir / source_path.name
    shutil.copy2(source_path, dwg_path)

    command = [
        str(converter),
        str(input_dir),
        str(output_dir),
        str(getattr(settings, "DWG_DXF_VERSION", "ACAD2018")),
        "DXF",
        "0",
        "1",
        "*.DWG",
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=int(getattr(settings, "DWG_CONVERTER_TIMEOUT", 120)),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DwgConverterError(f"DWG 转 DXF 失败：{exc}") from exc

    converted = next(output_dir.rglob("*.dxf"), None)
    if result.returncode != 0 or converted is None:
        detail = (result.stderr or result.stdout or "未生成 DXF 文件").strip()
        raise DwgConverterError(f"DWG 转 DXF 失败：{detail}")
    return converted, True


def _find_oda_converter():
    configured = str(getattr(settings, "DWG_CONVERTER_PATH", "") or "").strip()
    candidates = [
        configured,
        shutil.which("ODAFileConverter.exe"),
        shutil.which("ODAFileConverter"),
        Path("/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter"),
        Path.home() / "Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter",
        r"C:\Program Files\ODA\ODAFileConverter 27.1.0\ODAFileConverter.exe",
        r"C:\Program Files\ODA\ODAFileConverter 26.12.0\ODAFileConverter.exe",
        r"C:\Program Files\ODA\ODAFileConverter 25.12.0\ODAFileConverter.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


class _DxfWorkspaceParser:
    """把 ezdxf 实体转换成工作区可编辑对象。"""

    def __init__(self, document):
        self.document = document
        self.paths = []
        self.texts = []
        self.blocks = []
        self.warnings = []
        self.skipped_entities = 0
        self.next_id = 1
        self.text_entity_items = []

    def parse(self):
        for entity in self.document.modelspace():
            self._parse_entity(entity)
        self._attach_text_glyphs()
        return {
            "paths": self.paths,
            "texts": self.texts,
            "blocks": self.blocks,
            "nextId": self.next_id,
            "warnings": self.warnings,
            "skippedEntities": self.skipped_entities,
        }

    def _parse_entity(self, entity, inherited_handle=None, depth=0):
        if depth > 8:
            self.skipped_entities += 1
            return
        entity_type = entity.dxftype()
        handle = inherited_handle or getattr(entity.dxf, "handle", None)
        layer = getattr(entity.dxf, "layer", "0") or "0"

        try:
            if entity_type == "LINE":
                self._add_path(
                    entity_type,
                    [entity.dxf.start, entity.dxf.end],
                    False,
                    handle,
                    layer,
                    self._path_render_style(entity),
                )
            elif entity_type == "LWPOLYLINE":
                points = [(point[0], point[1]) for point in entity.get_points("xy")]
                self._add_path(
                    entity_type,
                    points,
                    bool(entity.closed),
                    handle,
                    layer,
                    self._path_render_style(entity),
                )
            elif entity_type == "POLYLINE":
                points = [vertex.dxf.location for vertex in entity.vertices]
                self._add_path(
                    entity_type,
                    points,
                    bool(entity.is_closed),
                    handle,
                    layer,
                    self._path_render_style(entity),
                )
            elif entity_type == "CIRCLE":
                self._add_path(entity_type, _arc_points(entity.dxf.center, entity.dxf.radius, 0, 360, 48), True, handle, layer)
            elif entity_type == "ARC":
                self._add_path(
                    entity_type,
                    _arc_points(entity.dxf.center, entity.dxf.radius, entity.dxf.start_angle, entity.dxf.end_angle, 24),
                    False,
                    handle,
                    layer,
                )
            elif entity_type in {"ELLIPSE", "SPLINE"}:
                points = list(entity.flattening(distance=0.5, segments=8))
                self._add_path(entity_type, points, entity_type == "ELLIPSE", handle, layer)
            elif entity_type == "HATCH":
                self._add_hatch(entity, handle, layer)
            elif entity_type in {"TEXT", "MTEXT", "ATTRIB", "ATTDEF"}:
                self._add_text(entity, handle, layer)
            elif entity_type == "INSERT":
                self._add_insert(entity, handle, layer, depth)
            elif entity_type == "DIMENSION":
                for child in entity.virtual_entities():
                    self._parse_entity(child, handle, depth + 1)
            elif entity_type in {"POINT", "SOLID", "TRACE", "3DFACE"}:
                self._parse_simple_geometry(entity, handle, layer)
            else:
                self.skipped_entities += 1
        except Exception as exc:
            self.skipped_entities += 1
            if len(self.warnings) < 50:
                self.warnings.append(f"实体 {entity_type}({handle or '无句柄'}) 解析失败：{exc}")

    def _path_render_style(self, entity):
        """Preserve CAD complex linetypes that carry visible text."""
        linetype_name = str(getattr(entity.dxf, "linetype", "") or "").upper()
        if linetype_name in {"", "BYLAYER", "BYBLOCK"}:
            try:
                layer = self.document.layers.get(str(getattr(entity.dxf, "layer", "0") or "0"))
                linetype_name = str(getattr(layer.dxf, "linetype", "") or "").upper()
            except Exception:
                pass
        if linetype_name != "TG_LP":
            return None

        period = 2.78
        try:
            linetype = self.document.linetypes.get(linetype_name)
            period = abs(float(getattr(linetype.dxf, "length", 0) or 0)) or period
        except Exception:
            pass
        entity_scale = abs(float(getattr(entity.dxf, "ltscale", 1) or 1))
        global_scale = abs(float(self.document.header.get("$LTSCALE", 1) or 1))
        period *= entity_scale * global_scale
        return {
            "borderTileLabel": "LP",
            "borderTileSize": max(period * 0.2, 0.8),
            "borderTileSpacing": max(period, 1.0),
            "cadRender": True,
            "cadLinetype": linetype_name,
        }

    def _add_path(self, name, raw_points, closed, handle, layer, render_style=None):
        points = [_xy(point) for point in raw_points]
        points = [point for point in points if point is not None]
        if len(points) < 2:
            return
        item = {
            "name": name,
            "closed": bool(closed),
            "points": [{"x": point[0], "y": point[1]} for point in points],
            "id": self._new_id(),
            "layer": layer,
            "importedSourceHandles": _handles(handle),
        }
        if render_style:
            item.update(render_style)
        self.paths.append(item)

    def _add_text(self, entity, handle, layer):
        raw_content = str(getattr(entity, "text", "") or "")
        if entity.dxftype() == "MTEXT":
            content = entity.plain_text()
            insert = entity.dxf.insert
            height = float(getattr(entity.dxf, "char_height", 2.5) or 2.5)
            rotation = float(getattr(entity.dxf, "rotation", 0) or 0)
            attachment_point = int(getattr(entity.dxf, "attachment_point", 1) or 1)
            layout_width = float(getattr(entity.dxf, "width", 0) or 0)
            horizontal_slot = (attachment_point - 1) % 3
            vertical_slot = (attachment_point - 1) // 3
            text_anchor = ("start", "middle", "end")[horizontal_slot]
            dominant_baseline = ("hanging", "central", "text-after-edge")[vertical_slot]
        else:
            content = str(getattr(entity.dxf, "text", "") or "")
            insert = getattr(entity.dxf, "insert", (0, 0, 0))
            height = float(getattr(entity.dxf, "height", 2.5) or 2.5)
            rotation = float(getattr(entity.dxf, "rotation", 0) or 0)
            attachment_point = None
            layout_width = 0
            horizontal_alignment = int(getattr(entity.dxf, "halign", 0) or 0)
            vertical_alignment = int(getattr(entity.dxf, "valign", 0) or 0)
            align_point = getattr(entity.dxf, "align_point", None)
            if align_point is not None and (horizontal_alignment or vertical_alignment):
                insert = align_point
            text_anchor = "middle" if horizontal_alignment in {1, 4} else "end" if horizontal_alignment == 2 else "start"
            dominant_baseline = "central" if vertical_alignment == 2 else "text-after-edge" if vertical_alignment == 3 else "alphabetic"
        display_content = content.replace("\\P", "\n")
        normalized_content = "\n".join(
            line.strip()
            for line in display_content.split("\n")
        ).strip()
        normalized_line_whitespace = normalized_content != display_content.strip()
        content = normalized_content
        if not content:
            return
        x, y = _xy(insert) or (0.0, 0.0)
        style_name = str(getattr(entity.dxf, "style", "Standard") or "Standard")
        font_family = "SimSun"
        cad_font = ""
        width_factor = float(getattr(entity.dxf, "width", 1) or 1)
        try:
            text_style = self.document.styles.get(style_name)
            cad_font = str(getattr(text_style.dxf, "font", "") or "")
            font_family = _browser_font_family(cad_font)
            width_factor = float(getattr(text_style.dxf, "width", width_factor) or width_factor)
        except Exception:
            pass
        item = {
            "attachmentPoint": attachment_point,
            "cadRender": True,
            "cadFont": cad_font,
            "cadBoxWidth": layout_width,
            "dominantBaseline": dominant_baseline,
            "fontFamily": font_family,
            "fontStyle": "italic" if re.search(r"\|i1(?:\||;)", raw_content, re.IGNORECASE) else "normal",
            "fontWeight": 700 if re.search(r"\|b1(?:\||;)", raw_content, re.IGNORECASE) else 400,
            "fontSize": height,
            "height": height * 1.16,
            "name": content,
            "orientation": "vertical" if 45 <= abs(rotation % 180) <= 135 else "horizontal",
            "rotation": rotation,
            "text": content,
            "textAnchor": text_anchor,
            "widthFactor": width_factor,
            "width": max(len(content.replace("\n", "")), 1) * height * width_factor,
            "x": x,
            "y": y,
            "id": self._new_id(),
            "layer": layer,
            "importedSourceHandles": _handles(handle),
            "_forceGlyphRedraw": normalized_line_whitespace,
        }
        self.texts.append(item)
        self.text_entity_items.append((entity, item))

    def _attach_text_glyphs(self):
        """使用 ezdxf 的 CAD 排版器生成文字轮廓，避免浏览器字体度量造成错位。"""
        if not self.text_entity_items:
            return
        try:
            from ezdxf.addons.drawing import Frontend, RenderContext
            from ezdxf.addons.drawing.config import Configuration
            from ezdxf.addons.drawing.recorder import Recorder

            render_context = RenderContext(self.document)
            configuration = Configuration()
            recorder = Recorder()
            entities = [entity for entity, _ in self.text_entity_items]
            Frontend(
                render_context,
                recorder,
                config=configuration,
            ).draw_entities(entities)
            records_by_handle = {}
            for record in recorder.records:
                if hasattr(record, "paths") and record.handle:
                    records_by_handle.setdefault(str(record.handle).upper(), []).append(record)

            def record_outlines(records):
                outlines = []
                for record in records:
                    if not hasattr(record, "paths"):
                        continue
                    for path in record.paths:
                        sub_paths = list(path.sub_paths()) if path.has_sub_paths else [path]
                        for sub_path in sub_paths:
                            points = [
                                {"x": float(point.x), "y": float(point.y)}
                                for point in sub_path.flattening(0.08)
                            ]
                            if len(points) >= 3:
                                outlines.append(points)
                return outlines

            for entity, item in self.text_entity_items:
                handle = str(getattr(entity.dxf, "handle", "") or "").upper()
                outlines = record_outlines(records_by_handle.get(handle, []))
                if item.pop("_forceGlyphRedraw", False):
                    outlines = []
                wrapped_text = _wrapped_cad_text(item)
                if wrapped_text != item["text"]:
                    item["text"] = wrapped_text
                    item["name"] = wrapped_text
                    outlines = []
                # INSERT 展开得到的虚拟文字可能没有自己的 handle，单独绘制可保留
                # 块变换后的准确字形位置。
                if not outlines:
                    target = entity.copy()
                    if target.dxftype() == "MTEXT":
                        # ODA 生成的动态分栏 MTEXT 和内嵌 SHX 字体声明无法由
                        # ezdxf 直接绘制；去掉分栏容器后，插入点和附件点保持不变。
                        target._columns = None
                        original_plain_text = "\n".join(
                            line.strip()
                            for line in target.plain_text().replace("\\P", "\n").split("\n")
                        ).strip()
                        if original_plain_text != item["text"]:
                            fallback_text = "\\P".join(item["text"].split("\n"))
                            if int(item.get("fontWeight") or 400) >= 700:
                                font_name = str(item.get("fontFamily") or "SimSun")
                                italic = 1 if item.get("fontStyle") == "italic" else 0
                                fallback_text = f"{{\\f{font_name}|b1|i{italic}|c134|p2;{fallback_text}}}"
                            target.text = fallback_text
                    modelspace = self.document.modelspace()
                    modelspace.add_entity(target)
                    fallback_recorder = Recorder()
                    try:
                        Frontend(
                            render_context,
                            fallback_recorder,
                            config=configuration,
                        ).draw_entities([target])
                        outlines = record_outlines(fallback_recorder.records)
                    finally:
                        modelspace.delete_entity(target)
                if outlines and not _cad_glyph_outlines_complete(item, outlines):
                    # Some SHX/CJK combinations are only partially supported
                    # by ezdxf's recorder.  For example ``PLC柜`` may yield the
                    # outline of the letter C alone.  Rendering that incomplete
                    # geometry hides the remaining characters, so use the
                    # browser text fallback for the complete semantic string.
                    outlines = []
                if outlines:
                    item["glyphPaths"] = outlines
                    item["glyphFillRule"] = "evenodd"
        except Exception as exc:
            if len(self.warnings) < 50:
                self.warnings.append(f"CAD 文字轮廓生成失败: {exc}")

    def _add_insert(self, entity, handle, layer, depth):
        x, y = _xy(entity.dxf.insert) or (0.0, 0.0)
        block = {
            "id": self._new_id(),
            "name": str(entity.dxf.name),
            "x": x,
            "y": y,
            "width": abs(float(getattr(entity.dxf, "xscale", 1) or 1)),
            "height": abs(float(getattr(entity.dxf, "yscale", 1) or 1)),
            "rotation": float(getattr(entity.dxf, "rotation", 0) or 0),
            "layer": layer,
            "importedSourceHandles": _handles(handle),
        }
        self.blocks.append(block)
        for attribute in getattr(entity, "attribs", []):
            self._parse_entity(attribute, handle, depth + 1)
        try:
            for child in entity.virtual_entities():
                self._parse_entity(child, handle, depth + 1)
        except Exception as exc:
            if len(self.warnings) < 50:
                self.warnings.append(f"图块 {entity.dxf.name}({handle or '无句柄'}) 展开失败：{exc}")

    def _add_hatch(self, entity, handle, layer):
        from ezdxf.path import from_hatch

        start_index = len(self.paths)
        for boundary in from_hatch(entity):
            points = list(boundary.flattening(distance=0.5, segments=8))
            self._add_path("HATCH", points, True, handle, layer)
        for item in self.paths[start_index:]:
            item["hatch"] = True
            item["solidFill"] = bool(getattr(entity.dxf, "solid_fill", 0))
            item["patternName"] = str(getattr(entity.dxf, "pattern_name", "") or "")
            item["cadRender"] = True
            if item["solidFill"]:
                item["fillStyle"] = "solid"
                item["fillColor"] = "#111111"
            else:
                item["fillStyle"] = "hatch"
                pattern_lines = getattr(getattr(entity, "pattern", None), "lines", None) or []
                hatch_angles = []
                for pattern_line in pattern_lines:
                    angle = float(getattr(pattern_line, "angle", 0) or 0) % 180
                    if not any(abs(angle - existing) < 0.01 for existing in hatch_angles):
                        hatch_angles.append(angle)
                if not hatch_angles:
                    hatch_angles = [float(getattr(entity.dxf, "pattern_angle", 45) or 45) % 180]
                item["hatchAngles"] = hatch_angles
                item["hatchAngle"] = hatch_angles[0]
                # The browser draws continuous lines rather than the source
                # pattern's dash segments. Large ANSI31 areas therefore need
                # a wider equivalent interval to retain the DWG's visual
                # density after fitting the sheet to the board.
                pattern_scale = float(getattr(entity.dxf, "pattern_scale", 1) or 1)
                spacing_multiplier = 4.0 if item["patternName"].upper() == "ANSI31" else 3.0
                item["hatchSpacing"] = max(pattern_scale * spacing_multiplier, 0.5)

    def _parse_simple_geometry(self, entity, handle, layer):
        entity_type = entity.dxftype()
        if entity_type == "POINT":
            x, y = _xy(entity.dxf.location) or (0.0, 0.0)
            radius = 0.22
            self._add_path(
                entity_type,
                [(x, y - radius), (x + radius, y), (x, y + radius), (x - radius, y)],
                True,
                handle,
                layer,
                {
                    "cadRender": True,
                    "fillStyle": "solid",
                    "fillColor": "#111111",
                    "strokeColor": "#111111",
                    "strokeWidth": 0.2,
                },
            )
            return
        points = []
        # SOLID/TRACE 的 DXF 顶点顺序是 0,1,3,2；按 0,1,2,3 会形成自交多边形，
        # 也是此前图例中黑色三角形和接线箱填充缺失的根因。
        vertex_names = ("vtx0", "vtx1", "vtx3", "vtx2") if entity_type in {"SOLID", "TRACE"} else ("vtx0", "vtx1", "vtx2", "vtx3")
        for name in vertex_names:
            value = getattr(entity.dxf, name, None)
            if value is not None:
                points.append(value)
        render_style = None
        if entity_type in {"SOLID", "TRACE"}:
            render_style = {
                "cadRender": True,
                "fillStyle": "solid",
                "fillColor": "#111111",
                "strokeColor": "#111111",
                "strokeWidth": 0.5,
            }
        self._add_path(entity_type, points, True, handle, layer, render_style)

    def _new_id(self):
        value = self.next_id
        self.next_id += 1
        return value


def _browser_font_family(cad_font):
    """把 CAD 字体文件/SHX 样式映射为浏览器可用字体，同时由 cadFont 保留原值。"""
    name = Path(str(cad_font or "")).stem.lower()
    if any(token in name for token in ("simsun", "song", "hzfs", "hztxt")):
        return "SimSun"
    if any(token in name for token in ("simhei", "heiti")):
        return "SimHei"
    if name in {"simplex", "txt", "romans", "romand"}:
        return "Arial"
    return "SimSun"


def _is_single_line_cad_identifier(value):
    """Equipment / marker tags must stay on one line even in a tight MTEXT box.

    Examples: ``SC1``, ``025``, ``S3-S5``, ``AT-110``, ``AT-110 1``.
    """
    raw = str(value or "").strip()
    if not raw or "\n" in raw:
        return False
    compact = re.sub(r"\s+", "", raw)
    return bool(
        re.fullmatch(
            r"(?:[A-Za-z]{1,8}[-_]?)?\d{1,4}(?:[-~](?:[A-Za-z]{0,8}[-_]?)?\d{1,4})*",
            compact,
        )
    )


def _is_equipment_nameplate_label(value):
    """True only for in-box equipment codes such as ``AT-110`` / ``AT-110 1``.

    Deliberately excludes connector labels (``SC2``), bank ranges (``S3-S5``),
    short markers (``S03`` / ``025``) and Chinese captions — those must keep
    their CAD insert seats.
    """
    compact = re.sub(r"\s+", "", str(value or "").strip()).upper()
    return bool(re.fullmatch(r"[A-Z]{2,8}-\d{2,5}", compact))


def _wrapped_cad_text(item):
    """按 MTEXT 的排版宽度补充显式换行，避免长文字越过标题栏单元格。"""
    text = str(item.get("text") or "")
    if _is_single_line_cad_identifier(text):
        # Short CAD identifiers such as SC1/SC2, AT-110 1 and zero-padded
        # test-point numbers such as 025 are single labels even when their
        # MTEXT width is narrowly fitted around the glyphs.  Treating the
        # width as a prose wrapping box incorrectly stacks the final digit
        # (or the equipment suffix) onto a second, visually smaller line.
        return text
    box_width = float(item.get("cadBoxWidth") or 0)
    char_width = float(item.get("fontSize") or 0) * float(item.get("widthFactor") or 1)
    # Bold SimSun title-block values are laid out wider than their nominal
    # MTEXT height. Account for that before wrapping so the company name uses
    # the same balanced two-line layout as AutoCAD instead of a 17/7 split.
    if int(item.get("fontWeight") or 400) >= 700:
        char_width *= 1.44
    if box_width <= 0 or char_width <= 0:
        return text
    # AutoCAD's SHX/CJK layout permits a glyph to use slightly less than the
    # nominal character width. Rounding matches the displayed MTEXT columns;
    # flooring incorrectly turns short labels such as "放空立管" into one
    # character per line instead of two balanced lines.
    characters_per_line = max(round(box_width / char_width), 1)
    lines = []
    for paragraph in text.split("\n"):
        if len(paragraph) * char_width <= box_width * 1.08:
            lines.append(paragraph)
            continue
        lines.extend(
            paragraph[index:index + characters_per_line]
            for index in range(0, len(paragraph), characters_per_line)
        )
    return "\n".join(lines)


def _cad_glyph_outlines_complete(item, outlines):
    """Reject recorder output that contains only a fragment of the text."""
    points = [point for outline in outlines for point in outline]
    if not points:
        return False
    outline_width = max(float(point["x"]) for point in points) - min(float(point["x"]) for point in points)
    outline_height = max(float(point["y"]) for point in points) - min(float(point["y"]) for point in points)
    text = str(item.get("text") or item.get("name") or "")
    longest_line = max((len(line) for line in text.split("\n")), default=1)
    font_size = max(float(item.get("fontSize") or 0), 0.01)
    width_factor = max(abs(float(item.get("widthFactor") or 1)), 0.01)
    nominal_width = longest_line * font_size * width_factor
    return outline_width >= nominal_width * 0.4 and outline_height >= font_size * 0.25


def _normalize_hatch_pattern_spacing(paths):
    """Scale CAD hatch spacing to the visible area without over-densifying it."""
    for path in paths:
        if str(path.get("fillStyle") or "").lower() != "hatch":
            continue
        points = path.get("points") or []
        if len(points) < 3:
            continue
        xs = [float(point.get("x") or 0) for point in points]
        ys = [float(point.get("y") or 0) for point in points]
        short_side = min(max(xs) - min(xs), max(ys) - min(ys))
        if short_side <= 0:
            continue
        # Preserve the source scale on large ANSI31 areas (for example a gas
        # station canopy).  The former short-side/18 ceiling compressed these
        # broad DWG hatches to 40–48px regardless of their CAD scale, making
        # them visibly denser than the source drawing.
        visual_floor = max(3.0, min(12.0, short_side / 16.0))
        is_large_ansi31 = str(path.get("patternName") or "").upper() == "ANSI31" and short_side >= 180
        visual_ceiling = (
            max(24.0, min(120.0, short_side / 8.0))
            if is_large_ansi31
            else max(12.0, min(48.0, short_side / 18.0))
        )
        current = float(path.get("hatchSpacing") or visual_floor)
        path["hatchSpacing"] = max(visual_floor, min(current, visual_ceiling))


def _normalize_canvas(parsed, width, height, target_area=None):
    _discard_entities_outside_sheet_frame(parsed)
    min_x, min_y, max_x, max_y = _drawing_bounds_with_margin(parsed)
    source_width = max(max_x - min_x, 1.0)
    source_height = max(max_y - min_y, 1.0)
    if isinstance(target_area, dict):
        area_x = float(target_area.get("x") or 0)
        area_y = float(target_area.get("y") or 0)
        area_width = max(float(target_area.get("width") or width), 1)
        area_height = max(float(target_area.get("height") or height), 1)
        scale_x = area_width / source_width
        scale_y = area_height / source_height
    else:
        padding = max(min(width, height) * 0.025, 10)
        scale = min((width - padding * 2) / source_width, (height - padding * 2) / source_height)
        area_x = padding
        area_y = padding
        scale_x = scale
        scale_y = scale

    def transform(x, y):
        return (
            area_x + (float(x) - min_x) * scale_x,
            area_y + (max_y - float(y)) * scale_y,
        )

    for path in parsed["paths"]:
        for point in path["points"]:
            point["x"], point["y"] = transform(point["x"], point["y"])
        if path.get("strokeWidth") is not None:
            path["strokeWidth"] = max(float(path["strokeWidth"]) * min(scale_x, scale_y), 0.5)
        if path.get("hatchSpacing") is not None:
            path["hatchSpacing"] = max(float(path["hatchSpacing"]) * min(scale_x, scale_y), 3)
        if path.get("borderTileSize") is not None:
            # Preserve the CAD LP scale as a whole number.  A small LP size is
            # valid and must not be lifted to the old four-unit minimum.
            path["borderTileSize"] = round(float(path["borderTileSize"]) * min(scale_x, scale_y))
        if path.get("borderTileSpacing") is not None:
            path["borderTileSpacing"] = max(float(path["borderTileSpacing"]) * min(scale_x, scale_y), 8)
    _normalize_hatch_pattern_spacing(parsed["paths"])
    for text in parsed["texts"]:
        render_x, render_y = transform(text["x"], text["y"])
        glyph_paths = text.get("glyphPaths") or []
        if glyph_paths:
            for outline in glyph_paths:
                for point in outline:
                    point["x"], point["y"] = transform(point["x"], point["y"])
                    point["x"] -= render_x
                    point["y"] -= render_y
            glyph_xs = [point["x"] for outline in glyph_paths for point in outline]
            glyph_ys = [point["y"] for outline in glyph_paths for point in outline]
            text["glyphBounds"] = {
                "x": min(glyph_xs),
                "y": min(glyph_ys),
                "width": max(glyph_xs) - min(glyph_xs),
                "height": max(glyph_ys) - min(glyph_ys),
            }
        text["x"], text["y"] = render_x, render_y
        if text.get("cadRender"):
            text["fontSize"] = min(max(float(text["fontSize"]) * scale_y, 4), 48)
            text["widthFactor"] = float(text.get("widthFactor") or 1) * scale_x / max(scale_y, 0.0001)
            text["cadBoxWidth"] = float(text.get("cadBoxWidth") or 0) * scale_x
        else:
            text["fontSize"] = min(max(float(text["fontSize"]) * min(scale_x, scale_y), 6), 48)
        text_lines = str(text.get("text") or "").split("\n")
        text["height"] = text["fontSize"] * 1.16 * max(len(text_lines), 1)
        text["width"] = (
            max(max((len(line) for line in text_lines), default=1), 1)
            * text["fontSize"]
            * float(text.get("widthFactor") or 1)
        )
        if glyph_paths and abs(float(text.get("rotation") or 0) % 180) < 0.01:
            # Keep the recorder's exact CAD width. Only constrain an outline
            # when it actually exceeds MTEXT's layout box, so long title-block
            # values stay in their cell without narrowing every Chinese label.
            glyph_bounds = text.get("glyphBounds") or {}
            glyph_width = float(glyph_bounds.get("width") or 0)
            box_width = float(text.get("cadBoxWidth") or 0)
            if box_width > 0 and glyph_width > box_width * 1.02:
                horizontal_scale = box_width / glyph_width
                for outline in glyph_paths:
                    for point in outline:
                        point["x"] *= horizontal_scale
                glyph_xs = [point["x"] for outline in glyph_paths for point in outline]
                glyph_ys = [point["y"] for outline in glyph_paths for point in outline]
                text["glyphBounds"] = {
                    "x": min(glyph_xs),
                    "y": min(glyph_ys),
                    "width": max(glyph_xs) - min(glyph_xs),
                    "height": max(glyph_ys) - min(glyph_ys),
                }
    _center_multiline_cell_texts(parsed)
    for block in parsed["blocks"]:
        block["x"], block["y"] = transform(block["x"], block["y"])
        block["width"] = max(float(block["width"]) * scale_x, 1)
        block["height"] = max(float(block["height"]) * scale_y, 1)


def _center_multiline_cell_texts(parsed):
    """Center narrow multiline MTEXT in the table cell that encloses it."""
    vertical_segments = []
    for path in parsed.get("paths") or []:
        points = path.get("points") or []
        if len(points) != 2:
            continue
        first, second = points
        if abs(float(first["x"]) - float(second["x"])) > 0.8:
            continue
        vertical_segments.append(
            (
                (float(first["x"]) + float(second["x"])) / 2,
                min(float(first["y"]), float(second["y"])),
                max(float(first["y"]), float(second["y"])),
            )
        )

    for text in parsed.get("texts") or []:
        if "\n" not in str(text.get("text") or ""):
            continue
        if float(text.get("cadBoxWidth") or 0) > 0:
            continue
        bounds = text.get("glyphBounds") or {}
        glyph_width = float(bounds.get("width") or 0)
        glyph_height = float(bounds.get("height") or 0)
        if glyph_width <= 0 or glyph_height <= 0:
            continue
        glyph_center_x = float(text["x"]) + float(bounds.get("x") or 0) + glyph_width / 2
        glyph_center_y = float(text["y"]) + float(bounds.get("y") or 0) + glyph_height / 2
        boundaries = sorted(
            x
            for x, top, bottom in vertical_segments
            if top - 1 <= glyph_center_y <= bottom + 1
        )
        left = max((x for x in boundaries if x <= glyph_center_x), default=None)
        right = min((x for x in boundaries if x >= glyph_center_x), default=None)
        if left is None or right is None or right - left <= 1:
            continue
        cell_width = right - left
        if cell_width > max(glyph_width * 3, 120):
            continue
        target_width = cell_width * 0.88
        if glyph_width > target_width:
            local_center = float(bounds.get("x") or 0) + glyph_width / 2
            horizontal_scale = target_width / glyph_width
            for outline in text.get("glyphPaths") or []:
                for point in outline:
                    point["x"] = local_center + (float(point["x"]) - local_center) * horizontal_scale
            bounds["x"] = local_center - target_width / 2
            bounds["width"] = target_width
            glyph_width = target_width
            text["glyphBounds"] = bounds
            glyph_center_x = float(text["x"]) + local_center
        text["x"] += (left + right) / 2 - glyph_center_x


def _drawing_bounds(parsed):
    # Reports generated from a common CAD template use four TRACE entities for
    # the outer sheet frame.  Some exported DWGs also contain a stray hatch or
    # construction marker far away from that frame.  Including the marker in
    # the global min/max compresses the complete drawing into one side of the
    # board (most visible with multi-DWG imports).  Prefer the explicit sheet
    # frame when it is present; otherwise retain the generic entity bounds.
    sheet_frame = _sheet_frame_bounds(parsed)
    if sheet_frame:
        return sheet_frame

    xs = []
    ys = []
    for path in parsed["paths"]:
        for point in path["points"]:
            xs.append(float(point["x"]))
            ys.append(float(point["y"]))
    for item in parsed["texts"] + parsed["blocks"]:
        xs.append(float(item.get("x", 0)))
        ys.append(float(item.get("y", 0)))
    if not xs or not ys:
        return 0.0, 0.0, 1600.0, 1280.0
    return min(xs), min(ys), max(xs), max(ys)


def _sheet_frame_bounds(parsed):
    trace_points = [
        point
        for path in parsed["paths"]
        if str(path.get("name") or "").upper() == "TRACE"
        for point in path.get("points") or []
    ]
    if len(trace_points) < 8:
        return None
    trace_xs = [float(point["x"]) for point in trace_points]
    trace_ys = [float(point["y"]) for point in trace_points]
    min_x, max_x = min(trace_xs), max(trace_xs)
    min_y, max_y = min(trace_ys), max(trace_ys)
    if max_x - min_x <= 1 or max_y - min_y <= 1:
        return None
    return min_x, min_y, max_x, max_y


def _discard_entities_outside_sheet_frame(parsed):
    """Remove isolated CAD debris that sits far outside an explicit sheet frame."""
    frame = _sheet_frame_bounds(parsed)
    if not frame:
        return
    min_x, min_y, max_x, max_y = frame
    padding = max(max_x - min_x, max_y - min_y) * 0.12
    left, top = min_x - padding, min_y - padding
    right, bottom = max_x + padding, max_y + padding

    def inside(x, y):
        return left <= float(x) <= right and top <= float(y) <= bottom

    parsed["paths"] = [
        path
        for path in parsed["paths"]
        if any(inside(point["x"], point["y"]) for point in path.get("points") or [])
    ]
    parsed["texts"] = [item for item in parsed["texts"] if inside(item.get("x", 0), item.get("y", 0))]
    parsed["blocks"] = [item for item in parsed["blocks"] if inside(item.get("x", 0), item.get("y", 0))]


def _drawing_bounds_with_margin(parsed):
    """与前端 cadRenderer.renderDrawingSvg 的 viewBox 边距保持一致。"""
    min_x, min_y, max_x, max_y = _drawing_bounds(parsed)
    source_width = max(max_x - min_x, 1.0)
    source_height = max(max_y - min_y, 1.0)
    margin = max(source_width, source_height) * 0.025
    return min_x - margin, min_y - margin, max_x + margin, max_y + margin


def _extract_binding_source(source):
    source = source if isinstance(source, dict) else {}
    canvas = _active_canvas(source)
    report_tables = source.get("reportTables") or canvas.get("reportTables") or DEFAULT_REPORT_TABLES
    report_tables = {
        key: copy.deepcopy(report_tables.get(key) or [])
        for key in DEFAULT_REPORT_TABLES
    }
    cover = (
        source.get("cover")
        or source.get("coverReportInfo")
        or canvas.get("coverReportInfo")
        or {}
    )
    chapter_order = canvas.get("reportChapterOrder") or source.get("reportChapterOrder") or [
        "cover",
        "general",
        "overview",
        "power",
        "grounding",
        "transition",
        "spd",
        "spdTest",
    ]
    return {
        "reportTables": report_tables,
        "testPoints": copy.deepcopy(canvas.get("testPoints") or []),
        "boardWidth": canvas.get("boardWidth"),
        "boardHeight": canvas.get("boardHeight"),
        "coverReportInfo": copy.deepcopy(cover),
        "reportChapterOrder": copy.deepcopy(chapter_order),
    }


def _active_canvas(source):
    if not isinstance(source, dict):
        return {}
    if source.get("boardWidth") or source.get("paths") or source.get("testPoints"):
        return source
    for key in ("workspace", "drawingWorkspace", "drawingData", "legendWorkspace"):
        nested = source.get(key)
        if isinstance(nested, dict):
            canvas = _active_canvas(nested)
            if canvas:
                return canvas
    legend = source.get("legend")
    if isinstance(legend, dict):
        canvas = _active_canvas(legend)
        if canvas:
            return canvas
    tab_data = source.get("tabData")
    if isinstance(tab_data, dict) and tab_data:
        active_id = source.get("activeTabId")
        if active_id is not None and isinstance(tab_data.get(str(active_id)), dict):
            return tab_data[str(active_id)]
        return next((item for item in tab_data.values() if isinstance(item, dict)), {})
    return {}


def _bind_report_rows(parsed, report_tables, existing_points, old_width, old_height, new_width, new_height):
    used_ids = {item["id"] for key in ("paths", "texts", "blocks") for item in parsed[key]}
    next_id = max(used_ids, default=0) + 1
    handle_targets = _handle_targets(parsed)
    text_candidates = parsed["texts"]
    existing_by_id = {str(point.get("id")): point for point in existing_points if point.get("id") is not None}
    existing_by_label = {}
    for point in existing_points:
        # New workspaces display the Word table marker while retaining the
        # original CAD marker separately.  Index every marker representation
        # so re-importing a DWG preserves the user's adjusted position.
        for key in ("label", "reportMarker", "sourceMarker"):
            value = str(point.get(key) or "").strip()
            if value:
                existing_by_label.setdefault(value, point)
    test_points = []
    unmatched = []
    bound_rows = 0
    # A solid CAD marker must belong to one numbered callout only.  Without
    # this guard, a slightly farther label can claim the same HATCH triangle
    # after its nearer neighbour has already been matched.  The later point
    # then falls back to a synthetic front-end arrow, so the drawing displays
    # an extra arrow beside the original CAD one.
    used_marker_path_ids = set()

    _infer_missing_report_markers(report_tables, text_candidates)

    for report_type, rows in report_tables.items():
        for row in rows:
            marker = str(row.get("marker") or row.get("label") or "").strip()
            if not marker:
                # Some official tables intentionally leave the number column blank.
                # Keep the row in the report, but do not invent an unbound canvas point.
                continue
            existing = existing_by_id.get(str(row.get("id"))) or existing_by_label.get(marker)
            raw_locations = set()
            if report_type == "grounding":
                raw_locations, _ = _grounding_location_marker_candidates(row)
            if report_type == "grounding" and existing is not None:
                existing_source = _compact_marker_text(existing.get("sourceMarker"))
                if raw_locations and existing_source not in raw_locations:
                    # A workspace imported by an older matcher may have saved
                    # row 8 at CAD 008 even though the row explicitly says
                    # 编号010.  Preserve manual geometry only when its stored
                    # physical source still agrees with the current report.
                    existing = None
            # 前端工作区中的检测点通常已经人工校正过位置和方向。
            # 优先保留该坐标，避免再次解析 DWG 后因边界归一化产生偏移。
            target = _scaled_existing_point(existing, old_width, old_height, new_width, new_height)
            if target is None:
                target = _target_from_existing(existing, handle_targets)
            if target is None and marker:
                available_paths = [
                    path
                    for path in parsed["paths"]
                    if int(path.get("id") or 0) not in used_marker_path_ids
                ]
                target = _match_marker_text(
                    marker,
                    row,
                    text_candidates,
                    available_paths,
                    report_type=report_type,
                )

            # Range annotations intentionally serve several report rows and
            # are collapsed below.  Ordinary labels, however, consume their
            # actual triangular HATCH parts so an adjacent label cannot reuse
            # them.
            if target is not None and not _parse_marker_range(target.get("markerLabel")):
                used_marker_path_ids.update(
                    int(item_id)
                    for item_id in target.get("sourceElementIds") or []
                    if int(item_id) in {int(path.get("id") or 0) for path in parsed["paths"]}
                )

            row_id = _unique_row_id(row.get("id"), used_ids, next_id)
            if row_id >= next_id:
                next_id = row_id + 1
            used_ids.add(row_id)
            row["id"] = row_id

            if target is None:
                unmatched.append({
                    "id": row_id,
                    "marker": marker,
                    "reportType": report_type,
                    "reason": "未在图纸中找到对应检测点编号",
                })
                continue

            target_marker = str(target.get("markerLabel") or "").strip()
            report_marker, report_row = _report_marker_for_display_label(
                target_marker,
                marker,
                report_type,
                report_tables,
            )
            fields = _report_fields(report_row or row, report_type)
            existing_binding = copy.deepcopy((existing or {}).get("binding"))
            target_binding = {
                "id": target.get("id"),
                "kind": target.get("kind") or "text",
            } if target.get("id") is not None else None
            # The Word table marker is the user-facing sequence number.  The
            # CAD marker remains stable source metadata used only to find the
            # physical symbol (for example table row ``1`` can match
            # ``编号 DL-005`` / CAD label ``005``).  Keeping both prevents a
            # later insertion/renumber from breaking the geometric binding.
            display_marker = report_marker or marker
            point = {
                "label": display_marker,
                "reportMarker": report_marker,
                "sourceMarker": target_marker or str((existing or {}).get("sourceMarker") or "").strip(),
                "imported": True,
                "side": (existing or {}).get("side") or target.get("side") or "right",
                "sourceElementIds": target.get("sourceElementIds") or [],
                "sourceHandles": target.get("sourceHandles") or [],
                "size": (existing or {}).get("size") or target.get("size") or 0.38,
                "x": target["x"],
                "y": target["y"],
                "binding": existing_binding or target_binding,
                "id": row_id,
                "reportFields": fields,
                "reportType": report_type,
            }
            if target.get("sourceTrianglePoints"):
                point["sourceTrianglePoints"] = copy.deepcopy(target["sourceTrianglePoints"])
            if point["binding"] is None:
                point.pop("binding")
            test_points.append(point)
            bound_rows += 1

    if not test_points:
        for text in _continuous_numeric_point_texts(text_candidates):
            label = str(text.get("text") or "").strip()
            target = _marker_target_from_text(text, parsed["paths"])
            point_id = next_id
            next_id += 1
            point = {
                "label": label,
                "imported": True,
                "side": target.get("side") or "right",
                "sourceElementIds": target.get("sourceElementIds") or [],
                "sourceHandles": target.get("sourceHandles") or [],
                "size": target.get("size") or 0.38,
                "x": target["x"],
                "y": target["y"],
                "binding": {"id": text["id"], "kind": "text"},
                "id": point_id,
                "reportFields": {},
                "reportType": "",
            }
            if target.get("sourceTrianglePoints"):
                point["sourceTrianglePoints"] = copy.deepcopy(target["sourceTrianglePoints"])
            test_points.append(point)

    # A CAD annotation such as D100-D101 or D100-101 denotes one physical
    # symbol serving a contiguous run of report markers.  Keep that symbol as
    # one visible, special test point and retain its individual report rows as
    # non-visual companions.
    test_points = _collapse_declared_range_test_points(test_points, parsed.get("texts") or [])
    test_points = _collapse_paired_flange_test_points(test_points, parsed.get("paths") or [])

    range_points = _declared_range_test_points(test_points, text_candidates, parsed["paths"], next_id)
    test_points.extend(range_points)

    return {
        "testPoints": test_points,
        "reportTables": report_tables,
        "unmatched": unmatched,
        "boundRows": bound_rows,
        "nextId": next_id,
    }


def _parse_marker_range(value):
    """Return every marker in a compact CAD range, e.g. D1-30 or D1-D30."""
    compact = re.sub(r"\s+", "", str(value or "")).upper()
    match = re.fullmatch(r"([A-Z]?)(\d+)(?:-|－|—|~|～|至)([A-Z]?)(\d+)", compact)
    if not match:
        return None
    start_prefix, start_text, end_prefix, end_text = match.groups()
    end_prefix = end_prefix or start_prefix
    if start_prefix != end_prefix:
        return None
    start, end = int(start_text), int(end_text)
    # A marker range is a compact symbol label, never an unbounded numeric
    # sequence.  Avoid treating arbitrary dimensions as thousands of points.
    if abs(end - start) > 500:
        return None
    width = max(len(start_text), len(end_text)) if (start_text.startswith("0") or end_text.startswith("0")) else 1
    return [f"{start_prefix}{str(number).zfill(width)}" for number in range(min(start, end), max(start, end) + 1)]


def _marker_matches_report_type(marker, report_type):
    """Keep prefixed CAD ranges bound to their corresponding Word table."""
    prefix_match = re.match(r"\s*([A-Z])", str(marker or "").upper())
    if not prefix_match:
        return True
    prefix = prefix_match.group(1)
    if prefix == "D":
        return report_type == "transition"
    if prefix == "S":
        # Both SPD detail and SPD test tables use S-prefixed CAD points.
        return report_type in {"spd", "spdTest"}
    return True


def _compact_range_label(markers):
    return f"{markers[0]}-{markers[-1]}" if len(markers) > 1 else markers[0]


def _collapse_declared_range_test_points(test_points, texts):
    """Collapse report rows attached to one explicit CAD range label."""
    ranges = []
    for text in texts:
        markers = _parse_marker_range(text.get("text"))
        if markers and text.get("id") is not None:
            ranges.append((int(text["id"]), str(text.get("text") or "").strip(), markers))
    if not ranges:
        return test_points

    consumed_ids = set()
    result = []
    for text_id, _display_label, range_markers in ranges:
        marker_order = {marker: index for index, marker in enumerate(range_markers)}
        members = [
            point for point in test_points
            if point.get("id") not in consumed_ids
            and str(point.get("label") or "").strip() in marker_order
            and _marker_matches_report_type(point.get("label"), point.get("reportType"))
            and text_id in {int(item_id) for item_id in point.get("sourceElementIds") or [] if str(item_id).strip().isdigit()}
        ]
        if len(members) < 2:
            continue
        members.sort(key=lambda point: marker_order[str(point.get("label") or "").strip()])
        primary = members[0]
        primary_marker = str(primary.get("reportMarker") or primary.get("label") or "").strip()
        visible_point = {
            **primary,
            "label": _compact_range_label(range_markers),
            "reportMarker": primary_marker,
            "reportMarkers": range_markers,
            "reportOnly": False,
            "specialRange": True,
        }
        result.append(visible_point)
        consumed_ids.add(primary.get("id"))
        for member in members[1:]:
            marker = str(member.get("reportMarker") or member.get("label") or "").strip()
            result.append({
                **member,
                "label": marker,
                "reportMarker": marker,
                "reportOnly": True,
                "sourceElementIds": [],
                "sourceHandles": [],
                "visualTestPointId": primary.get("id"),
            })
            consumed_ids.add(member.get("id"))
    return result + [point for point in test_points if point.get("id") not in consumed_ids]


def _declared_range_test_points(existing_points, texts, paths, next_id):
    """Expose range labels even when no Word table rows are available yet."""
    points = []
    represented_text_ids = {
        int(item_id)
        for point in existing_points
        for item_id in point.get("sourceElementIds") or []
        if str(item_id).strip().isdigit()
    }
    for text in texts:
        markers = _parse_marker_range(text.get("text"))
        if not markers or int(text.get("id") or 0) in represented_text_ids:
            continue
        target = _marker_target_from_text(text, paths)
        points.append({
            "id": next_id,
            "label": _compact_range_label(markers),
            "reportMarker": markers[0],
            "reportMarkers": markers,
            "specialRange": True,
            "imported": True,
            "side": target.get("side") or "right",
            "sourceElementIds": target.get("sourceElementIds") or [],
            "sourceHandles": target.get("sourceHandles") or [],
            "size": target.get("size") or 0.38,
            "x": target["x"],
            "y": target["y"],
            "binding": {"id": text["id"], "kind": "text"},
            "reportFields": {},
            "reportType": "",
        })
        next_id += 1
    return points


def _collapse_paired_flange_test_points(test_points, paths):
    """Represent two consecutive flange rows as one visible point while retaining both report rows."""
    result = []
    index = 0
    while index < len(test_points):
        first = test_points[index]
        second = test_points[index + 1] if index + 1 < len(test_points) else None
        first_match = re.fullmatch(r"([A-Za-z]?)(\d+)", str(first.get("label") or "").strip())
        second_match = re.fullmatch(r"([A-Za-z]?)(\d+)", str((second or {}).get("label") or "").strip())
        first_fields = first.get("reportFields") or {}
        second_fields = (second or {}).get("reportFields") or {}
        shared_source_ids = set(first.get("sourceElementIds") or []) & set((second or {}).get("sourceElementIds") or [])
        equipment_text = " ".join(
            str(value or "")
            for value in (
                first_fields.get("equipmentName"),
                second_fields.get("equipmentName"),
                first_fields.get("installLocation"),
                second_fields.get("installLocation"),
            )
        )
        is_consecutive_flange_pair = (
            second is not None
            and first_match is not None
            and second_match is not None
            and first_match.group(1).upper() == second_match.group(1).upper()
            and int(second_match.group(2)) == int(first_match.group(2)) + 1
            and first.get("reportType") == second.get("reportType")
            # Transition-resistance drawings frequently place independent
            # points on both sides of one flange symbol (D49/D50, D67/D68,
            # etc.).  Those must remain two visible points.  Only the legacy
            # equipotential-bonding table uses one physical flange symbol for
            # a paired report row.
            and first.get("reportType") == "equipotentialBonding"
            and ("法兰" in equipment_text or bool(shared_source_ids))
            and math.hypot(
                float(first.get("x") or 0) - float(second.get("x") or 0),
                float(first.get("y") or 0) - float(second.get("y") or 0),
            ) <= 120
        )
        if not is_consecutive_flange_pair:
            result.append(first)
            index += 1
            continue

        midpoint = {
            "x": (float(first.get("x") or 0) + float(second.get("x") or 0)) / 2,
            "y": (float(first.get("y") or 0) + float(second.get("y") or 0)) / 2,
        }
        symbol_candidates = []
        for path in paths:
            if not path.get("closed"):
                continue
            box = _path_box(path)
            if box["width"] > 160 or box["height"] > 100 or box["width"] * box["height"] < 8:
                continue
            center = {"x": box["x"] + box["width"] / 2, "y": box["y"] + box["height"] / 2}
            distance = math.hypot(center["x"] - midpoint["x"], center["y"] - midpoint["y"])
            if distance <= 120:
                symbol_candidates.append((distance, path, center))
        nearest_symbol = min(symbol_candidates, key=lambda item: item[0]) if symbol_candidates else None
        related_symbol_paths = []
        if nearest_symbol:
            nearest_box = _path_box(nearest_symbol[1])
            nearby_radius = max(25, nearest_box["width"] * 1.5, nearest_box["height"] * 1.5)
            for path in paths:
                box = _path_box(path)
                center_x = box["x"] + box["width"] / 2
                center_y = box["y"] + box["height"] / 2
                if (
                    box["width"] <= 160
                    and box["height"] <= 100
                    and math.hypot(center_x - nearest_symbol[2]["x"], center_y - nearest_symbol[2]["y"]) <= nearby_radius
                ):
                    related_symbol_paths.append(path)
        source_element_ids = list(dict.fromkeys(
            list(first.get("sourceElementIds") or [])
            + list(second.get("sourceElementIds") or [])
            + [path.get("id") for path in related_symbol_paths if path.get("id") is not None]
        ))
        source_handles = list(dict.fromkeys(
            list(first.get("sourceHandles") or [])
            + list(second.get("sourceHandles") or [])
            + [
                handle
                for path in related_symbol_paths
                for handle in path.get("importedSourceHandles") or []
            ]
        ))
        first_marker = str(first.get("label") or "").strip()
        second_marker = str(second.get("label") or "").strip()
        marker_prefix = first_match.group(1).upper()
        display_label = (
            f"{marker_prefix}{int(first_match.group(2))}"
            f"~{marker_prefix}{int(second_match.group(2))}"
        )
        visible_point = {
            **first,
            "label": display_label,
            "reportMarker": first_marker,
            "reportMarkers": [first_marker, second_marker],
            "reportOnly": False,
            "sourceElementIds": source_element_ids,
            "sourceHandles": source_handles,
            "x": nearest_symbol[2]["x"] if nearest_symbol else midpoint["x"],
            "y": nearest_symbol[2]["y"] if nearest_symbol else midpoint["y"],
        }
        report_only_point = {
            **second,
            "label": second_marker,
            "reportMarker": second_marker,
            "reportOnly": True,
            "sourceElementIds": [],
            "sourceHandles": [],
            "visualTestPointId": visible_point.get("id"),
            "x": visible_point["x"],
            "y": visible_point["y"],
        }
        result.extend([visible_point, report_only_point])
        index += 2
    return result


def _assign_interaction_groups(parsed, test_points):
    """Keep the primitives expanded from one CAD marker interactive as one symbol."""
    items = parsed.get("paths", []) + parsed.get("texts", []) + parsed.get("blocks", [])
    for point in test_points:
        source_ids = {
            int(item_id)
            for item_id in point.get("sourceElementIds") or []
            if str(item_id).strip().isdigit()
        }
        handles = {
            str(handle).upper()
            for handle in point.get("sourceHandles") or []
            if str(handle).strip()
        }
        if not source_ids and not handles:
            continue
        group_id = f"cad-marker-{point.get('label') or point.get('id')}"
        members = []
        for item in items:
            if source_ids:
                if int(item.get("id") or 0) in source_ids:
                    members.append(item)
                continue
            item_handles = {
                str(handle).upper()
                for handle in item.get("importedSourceHandles") or []
                if str(handle).strip()
            }
            if handles.intersection(item_handles):
                members.append(item)
        if len(members) < 2:
            continue
        for item in members:
            item["interactionGroupId"] = group_id
        point["interactionGroupId"] = group_id


def _assign_sheet_frame_interaction_group(parsed):
    """Make the four TRACE strips of a CAD sheet border one selectable object.

    ODA exports the outer drawing frame as four thin, closed TRACE polygons,
    not four LINE entities.  They therefore never entered the previous marker
    grouping flow and reached the browser as four unrelated objects.
    """
    trace_paths = [
        path for path in parsed.get("paths") or []
        if str(path.get("name") or "").upper() == "TRACE" and len(path.get("points") or []) >= 3
    ]
    if len(trace_paths) < 4:
        return

    frame = _sheet_frame_bounds({"paths": trace_paths})
    if not frame:
        return
    left, top, right, bottom = frame
    frame_width = right - left
    frame_height = bottom - top
    tolerance = max(max(frame_width, frame_height) * 0.006, 1.5)

    def bounds(path):
        points = path.get("points") or []
        xs = [float(point["x"]) for point in points]
        ys = [float(point["y"]) for point in points]
        return min(xs), min(ys), max(xs), max(ys)

    top_edges = []
    bottom_edges = []
    left_edges = []
    right_edges = []
    for path in trace_paths:
        x1, y1, x2, y2 = bounds(path)
        width, height = x2 - x1, y2 - y1
        if width >= frame_width * 0.72 and height <= max(tolerance * 3, frame_height * 0.035):
            if abs(y1 - top) <= tolerance * 2:
                top_edges.append(path)
            if abs(y2 - bottom) <= tolerance * 2:
                bottom_edges.append(path)
        if height >= frame_height * 0.72 and width <= max(tolerance * 3, frame_width * 0.035):
            if abs(x1 - left) <= tolerance * 2:
                left_edges.append(path)
            if abs(x2 - right) <= tolerance * 2:
                right_edges.append(path)

    edges = [
        max(top_edges, key=lambda item: bounds(item)[2] - bounds(item)[0], default=None),
        max(bottom_edges, key=lambda item: bounds(item)[2] - bounds(item)[0], default=None),
        max(left_edges, key=lambda item: bounds(item)[3] - bounds(item)[1], default=None),
        max(right_edges, key=lambda item: bounds(item)[3] - bounds(item)[1], default=None),
    ]
    if any(edge is None for edge in edges) or len({id(edge) for edge in edges}) != 4:
        return
    group_id = "cad-sheet-frame-" + "-".join(str(edge.get("id")) for edge in edges)
    for edge in edges:
        edge["interactionGroupId"] = group_id
        edge["locked"] = False


def _infer_missing_report_markers(report_tables, texts):
    """Infer table markers only when the drawing explicitly describes their range."""
    numeric_points = _continuous_numeric_point_texts(texts)
    if not numeric_points:
        return

    drawing_text = " ".join(str(item.get("text") or "") for item in texts)
    compact = re.sub(r"\s+", "", drawing_text).upper()
    range_match = re.search(r"([A-Z]?)1(?:-|—|~|～|至)([A-Z]?)(\d+)", compact)

    preferred_kind = None
    prefix = ""
    declared_count = len(numeric_points)
    if range_match:
        prefix = range_match.group(1) or range_match.group(2) or ""
        declared_count = int(range_match.group(3))
        if "法兰" in drawing_text or prefix == "D":
            preferred_kind = "transition"

    candidates = []
    for kind, rows in report_tables.items():
        if not isinstance(rows, list) or not rows:
            continue
        missing = [row for row in rows if isinstance(row, dict) and not str(row.get("marker") or "").strip()]
        if not missing:
            continue
        score = abs(len(rows) - declared_count)
        if preferred_kind == kind:
            score -= 1000
        candidates.append((score, kind, rows))
    if not candidates:
        return

    _, _, rows = min(candidates, key=lambda item: item[0])
    for index, row in enumerate(rows, start=1):
        if index > declared_count or not isinstance(row, dict) or str(row.get("marker") or "").strip():
            continue
        row["marker"] = f"{prefix}{index}" if prefix else str(index)


def _continuous_numeric_point_texts(texts):
    """识别图纸中从 1 开始连续编号的检测点，排除图号和设备编号。"""
    candidates = {}
    for text in texts:
        label = str(text.get("text") or "").strip()
        if not label.isdigit():
            continue
        number = int(label)
        if 1 <= number <= 999:
            candidates.setdefault(number, []).append(text)

    last_number = 0
    while last_number + 1 in candidates:
        last_number += 1
    if last_number < 3:
        return []
    return [candidates[number][0] for number in range(1, last_number + 1)]


def _handle_targets(parsed):
    targets = {}
    for kind in ("texts", "blocks", "paths"):
        for item in parsed[kind]:
            target = _item_target(item, kind[:-1] if kind.endswith("s") else kind)
            for handle in item.get("importedSourceHandles") or []:
                targets[str(handle).upper()] = target
    return targets


def _target_from_existing(existing, handle_targets):
    if not isinstance(existing, dict):
        return None
    for handle in existing.get("sourceHandles") or existing.get("importedSourceHandles") or []:
        target = handle_targets.get(str(handle).upper())
        if target:
            return target
    return None


def _match_marker_text(marker, row, texts, paths, report_type=None):
    # In many legacy drawings the visible callout is an equipment/location
    # number (``005`` for ``编号DL-005``), not the ordinal used by the Word
    # measurement table (for example test point ``1``).  Resolve that stable
    # physical identifier first.  Falling back to the report ordinal is still
    # needed for rows such as the photovoltaic and static-discharge points,
    # whose CAD labels are the test-point number itself.
    raw_location_markers, location_markers = (
        _grounding_location_marker_candidates(row)
        if report_type == "grounding"
        else (set(), set())
    )
    # Preserve the zero padding from ``DL-034`` for the first pass.  The same
    # sheet can contain both ``034`` (a location code) and ``34`` (a report
    # point number); canonical numeric comparison would otherwise make them
    # indistinguishable and attach the location row to the wrong arrow.
    location_candidates = [
        text for text in texts
        if _compact_marker_text(text.get("text")) in raw_location_markers
    ]
    if not location_candidates:
        location_candidates = [
            text for text in texts
            if _normalize_marker(text.get("text")) in location_markers
        ]
    if location_candidates:
        return _select_marker_target(location_candidates, row, paths, texts)
    if raw_location_markers:
        # An explicit physical code is authoritative.  If that code is absent
        # from the drawing, binding the row to its report ordinal would attach
        # it to an unrelated CAD point (for example report row 33 / 编号026
        # incorrectly landing on CAD 033).  Leave it unmatched/report-only so
        # the missing drawing point is visible to the user instead.
        return None

    raw_marker = _compact_marker_text(marker)
    normalized = _normalize_marker(marker)
    candidates = [text for text in texts if _compact_marker_text(text.get("text")) == raw_marker]
    if not candidates:
        candidates = [text for text in texts if _normalize_marker(text.get("text")) == normalized]
    if not candidates:
        candidates = [
            text
            for text in texts
            if _marker_is_in_text_range(normalized, _normalize_marker(text.get("text")))
        ]
    if not candidates:
        return None
    return _select_marker_target(candidates, row, paths, texts)


def _grounding_location_marker_candidates(row):
    """Return explicit physical codes encoded in a grounding report location.

    Reports use both ``编号DL-005`` and ``编号005`` while the drawing commonly
    renders only ``005``.  All three formats identify the same physical point.
    A number is accepted only when it immediately follows the explicit
    ``编号`` prefix (with an optional ``DL`` segment), so equipment identifiers
    such as BV102, PIT101, SDV101 and CT-101 remain descriptive names rather
    than drawing-point identifiers.
    """
    raw_candidates = set()
    candidates = set()
    if not isinstance(row, dict):
        return raw_candidates, candidates
    for key in ("workLocation", "installLocation", "referencePoint"):
        value = re.sub(r"\s+", "", str(row.get(key) or "")).upper()
        for match in re.finditer(r"编号(?:DL[-_－—]?)?(\d{1,4})(?!\d)", value):
            physical_marker = match.group(1)
            raw_candidates.add(physical_marker)
            candidates.add(_normalize_marker(physical_marker))
    return raw_candidates, candidates


def _compact_marker_text(value):
    """Remove display-only line breaks while retaining numeric padding."""
    return re.sub(r"\s+", "", str(value or "")).upper()


def _report_marker_for_display_label(display_marker, current_marker, report_type, report_tables):
    """Bind a zero-padded CAD duplicate to its unpadded Word row when safe.

    A sheet may contain both ``34`` and ``034`` as separate physical symbols.
    When the Word row 34 has no physical location code, both symbols are
    intentionally alternate CAD representations of that same report point.
    Do not apply this to rows with a location code (for example ``006``),
    because it identifies a different physical asset.
    """
    display = _compact_marker_text(display_marker)
    compact_number = _normalize_marker(display)
    if not re.fullmatch(r"0+\d+", display) or compact_number == _normalize_marker(current_marker):
        return current_marker, None
    if report_type == "grounding":
        current_row = next(
            (
                row
                for row in report_tables.get(report_type) or []
                if isinstance(row, dict)
                and _normalize_marker(row.get("marker")) == _normalize_marker(current_marker)
            ),
            None,
        )
        current_location_markers, _ = _grounding_location_marker_candidates(current_row)
        if display in current_location_markers:
            # ``034`` is the physical DL-034 code of the current row.  Do not
            # reinterpret it as the ordinal report row 34.
            return current_marker, None
    for row in report_tables.get(report_type) or []:
        if not isinstance(row, dict) or _normalize_marker(row.get("marker")) != compact_number:
            continue
        raw_locations, _ = (
            _grounding_location_marker_candidates(row)
            if report_type == "grounding"
            else (set(), set())
        )
        if not raw_locations:
            return str(row.get("marker") or compact_number), row
    return current_marker, None


def _select_marker_target(candidates, row, paths, texts):
    """Choose an unambiguous CAD label, using nearby report prose as a tie-breaker."""
    if len(candidates) == 1:
        return _marker_target_from_text(candidates[0], paths)

    names = [
        str(row.get(key) or "").strip()
        for key in ("workLocation", "equipmentName", "installLocation", "referencePoint")
        if str(row.get(key) or "").strip()
    ]
    name_texts = [
        text for text in texts
        if any(name in str(text.get("text") or "") or str(text.get("text") or "") in name for name in names)
    ]
    if not name_texts:
        return _marker_target_from_text(candidates[0], paths)
    candidate = min(
        candidates,
        key=lambda item: min(_distance(item, name_text) for name_text in name_texts),
    )
    return _marker_target_from_text(candidate, paths)


def _marker_is_in_text_range(marker, text_value):
    """Match one report marker against a CAD range label such as D55~D56."""
    marker_match = re.fullmatch(r"([A-Z]?)(\d+)", marker)
    # DXF text is often split onto separate lines.  Once whitespace is
    # removed, a visible range such as ``D1\n-D\n12`` becomes ``D1-D12``.
    # Treat its hyphen as the range separator as well as the CAD tilde forms.
    range_match = re.fullmatch(r"([A-Z]?)(\d+)(?:~|～|-)([A-Z]?)(\d+)", text_value)
    if not marker_match or not range_match:
        return False
    marker_prefix, marker_number = marker_match.group(1), int(marker_match.group(2))
    start_prefix, start_number = range_match.group(1), int(range_match.group(2))
    end_prefix = range_match.group(3) or start_prefix
    end_number = int(range_match.group(4))
    return (
        marker_prefix == start_prefix == end_prefix
        and min(start_number, end_number) <= marker_number <= max(start_number, end_number)
    )


def _triangle_geometry_from_path(path):
    """Return tip, side and vertices for a small filled triangular CAD marker.

    Text is normally placed beside a test-point arrow, but that relationship
    is only a fallback: legends can deliberately place the label on either
    side of the arrow.  For an actual solid triangle the geometry gives us an
    unambiguous direction, including the left-facing sample in the legend.
    The tip (not the bounding-box centre) is the stable anchor used by the
    frontend when redrawing the arrow and its number.
    """
    raw_points = path.get("points") or []
    if len(raw_points) < 3:
        return None
    xs = [float(point.get("x") or 0) for point in raw_points]
    ys = [float(point.get("y") or 0) for point in raw_points]
    extent = max(max(xs) - min(xs), max(ys) - min(ys), 1.0)
    merge_distance = extent * 0.03
    vertices = []
    for point in raw_points:
        vertex = (float(point.get("x") or 0), float(point.get("y") or 0))
        if not vertices or math.hypot(vertex[0] - vertices[-1][0], vertex[1] - vertices[-1][1]) > merge_distance:
            vertices.append(vertex)
    if len(vertices) > 1 and math.hypot(vertices[0][0] - vertices[-1][0], vertices[0][1] - vertices[-1][1]) <= merge_distance:
        vertices.pop()
    if len(vertices) != 3:
        return None

    # A CAD arrowhead is commonly much taller than it is wide.  Selecting the
    # longest median in that case wrongly treats a rear corner as the tip.
    # First use the defining triangle silhouette: one vertex on one horizontal
    # extreme and two vertices on the opposite extreme means left/right.
    vertex_xs = [vertex[0] for vertex in vertices]
    vertex_ys = [vertex[1] for vertex in vertices]
    extreme_tolerance = extent * 0.05
    left_count = sum(abs(value - min(vertex_xs)) <= extreme_tolerance for value in vertex_xs)
    right_count = sum(abs(value - max(vertex_xs)) <= extreme_tolerance for value in vertex_xs)
    tip_index = None
    side = None
    if left_count == 1 and right_count >= 2:
        side = "left"
        tip_index = min(range(3), key=lambda index: vertices[index][0])
    elif right_count == 1 and left_count >= 2:
        side = "right"
        tip_index = max(range(3), key=lambda index: vertices[index][0])
    else:
        top_count = sum(abs(value - min(vertex_ys)) <= extreme_tolerance for value in vertex_ys)
        bottom_count = sum(abs(value - max(vertex_ys)) <= extreme_tolerance for value in vertex_ys)
        if top_count == 1 and bottom_count >= 2:
            side = "top"
            tip_index = min(range(3), key=lambda index: vertices[index][1])
        elif bottom_count == 1 and top_count >= 2:
            side = "bottom"
            tip_index = max(range(3), key=lambda index: vertices[index][1])

    if tip_index is None:
        tip_index = max(
            range(3),
            key=lambda index: math.hypot(
                vertices[index][0] - (vertices[(index + 1) % 3][0] + vertices[(index + 2) % 3][0]) / 2,
                vertices[index][1] - (vertices[(index + 1) % 3][1] + vertices[(index + 2) % 3][1]) / 2,
            ),
        )
        tip_x, tip_y = vertices[tip_index]
        base = [vertices[index] for index in range(3) if index != tip_index]
        base_x = (base[0][0] + base[1][0]) / 2
        base_y = (base[0][1] + base[1][1]) / 2
        delta_x = tip_x - base_x
        delta_y = tip_y - base_y
        if abs(delta_x) >= abs(delta_y):
            side = "right" if delta_x > 0 else "left"
        else:
            side = "bottom" if delta_y > 0 else "top"
    tip_x, tip_y = vertices[tip_index]
    return {
        "points": [{"x": vertex[0], "y": vertex[1]} for vertex in vertices],
        "side": side,
        "x": tip_x,
        "y": tip_y,
    }


def _triangle_side_from_path(path):
    geometry = _triangle_geometry_from_path(path)
    return None if geometry is None else geometry["side"]


def _triangle_hull_points(parts):
    points = [point for part in parts for point in part.get("points") or []]
    if len(points) < 3:
        return None
    xs = [float(point.get("x") or 0) for point in points]
    ys = [float(point.get("y") or 0) for point in points]
    tolerance = max(max(max(xs) - min(xs), max(ys) - min(ys)) * 0.03, 1e-6)
    merged = []
    for point in points:
        x = float(point.get("x") or 0)
        y = float(point.get("y") or 0)
        match = next(
            (
                existing
                for existing in merged
                if math.hypot(x - existing["x"], y - existing["y"]) <= tolerance
            ),
            None,
        )
        if match is None:
            merged.append({"x": x, "y": y, "count": 1})
        else:
            count = int(match["count"])
            match["x"] = (match["x"] * count + x) / (count + 1)
            match["y"] = (match["y"] * count + y) / (count + 1)
            match["count"] = count + 1
    hull = _convex_hull(merged)
    if len(hull) == 3:
        return hull

    # ODA often splits one filled arrow into upper/lower halves whose tip
    # coordinates differ by a hair.  The convex hull then has a tiny fourth
    # corner.  Rebuild a clean left/right silhouette from the combined bounds
    # so the tip sits on the shared mid-height of the full arrow.
    sides = {
        side
        for part in parts
        if (side := _triangle_side_from_path(part)) in {"left", "right"}
    }
    if len(sides) != 1 or len(parts) < 2:
        return None
    left_x, right_x = min(xs), max(xs)
    top_y, bottom_y = min(ys), max(ys)
    middle_y = (top_y + bottom_y) / 2
    side = next(iter(sides))
    if side == "left":
        return [
            {"x": right_x, "y": top_y},
            {"x": left_x, "y": middle_y},
            {"x": right_x, "y": bottom_y},
        ]
    return [
        {"x": left_x, "y": top_y},
        {"x": right_x, "y": middle_y},
        {"x": left_x, "y": bottom_y},
    ]


def _triangle_geometry_from_parts(parts):
    """Resolve tip/side after joining ODA-exported hatch halves of one arrow."""
    hull = _triangle_hull_points(parts)
    if hull is None:
        return None
    return _triangle_geometry_from_path({"points": hull})


def _triangle_side_from_parts(parts):
    """Resolve a CAD arrow direction after joining its exported hatch halves.

    ODA commonly expands one filled triangle into two adjacent HATCH paths.
    Each half is itself a small left/right triangle, so inspecting the first
    half reports the wrong direction for vertical arrows.  The convex hull of
    all touching halves restores the original three-point silhouette.
    """
    geometry = _triangle_geometry_from_parts(parts)
    return None if geometry is None else geometry["side"]


def _is_connector_identifier_label(value):
    """SC1/SC2-style names label a connector box; they are not tip callouts."""
    compact = re.sub(r"\s+", "", str(value or "")).upper()
    return bool(re.fullmatch(r"SC\d{1,3}", compact))


def _marker_target_from_text(text, paths):
    """将编号文字吸附到附近的小型填充标记；找不到时保留文字坐标。"""
    text_x = float(text.get("x") or 0)
    text_y = float(text.get("y") or 0)
    # Connector identifiers (SC1/SC2) sit above their equipment box.  Snapping
    # them onto a nearby filled triangle invents a free arrow that the DWG
    # never drew beside the label.
    if _is_connector_identifier_label(text.get("text") or text.get("name")):
        target = _item_target(text, "text")
        target["markerLabel"] = _compact_marker_text(text.get("text"))
        return target
    # Range labels (D100-D101) are often printed to the left of a vertical
    # equipment bank, so their original CAD arrow is farther away than a
    # regular single-point label.  A single label can also sit just outside a
    # 30-unit radius when two vertically stacked arrow HATCH parts are used.
    # Keep the broad range allowance, while giving ordinary labels enough
    # reach to claim their own adjacent marker.
    marker_distance_limit = 72 if _parse_marker_range(text.get("text")) else 44
    candidates = []
    for path in paths:
        marker_name = str(path.get("name") or "").upper()
        if marker_name not in {"HATCH", "POINT", "SOLID", "TRACE"} or not path.get("closed"):
            continue
        points = path.get("points") or []
        if len(points) < 3:
            continue
        xs = [float(point.get("x") or 0) for point in points]
        ys = [float(point.get("y") or 0) for point in points]
        width = max(xs) - min(xs)
        height = max(ys) - min(ys)
        if not (1 <= width <= 20 and 1 <= height <= 20):
            continue
        # A test-point marker is the arrow triangle itself.  Equipment such as
        # black control-box squares can sit directly above that triangle but
        # must never become part of the test-point interaction group.
        if _triangle_side_from_path(path) is None:
            continue
        center_x = (min(xs) + max(xs)) / 2
        center_y = (min(ys) + max(ys)) / 2
        distance = math.hypot(center_x - text_x, center_y - text_y)
        if distance <= marker_distance_limit:
            candidates.append((distance, center_x, center_y, path, {
                "x": min(xs),
                "y": min(ys),
                "width": width,
                "height": height,
            }))

    if not candidates:
        target = _item_target(text, "text")
        target["markerLabel"] = _compact_marker_text(text.get("text"))
        return target

    # Distance alone can choose a marker from the next row when stacked call
    # outs are very close.  Prefer the candidate aligned with the label on
    # either axis (the usual marker/label relationship) before using distance
    # as the final tie-breaker.
    _, _, _, marker, marker_box = min(
        candidates,
        key=lambda item: (
            item[0] + min(abs(item[1] - text_x), abs(item[2] - text_y)) * 0.75,
            item[0],
        ),
    )
    marker_parts = [marker]
    combined_box = dict(marker_box)
    for _, _, _, candidate, candidate_box in candidates:
        if candidate is marker or _triangle_side_from_path(candidate) is None or not _boxes_touch(combined_box, candidate_box, 0.75):
            continue
        marker_parts.append(candidate)
        combined_box = _union_boxes(combined_box, candidate_box)

    # Keep adjacent filled triangles together, but never absorb ordinary
    # equipment geometry.  A valve is commonly two black triangles plus a
    # circle: only the triangles are test-point arrows; the circle and its
    # surrounding frame must remain independent editable paths.
    marker_ids = {int(part.get("id") or 0) for part in marker_parts}
    max_part_width = max(combined_box["width"] * 2.5, 24)
    max_part_height = max(combined_box["height"] * 2.5, 24)
    tolerance = max(min(combined_box["width"], combined_box["height"]) * 0.2, 1)
    for path in paths:
        path_id = int(path.get("id") or 0)
        marker_name = str(path.get("name") or "").upper()
        path_points = path.get("points") or []
        if (
            path_id in marker_ids
            or marker_name not in {"HATCH", "POINT", "SOLID", "TRACE"}
            or not path.get("closed")
            or len(path_points) < 3
            or len(path_points) > 5
            or _triangle_side_from_path(path) is None
        ):
            continue
        path_box = _path_box(path)
        if (
            path_box["width"] <= max_part_width
            and path_box["height"] <= max_part_height
            and _boxes_touch(combined_box, path_box, tolerance)
        ):
            marker_parts.append(path)
            marker_ids.add(path_id)
            combined_box = _union_boxes(combined_box, path_box)

    center_x = combined_box["x"] + combined_box["width"] / 2
    center_y = combined_box["y"] + combined_box["height"] / 2
    delta_x = text_x - center_x
    delta_y = text_y - center_y
    if abs(delta_x) > abs(delta_y):
        side = "left" if delta_x > 0 else "right"
    else:
        side = "bottom" if delta_y < 0 else "top"
    # Prefer the triangle's physical tip.  The relative label position is
    # retained as a fallback for non-triangular markers and composite symbols.
    geometry = _triangle_geometry_from_parts(marker_parts) or next(
        (
            resolved
            for part in marker_parts
            if (resolved := _triangle_geometry_from_path(part))
        ),
        None,
    )
    if geometry:
        side = geometry["side"]
        anchor_x = float(geometry["x"])
        anchor_y = float(geometry["y"])
        source_triangle_points = list(geometry["points"])
    else:
        anchor_x = center_x
        anchor_y = center_y
        source_triangle_points = None
    # Do not push the CAD number away from its arrow.  Existing-report import
    # hides that number and redraws it from the tip/side, so mutating text.x/y
    # only makes the overlay label drift (especially for vertical stacks like
    # 41/42).
    if side in {"top", "bottom"}:
        size = (combined_box["width"] / 18 + combined_box["height"] / 14) / 2
    else:
        size = (combined_box["width"] / 14 + combined_box["height"] / 18) / 2
    size = max(0.2, min(size, 4))
    handles = list(dict.fromkeys(
        [
            handle
            for part in marker_parts
            for handle in part.get("importedSourceHandles") or []
        ]
        + list(text.get("importedSourceHandles") or [])
    ))
    source_element_ids = list(dict.fromkeys(
        [int(part.get("id")) for part in marker_parts if part.get("id") is not None]
        + ([int(text.get("id"))] if text.get("id") is not None else [])
    ))
    target = {
        "markerLabel": _compact_marker_text(text.get("text")),
        "x": anchor_x,
        "y": anchor_y,
        "sourceElementIds": source_element_ids,
        "sourceHandles": handles,
        "id": text.get("id"),
        "kind": "text",
        "side": side,
        "size": size,
    }
    if source_triangle_points:
        target["sourceTrianglePoints"] = source_triangle_points
    return target


def _marker_text_visual_box(text):
    glyph_bounds = text.get("glyphBounds")
    if isinstance(glyph_bounds, dict):
        return {
            "x": float(text.get("x") or 0) + float(glyph_bounds.get("x") or 0),
            "y": float(text.get("y") or 0) + float(glyph_bounds.get("y") or 0),
            "width": max(float(glyph_bounds.get("width") or 0), 1),
            "height": max(float(glyph_bounds.get("height") or 0), 1),
        }
    return _item_box(text)


def _separate_overlapping_marker_label(text, marker_box, delta_x, delta_y):
    """Keep API coordinates unless the visible label collides with its marker."""
    text_box = _marker_text_visual_box(text)
    gap = max(1.5, min(marker_box["width"], marker_box["height"]) * 0.12)
    expanded_marker = {
        "x": marker_box["x"] - gap,
        "y": marker_box["y"] - gap,
        "width": marker_box["width"] + gap * 2,
        "height": marker_box["height"] + gap * 2,
    }
    if not _boxes_touch(text_box, expanded_marker):
        return
    shift_x = 0
    shift_y = 0
    if abs(delta_x) > abs(delta_y):
        if delta_x >= 0:
            shift_x = expanded_marker["x"] + expanded_marker["width"] - text_box["x"]
        else:
            shift_x = expanded_marker["x"] - (text_box["x"] + text_box["width"])
    elif delta_y >= 0:
        shift_y = expanded_marker["y"] + expanded_marker["height"] - text_box["y"]
    else:
        shift_y = expanded_marker["y"] - (text_box["y"] + text_box["height"])
    text["x"] = float(text.get("x") or 0) + shift_x
    text["y"] = float(text.get("y") or 0) + shift_y


def _boxes_touch(left, right, tolerance=0):
    return not (
        left["x"] + left["width"] < right["x"] - tolerance
        or right["x"] + right["width"] < left["x"] - tolerance
        or left["y"] + left["height"] < right["y"] - tolerance
        or right["y"] + right["height"] < left["y"] - tolerance
    )


def _union_boxes(left, right):
    x = min(left["x"], right["x"])
    y = min(left["y"], right["y"])
    right_edge = max(left["x"] + left["width"], right["x"] + right["width"])
    bottom_edge = max(left["y"] + left["height"], right["y"] + right["height"])
    return {"x": x, "y": y, "width": right_edge - x, "height": bottom_edge - y}


def _scaled_existing_point(existing, old_width, old_height, new_width, new_height):
    if not isinstance(existing, dict):
        return None
    if existing.get("x") is None or existing.get("y") is None:
        return None
    scale_x = new_width / float(old_width) if old_width else 1
    scale_y = new_height / float(old_height) if old_height else 1
    return {
        "x": float(existing["x"]) * scale_x,
        "y": float(existing["y"]) * scale_y,
        "sourceElementIds": existing.get("sourceElementIds") or [],
        "sourceHandles": existing.get("sourceHandles") or [],
        "id": None,
        "kind": "point",
    }


def _item_target(item, kind):
    if kind == "path":
        points = item.get("points") or []
        x = sum(point["x"] for point in points) / len(points) if points else 0
        y = sum(point["y"] for point in points) / len(points) if points else 0
    else:
        x = item.get("x", 0)
        y = item.get("y", 0)
    return {
        "x": float(x),
        "y": float(y),
        "sourceElementIds": [item.get("id")] if item.get("id") is not None else [],
        "sourceHandles": item.get("importedSourceHandles") or [],
        "id": item.get("id"),
        "kind": kind,
    }


_REPORT_FIELD_LABELS = {
    "equipmentName": "设备名称",
    "workLocation": "—",
    "installLocation": "安装位置",
    "referencePoint": "基准点",
    "conductorSpec": "连接导体材质规格",
    "protectionZone": "防雷分区",
    "standardValue": "标准值",
    "measuredValue": "测试值",
    "result": "结论",
    "spdModel": "SPD型号",
    "wireLength": "接线长度",
    "spdLevel": "SPD级别",
    "installQuantity": "安装数量",
}


def _report_fields(row, report_type=""):
    excluded = {"id", "marker", "label", "bindingName", "blockName", "placeName"}
    fields = {key: copy.deepcopy(value) for key, value in row.items() if key not in excluded}
    if row.get("placeName") and not fields.get("installLocation"):
        fields["installLocation"] = row["placeName"]

    common_keys = ("equipmentName", "result")
    if report_type == "spd":
        required_keys = common_keys + (
            "spdModel", "installLocation", "wireLength", "spdLevel",
            "installQuantity", "measuredValue",
        )
    elif report_type == "transition":
        required_keys = common_keys + (
            "workLocation", "referencePoint", "conductorSpec", "protectionZone",
            "standardValue", "measuredValue",
        )
    else:
        required_keys = common_keys + (
            "workLocation", "conductorSpec", "protectionZone",
            "standardValue", "measuredValue",
        )

    for key in required_keys:
        value = fields.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            fields[key] = _REPORT_FIELD_LABELS[key]
    return fields


def _build_report_payload(binding_data, workspace, report_tables):
    if not isinstance(binding_data, dict):
        return None
    if binding_data.get("schema") == "dwg-legend-designer-workspace":
        return None
    report = copy.deepcopy(binding_data)
    report["reportTables"] = copy.deepcopy(report_tables)
    report["legend"] = {"workspace": workspace}
    return report


def _unique_row_id(value, used_ids, next_id):
    try:
        candidate = int(value)
    except (TypeError, ValueError):
        candidate = next_id
    while candidate in used_ids:
        candidate += 1
    return candidate


def _normalize_marker(value):
    """Normalize formatting without discarding a semantic marker prefix.

    ``1`` is a grounding-resistance point while ``D1`` is a transition-
    resistance point.  Treating both as ``1`` lets report-only transition rows
    overwrite visible grounding points from the CAD drawing.

    CAD drawings commonly pad numeric callouts to a fixed width (``001``),
    whereas the Word measurement table uses the same marker as ``1``.  The
    padding is presentational rather than semantic, so remove it only from a
    complete marker and retain its optional prefix (``D001`` remains ``D1``).
    Do not touch compound labels such as ranges here: those are handled by
    ``_marker_is_in_text_range``.
    """
    compact = _compact_marker_text(value)
    match = re.fullmatch(r"([A-Z]?)(\d+)", compact)
    if not match:
        return compact
    prefix, number = match.groups()
    return f"{prefix}{int(number)}"


def finalize_existing_report_workspace(workspace, image_data_url=None, image_filename="校对图片"):
    """将通用解析结果整理成前端“导入已有报告”使用的工作区结构。"""
    canvas = _active_canvas(workspace)
    if not canvas:
        return workspace

    original_texts = list(canvas.get("texts") or [])
    test_points = list(canvas.get("testPoints") or [])
    paths = list(canvas.get("paths") or [])
    texts = list(original_texts)

    # Preserve the exact CAD marker geometry. Some drawings use squares or
    # compound symbols instead of the frontend's canonical triangle. Keep the
    # source members and the semantic test point in one editable interaction
    # group so selecting, moving or resizing the marker transforms everything
    # together.
    marker_group_ids = {
        str(point.get("interactionGroupId"))
        for point in test_points
        if point.get("interactionGroupId")
    }
    if marker_group_ids:
        for item in [*paths, *texts]:
            if str(item.get("interactionGroupId") or "") in marker_group_ids:
                item["testPointSource"] = True
    for point in test_points:
        point["cadSourceVisible"] = bool(point.get("interactionGroupId"))
        source_marker = _compact_marker_text(point.get("sourceMarker"))
        display_marker = _compact_marker_text(point.get("label"))
        if not source_marker or source_marker == display_marker:
            continue
        source_ids = {
            int(item_id)
            for item_id in point.get("sourceElementIds") or []
            if str(item_id).strip().isdigit()
        }
        # Retain the original CAD arrow/geometry but hide only its old number.
        # The semantic test-point overlay now renders the Word table marker.
        for text in texts:
            if int(text.get("id") or 0) not in source_ids:
                continue
            if _compact_marker_text(text.get("text")) == source_marker:
                text["hidden"] = True
    width = float(canvas.get("boardWidth") or 1600)
    height = float(canvas.get("boardHeight") or 1280)
    paths = [path for path in paths if not _is_orphan_legend_swatch(path, width, height)]
    paths = _remove_redundant_large_hatch_outlines(paths, width, height)

    chrome_regions = _report_image_chrome_regions(width, height)
    chrome_regions["legend"] = _detect_legend_region(paths, texts, width, height)
    paths = _remove_metal_roof_legend_empty_frame(paths, texts, chrome_regions["legend"])
    # Join compound filled-arrow halves before looking for duplicate legend
    # frames.  Otherwise the lower half of a separately-handled test-point
    # triangle is mistakenly discarded as a duplicate of the upper half.
    paths = _merge_legend_hatch_parts(paths, chrome_regions["legend"])
    paths = _remove_duplicate_legend_hatch_frames(paths, texts, chrome_regions["legend"])
    paths = _deduplicate_region_paths(paths, chrome_regions["legend"])
    texts = _deduplicate_region_texts(texts, chrome_regions["legend"])
    paths = _remove_title_block_fill_artifacts(paths, chrome_regions["titleBlock"])
    _normalize_north_labels(texts, paths, width, height)
    _fit_cad_text_to_enclosing_boxes(texts, paths)
    _fit_compact_identifier_texts_to_enclosing_boxes(texts, paths)
    _separate_connector_identifier_labels(texts, paths)
    _anchor_room_labels_to_enclosing_boxes(texts, paths)
    readonly_regions = {
        "legend": chrome_regions["legend"],
        "titleBlock": chrome_regions["titleBlock"],
    }
    for path in paths:
        readonly_group = next(
            (name for name, region in readonly_regions.items() if _box_center_in_region(_path_box(path), region)),
            None,
        )
        if readonly_group:
            path["locked"] = True
            path["readonlyGroup"] = readonly_group
            if str(path.get("name") or "").upper() in {"SOLID", "TRACE"}:
                path["fillStyle"] = "solid"
                path["fillColor"] = "#111111"
    for text in texts:
        readonly_group = next(
            (name for name, region in readonly_regions.items() if _box_center_in_region(_item_box(text), region)),
            None,
        )
        if readonly_group:
            text["fontFamily"] = text.get("fontFamily") or "SimSun"
            text["locked"] = True
            text["readonlyGroup"] = readonly_group
    next_id = max(
        [int(canvas.get("nextId") or 1)]
        + [int(item.get("id") or 0) + 1 for item in paths + texts + test_points],
    )
    blocks = []

    report_tables = canvas.get("reportTables") or {}
    rows_by_id = {
        str(row.get("id")): row
        for rows in report_tables.values()
        if isinstance(rows, list)
        for row in rows
        if isinstance(row, dict) and row.get("id") is not None
    }
    place_names = []
    for row in rows_by_id.values():
        place_name = str(row.get("placeName") or "").strip()
        if place_name:
            place_names.append(place_name)
    place_names.extend(
        str(text.get("text") or text.get("name") or "").strip()
        for text in original_texts
        if _is_bindable_place_label(text.get("text") or text.get("name"))
    )

    parent_by_name = {}
    for place_name in place_names:
        normalized = _normalize_place_name(place_name)
        if not normalized or normalized in parent_by_name:
            continue
        source = next(
            (
                text for text in original_texts
                if normalized in _normalize_place_name(text.get("text") or text.get("name"))
                or _normalize_place_name(text.get("text") or text.get("name")) in normalized
            ),
            None,
        )
        parent_width = max(90, len(place_name) * 19)
        parent = {
            "fontSize": 18,
            "height": 34,
            "hidden": True,
            "id": next_id,
            "importedParent": True,
            "name": place_name,
            "orientation": "horizontal",
            "text": place_name,
            "width": parent_width,
            "x": float((source or {}).get("x") or width / 2) - parent_width / 2,
            "y": float((source or {}).get("y") or height / 2) - 18,
        }
        next_id += 1
        texts.append(parent)
        parent_by_name[normalized] = parent

    for point in test_points:
        row = rows_by_id.get(str(point.get("id")))
        if not row:
            continue
        place_name = str(row.get("placeName") or "").strip()
        parent = parent_by_name.get(_normalize_place_name(place_name))
        if parent:
            # The source handles retain the exact CAD marker geometry. The public
            # binding represents the semantic report object selected in the UI.
            place_binding = {"id": parent["id"], "kind": "text"}
            point["binding"] = place_binding
            point["placeBinding"] = place_binding
        row["bindingName"] = place_name
        row["blockName"] = "无图块"

    canvas["paths"] = paths
    canvas["texts"] = texts
    canvas["blocks"] = blocks
    canvas["testPoints"] = test_points
    canvas["nextId"] = next_id
    if image_data_url:
        canvas["nativePreviewChrome"] = {
            "fromImportedDwg": True,
            "hasLegend": True,
            "hasTitleBlock": True,
        }
        canvas["previewImageBoard"] = {
            "fileName": image_filename,
            "hasNativeLegend": True,
            "hasNativeTitleBlock": True,
            "height": height,
            "svg": _report_image_full_svg(image_data_url, width, height),
            "width": width,
        }
    return workspace


def _normalize_place_name(value):
    return re.sub(r"[\s()（）—\-/]+", "", str(value or "")).lower()


def _is_bindable_place_label(value):
    text = re.sub(r"\s+", "", str(value or ""))
    if not re.search(r"[\u3400-\u9fff]", text) or not 2 <= len(text) <= 30:
        return False
    excluded = (
        "图例", "检测点", "测试点", "检测人员", "委托单位", "委托项目",
        "受检项目", "内容", "图别", "日期", "图号", "定期检测", "示意图",
        "绘制", "公司", "探测器", "变送器", "配电箱", "控制柜", "机柜",
        "接地干线", "接地扁钢", "钢扶梯", "防静电", "等电位", "接线箱",
        "汇流排", "静电泄放", "摄像头", "报警器", "电池柜", "光伏板", "立管",
    )
    if any(keyword in text for keyword in excluded):
        return False
    return any(keyword in text for keyword in ("区", "库", "室", "车间", "场", "站", "厂", "房", "道", "中心", "罐区", "装置区")) or "#" in text


def _item_box(item):
    x = float(item.get("x") or 0)
    y = float(item.get("y") or 0)
    width = max(float(item.get("cadBoxWidth") or item.get("width") or 0), 1)
    if item.get("cadRender"):
        line_count = max(len(str(item.get("text") or item.get("name") or "").split("\n")), 1)
        height = max(float(item.get("fontSize") or 0) * 1.16 * line_count, 1)
        anchor = item.get("textAnchor") or "start"
        if anchor == "middle":
            x -= width / 2
        elif anchor == "end":
            x -= width
        baseline = item.get("dominantBaseline") or "alphabetic"
        if baseline == "central":
            y -= height / 2
        elif baseline in {"text-after-edge", "alphabetic"}:
            y -= height
        return {"x": x, "y": y, "width": width, "height": height}
    return {
        "x": x,
        "y": y,
        "width": width,
        "height": max(float(item.get("height") or 0), 1),
    }


def _is_orphan_legend_swatch(path, width, height):
    """删除图例框下方由 HATCH 双边界产生的孤立细长色块。"""
    if str(path.get("name") or "").upper() != "HATCH":
        return False
    box = _path_box(path)
    center_x = box["x"] + box["width"] / 2
    center_y = box["y"] + box["height"] / 2
    pattern_name = str(path.get("patternName") or "").upper()
    return (
        pattern_name == "DASH"
        and width * 0.70 <= center_x <= width * 0.90
        and height * 0.68 <= center_y <= height * 0.86
        and width * 0.02 <= box["width"] <= width * 0.08
        and box["height"] <= height * 0.015
    )


def _path_box(path):
    points = path.get("points") or []
    if not points:
        return {"x": 0, "y": 0, "width": 1, "height": 1}
    xs = [float(point.get("x") or 0) for point in points]
    ys = [float(point.get("y") or 0) for point in points]
    return {"x": min(xs), "y": min(ys), "width": max(max(xs) - min(xs), 1), "height": max(max(ys) - min(ys), 1)}


def _box_center_in_region(box, region):
    center_x = box["x"] + box["width"] / 2
    center_y = box["y"] + box["height"] / 2
    return (
        region["x"] <= center_x <= region["x"] + region["width"]
        and region["y"] <= center_y <= region["y"] + region["height"]
    )


def _is_replaced_report_border_trace(path, board_width, board_height):
    """过滤已由校对图片边框图块替代的 DWG 宽线 TRACE。"""
    if str(path.get("name") or "").upper() != "TRACE":
        return False
    box = _path_box(path)
    is_long_horizontal = box["width"] >= board_width * 0.75 and box["height"] <= board_height * 0.012
    is_long_vertical = box["height"] >= board_height * 0.65 and box["width"] <= board_width * 0.012
    near_top = box["y"] <= board_height * 0.13
    near_left = box["x"] <= board_width * 0.06
    return (is_long_horizontal and near_top) or (is_long_vertical and near_left)


def _regions_overlap(left, right):
    return (
        left["x"] <= right["x"] + right["width"]
        and left["x"] + left["width"] >= right["x"]
        and left["y"] <= right["y"] + right["height"]
        and left["y"] + left["height"] >= right["y"]
    )


def _box_is_masked(box, regions):
    center_x = box["x"] + box["width"] / 2
    center_y = box["y"] + box["height"] / 2
    for region in regions:
        if not _regions_overlap(region, box):
            continue
        if region["x"] <= center_x <= region["x"] + region["width"] and region["y"] <= center_y <= region["y"] + region["height"]:
            return True
        overlap_x = min(region["x"] + region["width"], box["x"] + box["width"]) - max(region["x"], box["x"])
        overlap_y = min(region["y"] + region["height"], box["y"] + box["height"]) - max(region["y"], box["y"])
        if overlap_x > 0 and overlap_y > 0 and overlap_x * overlap_y / max(box["width"] * box["height"], 1) > 0.42:
            return True
    return False


def report_image_target_area(width, height):
    """与前端 LegendDesigner.reportImageTargetArea 保持一致。"""
    width = float(width)
    height = float(height)
    return {
        "x": width * 0.007,
        "y": height * 0.075,
        "width": width * 0.982,
        "height": height * 0.865,
    }


def _expand_region(region, padding, width, height):
    x = max(0, region["x"] - padding)
    y = max(0, region["y"] - padding)
    right = min(width, region["x"] + region["width"] + padding)
    bottom = min(height, region["y"] + region["height"] + padding)
    return {"x": x, "y": y, "width": max(right - x, 0), "height": max(bottom - y, 0)}


def _report_image_chrome_regions(width, height):
    return {
        "borderBottom": {"x": width * 0.007, "y": height * 0.913, "width": width * 0.982, "height": height * 0.01},
        "borderLeft": {"x": width * 0.005, "y": height * 0.074, "width": width * 0.01, "height": height * 0.84},
        "borderRight": {"x": width * 0.984, "y": height * 0.074, "width": width * 0.01, "height": height * 0.84},
        "borderTop": {"x": width * 0.007, "y": height * 0.073, "width": width * 0.982, "height": height * 0.01},
        "inspector": {"x": width * 0.05, "y": height * 0.918, "width": width * 0.18, "height": height * 0.035},
        "legend": {"x": width * 0.858, "y": height * 0.09, "width": width * 0.13, "height": height * 0.7},
        "titleBlock": {"x": width * 0.57, "y": height * 0.792, "width": width * 0.42, "height": height * 0.13},
    }


def _detect_legend_region(paths, texts, width, height):
    """Find the CAD legend frame so all of its primitives remain one read-only area."""
    fallback = _report_image_chrome_regions(width, height)["legend"]
    title = next(
        (
            item for item in texts
            if re.sub(r"\s+", "", str(item.get("text") or item.get("name") or "")) == "图例"
        ),
        None,
    )
    if not title:
        return fallback

    title_box = _item_box(title)
    title_x = title_box["x"] + title_box["width"] / 2
    title_y = title_box["y"] + title_box["height"] / 2
    candidates = []
    for path in paths:
        box = _path_box(path)
        if not (
            box["x"] <= title_x <= box["x"] + box["width"]
            and box["y"] <= title_y <= box["y"] + box["height"]
        ):
            continue
        if not (width * 0.05 <= box["width"] <= width * 0.28):
            continue
        if not (height * 0.2 <= box["height"] <= height * 0.85):
            continue
        candidates.append(box)
    if not candidates:
        return fallback
    return min(candidates, key=lambda box: box["width"] * box["height"])


def _convex_hull(points):
    unique = sorted({(float(point.get("x") or 0), float(point.get("y") or 0)) for point in points})
    if len(unique) <= 2:
        return [{"x": x, "y": y} for x, y in unique]

    def cross(origin, left, right):
        return (left[0] - origin[0]) * (right[1] - origin[1]) - (left[1] - origin[1]) * (right[0] - origin[0])

    lower = []
    for point in unique:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    upper = []
    for point in reversed(unique):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return [{"x": x, "y": y} for x, y in lower[:-1] + upper[:-1]]


def _remove_metal_roof_legend_empty_frame(paths, texts, region):
    """Remove the empty rectangle immediately left of the 金属屋面 legend label."""
    labels = [
        text
        for text in texts
        if "金属屋面" in re.sub(r"\s+", "", str(text.get("text") or text.get("name") or ""))
        and _box_center_in_region(_item_box(text), region)
    ]
    if not labels:
        return paths

    removed_ids = set()
    for label in labels:
        label_box = _item_box(label)
        label_center_y = label_box["y"] + label_box["height"] / 2
        candidates = []
        for path in paths:
            if not path.get("closed") or not _box_center_in_region(_path_box(path), region):
                continue
            # Keep the real filled symbol. Some CAD files add a coincident
            # unfilled outline around it; only that empty outline is redundant.
            if path.get("hatch") or path.get("fillStyle") in {"hatch", "solid"}:
                continue
            box = _path_box(path)
            gap = label_box["x"] - (box["x"] + box["width"])
            center_y_distance = abs(box["y"] + box["height"] / 2 - label_center_y)
            if (
                box["x"] + box["width"] / 2 < label_box["x"] + label_box["width"] / 2
                and -label_box["width"] * 0.15 <= gap <= label_box["width"]
                and center_y_distance <= max(box["height"], label_box["height"] * 1.5)
                and box["width"] <= label_box["width"] * 1.5
                and box["height"] <= label_box["height"] * 3
            ):
                candidates.append((center_y_distance + abs(gap) * 0.1, path))
        if candidates:
            _, empty_frame = min(candidates, key=lambda item: item[0])
            removed_ids.add(empty_frame.get("id"))
    return [path for path in paths if path.get("id") not in removed_ids]


def _remove_duplicate_legend_hatch_frames(paths, texts, region):
    """Keep the hatch icon aligned with a legend row and remove its displaced duplicate frame."""
    row_centers = [
        _item_box(text)["y"] + _item_box(text)["height"] / 2
        for text in texts
        if _box_center_in_region(_item_box(text), region)
        and re.sub(r"\s+", "", str(text.get("text") or text.get("name") or "")) != "图例"
    ]
    candidates = [
        (path, _path_box(path))
        for path in paths
        if str(path.get("name") or "").upper() == "HATCH"
        and path.get("closed")
        and _box_center_in_region(_path_box(path), region)
    ]
    if not row_centers or len(candidates) < 2:
        return paths

    removed_ids = set()
    for index, (left, left_box) in enumerate(candidates):
        if left.get("id") in removed_ids:
            continue
        for right, right_box in candidates[index + 1:]:
            if right.get("id") in removed_ids:
                continue
            width_ratio = left_box["width"] / max(right_box["width"], 1)
            height_ratio = left_box["height"] / max(right_box["height"], 1)
            center_x_offset = abs(
                (left_box["x"] + left_box["width"] / 2)
                - (right_box["x"] + right_box["width"] / 2)
            )
            center_y_offset = abs(
                (left_box["y"] + left_box["height"] / 2)
                - (right_box["y"] + right_box["height"] / 2)
            )
            if (
                not 0.8 <= width_ratio <= 1.2
                or not 0.8 <= height_ratio <= 1.2
                or center_x_offset > max(left_box["width"], right_box["width"]) * 0.15
                or center_y_offset > max(left_box["height"], right_box["height"]) * 3
            ):
                continue
            left_distance = min(
                abs(left_box["y"] + left_box["height"] / 2 - row_center)
                for row_center in row_centers
            )
            right_distance = min(
                abs(right_box["y"] + right_box["height"] / 2 - row_center)
                for row_center in row_centers
            )
            minimum_gap = max(left_box["height"], right_box["height"]) * 0.6
            if left_distance + minimum_gap < right_distance:
                removed_ids.add(right.get("id"))
            elif right_distance + minimum_gap < left_distance:
                removed_ids.add(left.get("id"))
                break
    return [path for path in paths if path.get("id") not in removed_ids]


def _merge_legend_hatch_parts(paths, region):
    """Merge HATCH fragments expanded from one CAD handle into one legend icon."""
    candidates = [
        path for path in paths
        if str(path.get("name") or "").upper() == "HATCH"
        and _box_center_in_region(_path_box(path), region)
    ]
    parents = {id(path): id(path) for path in candidates}

    def find(item_id):
        while parents[item_id] != item_id:
            parents[item_id] = parents[parents[item_id]]
            item_id = parents[item_id]
        return item_id

    def union(left, right):
        left_root = find(id(left))
        right_root = find(id(right))
        if left_root != right_root:
            parents[right_root] = left_root

    # Most HATCH boundary fragments retain a shared source handle.
    by_handle = {}
    for path in candidates:
        handles = tuple(sorted(str(value).upper() for value in path.get("importedSourceHandles") or []))
        if handles:
            by_handle.setdefault(handles, []).append(path)
    for members in by_handle.values():
        for member in members[1:]:
            union(members[0], member)

    # Some DWGs draw a filled arrow with two separately-handled triangles: an
    # upper and a lower half sharing the same left/right tip.  They must be
    # merged before the generic legend de-duplication, otherwise one half can
    # survive as the visibly skewed triangle seen in the detection-point key.
    for index, left in enumerate(candidates):
        if str(left.get("fillStyle") or "") != "solid":
            continue
        left_side = _triangle_side_from_path(left)
        if left_side not in {"left", "right"}:
            continue
        left_box = _path_box(left)
        for right in candidates[index + 1:]:
            if str(right.get("fillStyle") or "") != "solid" or _triangle_side_from_path(right) != left_side:
                continue
            right_box = _path_box(right)
            overlap = min(left_box["x"] + left_box["width"], right_box["x"] + right_box["width"]) - max(left_box["x"], right_box["x"])
            shared_width = min(left_box["width"], right_box["width"])
            vertical_gap = max(left_box["y"], right_box["y"]) - min(
                left_box["y"] + left_box["height"],
                right_box["y"] + right_box["height"],
            )
            center_distance = abs(
                left_box["y"] + left_box["height"] / 2
                - right_box["y"] - right_box["height"] / 2
            )
            if (
                overlap >= shared_width * 0.9
                and vertical_gap <= max(1.0, min(left_box["height"], right_box["height"]) * 0.08)
                and center_distance >= min(left_box["height"], right_box["height"]) * 0.35
            ):
                union(left, right)

    grouped = {}
    for path in candidates:
        grouped.setdefault(find(id(path)), []).append(path)

    removed_ids = set()
    for members in grouped.values():
        if len(members) < 2:
            continue
        all_points = [point for member in members for point in member.get("points") or []]
        hull = _convex_hull(all_points)
        if len(hull) < 3:
            continue
        triangle_sides = [_triangle_side_from_path(member) for member in members]
        side_set = {side for side in triangle_sides if side in {"left", "right"}}
        if len(side_set) == 1 and len(side_set) == len(set(triangle_sides)):
            # CAD exporters frequently give the two arrow halves almost (but
            # not exactly) the same tip coordinate.  A convex hull then has a
            # tiny fourth corner.  Rebuild the icon from its bounds so it is a
            # clean, symmetric, filled left/right triangle.
            hull_xs = [float(point.get("x") or 0) for point in hull]
            hull_ys = [float(point.get("y") or 0) for point in hull]
            left_x, right_x = min(hull_xs), max(hull_xs)
            top_y, bottom_y = min(hull_ys), max(hull_ys)
            middle_y = (top_y + bottom_y) / 2
            side = next(iter(side_set))
            hull = (
                [{"x": right_x, "y": top_y}, {"x": left_x, "y": middle_y}, {"x": right_x, "y": bottom_y}]
                if side == "left"
                else [{"x": left_x, "y": top_y}, {"x": right_x, "y": middle_y}, {"x": left_x, "y": bottom_y}]
            )
        members[0]["points"] = hull
        members[0]["closed"] = True
        members[0]["fillStyle"] = "solid"
        members[0]["fillColor"] = members[0].get("fillColor") or "#111111"
        removed_ids.update(member.get("id") for member in members[1:])
    return [path for path in paths if path.get("id") not in removed_ids]


def _canonical_path_key(path):
    points = tuple((round(float(point.get("x") or 0), 3), round(float(point.get("y") or 0), 3)) for point in path.get("points") or [])
    reverse = tuple(reversed(points))
    return (
        str(path.get("name") or "").upper(),
        bool(path.get("closed")),
        str(path.get("fillStyle") or ""),
        min(points, reverse) if points else (),
    )


def _deduplicate_region_paths(paths, region):
    seen = set()
    result = []
    for path in paths:
        if not _box_center_in_region(_path_box(path), region):
            result.append(path)
            continue
        key = _canonical_path_key(path)
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def _deduplicate_region_texts(texts, region):
    seen = set()
    result = []
    for item in texts:
        if not _box_center_in_region(_item_box(item), region):
            result.append(item)
            continue
        key = (
            str(item.get("text") or item.get("name") or "").strip(),
            round(float(item.get("x") or 0), 3),
            round(float(item.get("y") or 0), 3),
            round(float(item.get("fontSize") or 0), 3),
            round(float(item.get("rotation") or 0), 3),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _remove_redundant_large_hatch_outlines(paths, board_width, board_height):
    """Remove a duplicate polyline frame when the same large area is already rendered by a hatch."""
    hatch_boxes = [
        _path_box(path)
        for path in paths
        if str(path.get("name") or "").upper() == "HATCH" and path.get("closed")
    ]
    if not hatch_boxes:
        return paths

    def is_duplicate_outline(path):
        if str(path.get("name") or "").upper() not in {"LWPOLYLINE", "POLYLINE"}:
            return False
        if not path.get("closed") or path.get("fillStyle") in {"hatch", "solid"}:
            return False
        box = _path_box(path)
        if box["width"] * box["height"] < board_width * board_height * 0.01:
            return False
        for hatch_box in hatch_boxes:
            width_ratio = box["width"] / max(hatch_box["width"], 1)
            height_ratio = box["height"] / max(hatch_box["height"], 1)
            if not (0.9 <= width_ratio <= 1.1 and 0.9 <= height_ratio <= 1.1):
                continue
            overlap_width = max(
                0,
                min(box["x"] + box["width"], hatch_box["x"] + hatch_box["width"])
                - max(box["x"], hatch_box["x"]),
            )
            overlap_height = max(
                0,
                min(box["y"] + box["height"], hatch_box["y"] + hatch_box["height"])
                - max(box["y"], hatch_box["y"]),
            )
            overlap_area = overlap_width * overlap_height
            smaller_area = min(
                box["width"] * box["height"],
                hatch_box["width"] * hatch_box["height"],
            )
            if overlap_area / max(smaller_area, 1) >= 0.55:
                return True
        return False

    return [path for path in paths if not is_duplicate_outline(path)]


def _remove_title_block_fill_artifacts(paths, region):
    """Remove narrow CAD hatch swatches that overlap title-block values."""
    result = []
    for path in paths:
        box = _path_box(path)
        is_title_block_hatch = (
            str(path.get("name") or "").upper() == "HATCH"
            and _box_center_in_region(box, region)
            and box["width"] >= box["height"] * 4
            and box["height"] <= region["height"] * 0.08
        )
        if not is_title_block_hatch:
            result.append(path)
    return result


def _use_recognized_project_text_in_title_block(texts, width, height):
    """Prefer the decoded project name to unreliable CAD outline glyphs.

    A few DWGs have stale/incorrect glyph outlines in the title block while
    their TEXT value is correct (for example ``上高路加油站``).  The browser
    normally prioritizes those outlines, which makes the on-screen project
    name disagree with the parsed value.  Limit the fallback to the project
    value cell so other CAD typography remains visually faithful.
    """
    for text in texts:
        value = re.sub(r"\s+", "", str(text.get("text") or text.get("name") or ""))
        box = _item_box(text)
        # The imported-image crop can shift the detected title-block region,
        # so use the stable lower-right sheet area rather than that crop.
        is_title_value_area = box["x"] >= width * 0.55 and box["y"] >= height * 0.68
        if "加油站" not in value or not is_title_value_area:
            continue
        text.pop("glyphPaths", None)
        text.pop("glyphBounds", None)
        text.pop("glyphFillRule", None)


def _normalize_imported_compass_orientation(paths, texts, width, height):
    """Turn a horizontal upper-left CAD compass into the vertical preview form.

    Some source DWGs draw the north needle pointing left with ``N`` beside it.
    The drawing board convention is a vertical needle with an upright ``N`` at
    its tip.  Rotate only the compact needle geometry around its own centre;
    the letter moves with the tip but deliberately remains upright.
    """
    upper_left_north_labels = []
    for text in texts:
        value = re.sub(r"\s+", "", str(text.get("text") or text.get("name") or "")).upper()
        box = _item_box(text)
        if value == "N" and box["x"] <= width * 0.22 and box["y"] <= height * 0.28:
            upper_left_north_labels.append(text)

    for label in upper_left_north_labels:
        label_x = float(label.get("x") or 0)
        label_y = float(label.get("y") or 0)
        needle_paths = []
        for path in paths:
            box = _path_box(path)
            if max(box["width"], box["height"]) > min(width, height) * 0.16:
                continue
            center_x = box["x"] + box["width"] / 2
            center_y = box["y"] + box["height"] / 2
            # The compass components sit tightly next to N.  A wider search
            # can accidentally absorb the first equipment symbol below it.
            if math.hypot(center_x - label_x, center_y - label_y) <= min(width, height) * 0.08:
                needle_paths.append(path)
        if not needle_paths:
            continue

        min_x = min(_path_box(path)["x"] for path in needle_paths)
        min_y = min(_path_box(path)["y"] for path in needle_paths)
        max_x = max(_path_box(path)["x"] + _path_box(path)["width"] for path in needle_paths)
        max_y = max(_path_box(path)["y"] + _path_box(path)["height"] for path in needle_paths)
        needle_width = max_x - min_x
        needle_height = max_y - min_y
        # A vertical needle is already in the desired form.  Restrict the
        # correction to labels placed beside a clearly horizontal needle.
        if needle_width <= needle_height * 1.25 or not (label_x >= max_x or label_x <= min_x):
            continue

        center_x = (min_x + max_x) / 2
        center_y = (min_y + max_y) / 2

        def rotate_counterclockwise(x, y):
            # SVG/canvas coordinates grow downwards, so -90° is visually CCW.
            return center_x + (y - center_y), center_y - (x - center_x)

        for path in needle_paths:
            for point in path.get("points") or []:
                point["x"], point["y"] = rotate_counterclockwise(
                    float(point.get("x") or 0),
                    float(point.get("y") or 0),
                )
        label["x"], label["y"] = rotate_counterclockwise(label_x, label_y)


def _normalize_north_labels(texts, paths, width, height):
    """Render the north-arrow N with the tall serif style used by the drawing.

    The N in an imported DWG is a separate CAD text entity, not part of the
    needle geometry.  Preserve its original compass direction, but move it a
    small distance outward from the needle so its glyph never overlaps the
    tip after the browser normalizes the font metrics.
    """
    for item in texts:
        value = re.sub(r"\s+", "", str(item.get("text") or item.get("name") or "")).upper()
        box = _item_box(item)
        if value != "N" or box["x"] > width * 0.18 or box["y"] > height * 0.25:
            continue
        label_center_x = box["x"] + box["width"] / 2
        label_center_y = box["y"] + box["height"] / 2
        max_needle_size = min(width, height) * 0.13
        nearby_paths = []
        for path in paths:
            path_box = _path_box(path)
            if max(path_box["width"], path_box["height"]) > max_needle_size:
                continue
            path_center_x = path_box["x"] + path_box["width"] / 2
            path_center_y = path_box["y"] + path_box["height"] / 2
            if math.hypot(path_center_x - label_center_x, path_center_y - label_center_y) <= min(width, height) * 0.11:
                nearby_paths.append(path_box)
        if nearby_paths:
            needle_left = min(path_box["x"] for path_box in nearby_paths)
            needle_top = min(path_box["y"] for path_box in nearby_paths)
            needle_right = max(path_box["x"] + path_box["width"] for path_box in nearby_paths)
            needle_bottom = max(path_box["y"] + path_box["height"] for path_box in nearby_paths)
            needle_center_x = (needle_left + needle_right) / 2
            needle_center_y = (needle_top + needle_bottom) / 2
            direction_x = label_center_x - needle_center_x
            direction_y = label_center_y - needle_center_y
            direction_length = math.hypot(direction_x, direction_y)
            if direction_length > 0.01:
                font_size = max(float(item.get("fontSize") or 0), min(width, height) * 0.028)
                outward_gap = max(font_size * 0.48, min(width, height) * 0.015)
                item["x"] = float(item.get("x") or 0) + direction_x / direction_length * outward_gap
                item["y"] = float(item.get("y") or 0) + direction_y / direction_length * outward_gap
        font_size = max(float(item.get("fontSize") or 0), min(width, height) * 0.028)
        item["cadRender"] = True
        item["cadFont"] = "Times New Roman"
        item["fontFamily"] = "Times New Roman, SimSun, serif"
        item["fontSize"] = font_size
        item["width"] = font_size * 0.72
        item["height"] = font_size * 1.16
        item["cadBoxWidth"] = 0
        item["textAnchor"] = "middle"
        item["dominantBaseline"] = "hanging"
        item["fontStyle"] = "normal"
        item["fontWeight"] = 400
        item.pop("glyphPaths", None)
        item.pop("glyphBounds", None)


def _fit_cad_text_to_enclosing_boxes(texts, paths):
    """Scale outlined CAD labels into their original small equipment boxes."""
    rectangles = []
    for path in paths:
        if not path.get("closed"):
            continue
        box = _path_box(path)
        if 8 <= box["width"] <= 220 and 8 <= box["height"] <= 110:
            rectangles.append(box)

    for item in texts:
        outlines = item.get("glyphPaths") or []
        bounds = item.get("glyphBounds") or {}
        glyph_width = float(bounds.get("width") or 0)
        glyph_height = float(bounds.get("height") or 0)
        if not outlines or glyph_width <= 0 or glyph_height <= 0:
            continue
        if abs(float(item.get("rotation") or 0) % 180) > 0.01:
            continue

        absolute_x = float(item.get("x") or 0) + float(bounds.get("x") or 0)
        absolute_y = float(item.get("y") or 0) + float(bounds.get("y") or 0)
        center_x = absolute_x + glyph_width / 2
        center_y = absolute_y + glyph_height / 2
        candidates = [
            box for box in rectangles
            if box["x"] - 1 <= center_x <= box["x"] + box["width"] + 1
            and box["y"] - 1 <= center_y <= box["y"] + box["height"] + 1
        ]
        if not candidates:
            continue
        target = min(candidates, key=lambda box: box["width"] * box["height"])
        target_width = target["width"] * 0.86
        target_height = target["height"] * 0.82
        scale = min(target_width / glyph_width, target_height / glyph_height, 1.0)

        # Only labels that exceed or nearly touch the CAD box need adjustment.
        if scale >= 0.96:
            continue
        local_center_x = float(bounds.get("x") or 0) + glyph_width / 2
        local_center_y = float(bounds.get("y") or 0) + glyph_height / 2
        target_local_x = target["x"] + target["width"] / 2 - float(item.get("x") or 0)
        target_local_y = target["y"] + target["height"] / 2 - float(item.get("y") or 0)
        for outline in outlines:
            for point in outline:
                point["x"] = target_local_x + (float(point["x"]) - local_center_x) * scale
                point["y"] = target_local_y + (float(point["y"]) - local_center_y) * scale

        fitted_width = glyph_width * scale
        fitted_height = glyph_height * scale
        item["glyphBounds"] = {
            "x": target_local_x - fitted_width / 2,
            "y": target_local_y - fitted_height / 2,
            "width": fitted_width,
            "height": fitted_height,
        }
        item["fontSize"] = max(float(item.get("fontSize") or 0) * scale, 4)
        item["width"] = fitted_width
        item["height"] = fitted_height


def _fit_compact_identifier_texts_to_enclosing_boxes(texts, paths):
    """Center equipment tags such as AT-1101 inside their nameplate boxes.

    Keeping these labels on one line avoids a stacked digit, but the CAD insert
    point is often the MTEXT top-left corner.  Without the recorded glyph
    outlines the browser text then looks tiny and top-left aligned.  Match the
    AutoCAD nameplate look by sizing the tag to the enclosing rectangle and
    anchoring it at the box centre.
    """
    rectangles = []
    for path in paths:
        if not path.get("closed"):
            continue
        box = _path_box(path)
        if 8 <= box["width"] <= 220 and 8 <= box["height"] <= 110:
            rectangles.append(box)
    if not rectangles:
        return

    for item in texts:
        label = str(item.get("text") or item.get("name") or "").strip()
        # Only in-box equipment codes (AT-110 / AT-1101).  Do not recenter
        # connector labels, bank ranges or short markers onto nearby boxes.
        if not _is_equipment_nameplate_label(label):
            continue
        if abs(float(item.get("rotation") or 0) % 180) > 0.01:
            continue

        bounds = item.get("glyphBounds") or {}
        glyph_width = float(bounds.get("width") or 0)
        glyph_height = float(bounds.get("height") or 0)
        if glyph_width > 0 and glyph_height > 0:
            center_x = float(item.get("x") or 0) + float(bounds.get("x") or 0) + glyph_width / 2
            center_y = float(item.get("y") or 0) + float(bounds.get("y") or 0) + glyph_height / 2
        else:
            text_box = _item_box(item)
            center_x = text_box["x"] + text_box["width"] / 2
            center_y = text_box["y"] + text_box["height"] / 2

        candidates = [
            box for box in rectangles
            if box["x"] - 2 <= center_x <= box["x"] + box["width"] + 2
            and box["y"] - 2 <= center_y <= box["y"] + box["height"] + 2
        ]
        if not candidates:
            continue
        target = min(candidates, key=lambda box: box["width"] * box["height"])
        width_factor = max(abs(float(item.get("widthFactor") or 1)), 0.01)
        target_width = target["width"] * 0.9
        target_height = target["height"] * 0.78
        font_by_height = target_height
        # Condensed CAD nameplate tags are narrower than a full em square.
        font_by_width = target_width / max(len(label) * 0.5 * width_factor, 0.01)
        font_size = max(min(font_by_height, font_by_width), 4)

        # Prefer the CAD glyph silhouette when it already fills the nameplate.
        if (
            item.get("glyphPaths")
            and glyph_width >= target_width * 0.82
            and glyph_height >= target_height * 0.82
        ):
            continue

        item["fontSize"] = font_size
        item["width"] = target_width
        item["height"] = target_height
        item["textAnchor"] = "middle"
        item["dominantBaseline"] = "central"
        item["x"] = target["x"] + target["width"] / 2
        item["y"] = target["y"] + target["height"] / 2
        item["cadRender"] = True
        item.pop("glyphPaths", None)
        item.pop("glyphBounds", None)
        item.pop("glyphFillRule", None)


def _separate_connector_identifier_labels(texts, paths):
    """Keep SC1/SC2 connector names above, rather than inside, their boxes."""
    rectangles = [
        _path_box(path)
        for path in paths
        if path.get("closed")
        and 8 <= _path_box(path)["width"] <= 80
        and 6 <= _path_box(path)["height"] <= 45
    ]
    for item in texts:
        value = re.sub(r"\s+", "", str(item.get("text") or item.get("name") or "")).upper()
        if not re.fullmatch(r"SC\d{1,3}", value):
            continue
        text_box = _item_box(item)
        text_center_x = text_box["x"] + text_box["width"] / 2
        candidates = [
            box for box in rectangles
            if box["x"] - 2 <= text_center_x <= box["x"] + box["width"] + 2
            and text_box["y"] < box["y"] + box["height"] / 2
            and text_box["y"] + text_box["height"] >= box["y"] - 3
            and box["y"] - (text_box["y"] + text_box["height"]) <= text_box["height"]
        ]
        if not candidates:
            continue
        target = min(candidates, key=lambda box: (box["y"] - text_box["y"], box["width"] * box["height"]))
        gap = max(2.5, float(item.get("fontSize") or 0) * 0.18)
        target_box_y = target["y"] - text_box["height"] - gap
        item["y"] = float(item.get("y") or 0) + target_box_y - text_box["y"]


def _anchor_room_labels_to_enclosing_boxes(texts, paths):
    """Place room captions consistently at the lower-left inside their room."""
    room_labels = {"配电室"}
    closed_boxes = [_path_box(path) for path in paths if path.get("closed")]

    def contains(outer, inner, tolerance=1):
        return (
            outer["x"] - tolerance <= inner["x"]
            and outer["y"] - tolerance <= inner["y"]
            and outer["x"] + outer["width"] + tolerance >= inner["x"] + inner["width"]
            and outer["y"] + outer["height"] + tolerance >= inner["y"] + inner["height"]
        )

    # A room frame is structural: it contains several independent equipment
    # boxes.  Absolute width/height thresholds are unsafe because importing a
    # calibrated preview can scale an ordinary cabinet above those thresholds.
    room_frames = []
    for outer in closed_boxes:
        outer_area = outer["width"] * outer["height"]
        if outer_area <= 0:
            continue
        nested_equipment = [
            inner for inner in closed_boxes
            if inner is not outer
            and inner["width"] < outer["width"] * 0.8
            and inner["height"] < outer["height"] * 0.8
            and inner["width"] * inner["height"] < outer_area * 0.45
            and contains(outer, inner)
        ]
        if len(nested_equipment) >= 3:
            room_frames.append(outer)

    for item in texts:
        value = re.sub(r"\s+", "", str(item.get("text") or item.get("name") or ""))
        if value not in room_labels:
            continue
        text_box = _item_box(item)
        center_x = text_box["x"] + text_box["width"] / 2
        center_y = text_box["y"] + text_box["height"] / 2
        candidates = [
            box for box in room_frames
            if box["x"] <= center_x <= box["x"] + box["width"]
            and box["y"] <= center_y <= box["y"] + box["height"]
        ]
        if not candidates:
            continue
        target = min(candidates, key=lambda box: box["width"] * box["height"])
        horizontal_gap = max(3, target["width"] * 0.02)
        vertical_gap = max(4, target["height"] * 0.03)
        target_box_x = target["x"] + horizontal_gap
        target_box_y = target["y"] + target["height"] - text_box["height"] - vertical_gap
        item["x"] = float(item.get("x") or 0) + target_box_x - text_box["x"]
        item["y"] = float(item.get("y") or 0) + target_box_y - text_box["y"]


def _assign_legend_icon_groups(paths, texts, region):
    """Treat every icon plus its legend label as one stable read-only row."""
    row_texts = [
        item for item in texts
        if item.get("readonlyGroup") == "legend"
        and re.sub(r"\s+", "", str(item.get("text") or item.get("name") or "")) != "图例"
    ]
    if not row_texts:
        return

    # Text embedded in a CAD symbol (for example the boxed regulator name)
    # and the explanatory text to its right belong to the same legend row.
    # Cluster by baseline and use the rightmost text as the row anchor.
    row_texts.sort(key=lambda item: _item_box(item)["y"] + _item_box(item)["height"] / 2)
    threshold = max(region["height"] * 0.018, 8)
    rows = []
    for item in row_texts:
        center_y = _item_box(item)["y"] + _item_box(item)["height"] / 2
        if rows and abs(rows[-1]["centerY"] - center_y) <= threshold:
            rows[-1]["items"].append(item)
            values = [
                _item_box(value)["y"] + _item_box(value)["height"] / 2
                for value in rows[-1]["items"]
            ]
            rows[-1]["centerY"] = sum(values) / len(values)
        else:
            rows.append({"centerY": center_y, "items": [item]})
    for row in rows:
        row["anchor"] = max(row["items"], key=lambda item: _item_box(item)["x"])
        row["groupId"] = f"readonly-legend-icon-{row['anchor'].get('id')}"
        for item in row["items"]:
            item["interactionGroupId"] = row["groupId"]

    max_distance = max(region["height"] / max(len(rows), 1), 12) * 0.75
    for path in paths:
        if path.get("readonlyGroup") != "legend":
            continue
        box = _path_box(path)
        if box["width"] >= region["width"] * 0.8 and box["height"] >= region["height"] * 0.8:
            continue
        center_x = box["x"] + box["width"] / 2
        center_y = box["y"] + box["height"] / 2
        nearest = min(rows, key=lambda row: abs(row["centerY"] - center_y))
        anchor_box = _item_box(nearest["anchor"])
        if center_x >= anchor_box["x"] or abs(nearest["centerY"] - center_y) > max_distance:
            continue
        path["interactionGroupId"] = nearest["groupId"]


def _report_image_mask_regions(width, height):
    regions = _report_image_chrome_regions(width, height)
    return [
        _expand_region(regions["inspector"], width * 0.012, width, height),
        _expand_region(regions["borderTop"], width * 0.006, width, height),
        _expand_region(regions["borderRight"], width * 0.006, width, height),
        _expand_region(regions["borderBottom"], width * 0.006, width, height),
        _expand_region(regions["borderLeft"], width * 0.006, width, height),
    ]


def _round_report_image_coord(value):
    return round(float(value) * 100) / 100


def _report_image_crop_svg(data_url, image_width, image_height, crop):
    x = _round_report_image_coord(crop["x"])
    y = _round_report_image_coord(crop["y"])
    width = _round_report_image_coord(crop["width"])
    height = _round_report_image_coord(crop["height"])
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}"><rect width="{width}" height="{height}" fill="#fff"/>'
        f'<image href="{data_url}" x="{-x}" y="{-y}" width="{image_width}" height="{image_height}" '
        'preserveAspectRatio="none"/></svg>'
    )


def _report_image_full_svg(data_url, width, height):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:g}" height="{height:g}" viewBox="0 0 {width:g} {height:g}">'
        f'<rect width="100%" height="100%" fill="#fff"/><image href="{data_url}" x="0" y="0" width="{width:g}" height="{height:g}" '
        'preserveAspectRatio="none"/></svg>'
    )


def _is_inspector_label(text):
    value = re.sub(r"[\s：:]+", "", str(text.get("text") or text.get("name") or ""))
    return value == "检测人员"


def _distance(first, second):
    return math.hypot(float(first.get("x", 0)) - float(second.get("x", 0)), float(first.get("y", 0)) - float(second.get("y", 0)))


def _arc_points(center, radius, start_angle, end_angle, segments):
    cx, cy = _xy(center) or (0.0, 0.0)
    start = float(start_angle)
    end = float(end_angle)
    if end <= start:
        end += 360
    count = max(int(segments * (end - start) / 360), 4)
    return [
        (
            cx + float(radius) * math.cos(math.radians(start + (end - start) * index / count)),
            cy + float(radius) * math.sin(math.radians(start + (end - start) * index / count)),
        )
        for index in range(count + 1)
    ]


def _xy(value):
    try:
        return float(value[0]), float(value[1])
    except (TypeError, ValueError, IndexError):
        try:
            return float(value.x), float(value.y)
        except (TypeError, ValueError, AttributeError):
            return None


def _handles(handle):
    return [str(handle)] if handle else []


def _positive_int(value, fallback):
    try:
        result = int(value)
    except (TypeError, ValueError):
        return fallback
    return result if result > 0 else fallback
