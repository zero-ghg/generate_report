import json
import re
import sys
from base64 import b64decode
from copy import deepcopy
from datetime import datetime
from html import escape
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_ALIGN_VERTICAL, WD_ROW_HEIGHT_RULE
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.opc.part import Part
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.oxml.shape import CT_Inline
from docx.shared import Cm, Pt


FONT_NAME = "宋体"
EMPTY = "—"
COMPACT_TABLE_FONT_SIZE = 5.5
COVER_UNDERLINE_TAB_POS = "7200"
SUBPROJECT_TARGET_HEIGHT_CM = 18.8
SUBPROJECT_DATA_ROW_HEIGHT_CM = 0.38
SUBPROJECT_REMARK_MIN_HEIGHT_CM = 0.65
MEASUREMENT_REMARK_HEIGHT_CM = 1.15
LEGEND_IMAGE_MAX_WIDTH_CM = 16.2
LEGEND_IMAGE_MAX_HEIGHT_CM = 22.5


def main():
    if len(sys.argv) < 4:
        raise SystemExit("Usage: python scripts/generate_formatted_report.py template.docx input.json output.docx")

    template_path = Path(sys.argv[1])
    input_path = Path(sys.argv[2])
    output_path = Path(sys.argv[3])
    data = json.loads(input_path.read_text(encoding="utf-8-sig"))

    buffer = build_formatted_report_docx(data, template_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(buffer.getvalue())


def build_formatted_report_docx(data, template_path):
    template_path = Path(template_path)
    if not template_path.exists():
        raise FileNotFoundError(f"报告模板不存在: {template_path}")

    document = Document(template_path)
    template_tables = [deepcopy(table._tbl) for table in document.tables]
    template_heading = first_report_heading_paragraph(document)

    update_cover_text(document, data)
    remove_report_body_after_first_chapter(document)
    toc_entries = append_report_body(document, data, template_tables, template_heading)
    replace_toc_cache(document, toc_entries)
    update_fields_on_open(document)

    buffer = BytesIO()
    document.save(buffer)
    buffer.seek(0)
    return buffer


def update_cover_text(document, data):
    general = data.get("assistant", {}).get("general", {})
    replacements = {
        "委托单位名称": general.get("clientName"),
        "受检项目名称": general.get("projectName"),
        "受检项目地址": general.get("projectAddress"),
        "联   系   人": general.get("contactName"),
        "电        话": general.get("contactPhone"),
        "本次检测时间": general.get("inspectedDate"),
        "下次检测时间": general.get("nextDate"),
        "检测机构名称": general.get("agencyName"),
        "检测机构地址": general.get("agencyAddress"),
        "检测机构电话": general.get("agencyPhone"),
    }
    for paragraph in iter_all_paragraphs(document):
        text = paragraph.text.strip()
        for label, replacement in replacements.items():
            if text.startswith(label):
                existing_value = text[len(label) :].strip()
                value = replacement or existing_value
                if value:
                    replace_cover_paragraph_with_underlined_value(paragraph, label, value)


def iter_all_paragraphs(document):
    for paragraph_element in document._element.xpath(".//w:p"):
        yield paragraph_element_to_paragraph(paragraph_element)


def paragraph_element_to_paragraph(paragraph_element):
    from docx.text.paragraph import Paragraph

    return Paragraph(paragraph_element, None)


def replace_cover_paragraph_with_underlined_value(paragraph, label, replacement):
    template_rpr = first_paragraph_run_properties(paragraph)
    value, suffix = split_cover_suffix(str(replacement))
    for child in list(paragraph._p):
        if child.tag == qn("w:r"):
            paragraph._p.remove(child)
    set_cover_value_tab_stop(paragraph)

    label_run = paragraph.add_run(f"{label} ")
    if template_rpr is not None:
        label_run._r.insert(0, deepcopy(template_rpr))
    set_run_font(label_run, size=label_run.font.size.pt if label_run.font.size else None, bold=bool(label_run.bold) if label_run.bold is not None else None)

    value_run = paragraph.add_run(value)
    if template_rpr is not None:
        value_run._r.insert(0, deepcopy(template_rpr))
    set_run_font(value_run, size=value_run.font.size.pt if value_run.font.size else None, bold=bool(value_run.bold) if value_run.bold is not None else None)
    value_run.underline = True

    tab_run = paragraph.add_run()
    if template_rpr is not None:
        tab_run._r.insert(0, deepcopy(template_rpr))
    set_run_font(tab_run, size=tab_run.font.size.pt if tab_run.font.size else None, bold=bool(tab_run.bold) if tab_run.bold is not None else None)
    tab_run.underline = True
    tab_run._r.append(OxmlElement("w:tab"))

    if suffix:
        suffix_run = paragraph.add_run(suffix)
        if template_rpr is not None:
            suffix_run._r.insert(0, deepcopy(template_rpr))
        set_run_font(suffix_run, size=suffix_run.font.size.pt if suffix_run.font.size else None, bold=bool(suffix_run.bold) if suffix_run.bold is not None else None)


def split_cover_suffix(value):
    for marker in ("（盖章）", "(盖章)"):
        if marker in value:
            before, after = value.split(marker, 1)
            return before.rstrip(), marker + after
    return value.strip(), ""


def set_cover_value_tab_stop(paragraph):
    p_pr = paragraph._p.get_or_add_pPr()
    tabs = p_pr.find(qn("w:tabs"))
    if tabs is None:
        tabs = OxmlElement("w:tabs")
        p_pr.append(tabs)
    tab = OxmlElement("w:tab")
    tab.set(qn("w:val"), "right")
    tab.set(qn("w:pos"), COVER_UNDERLINE_TAB_POS)
    tabs.append(tab)


def update_fields_on_open(document):
    settings = document.settings._element
    update_fields = settings.find(qn("w:updateFields"))
    if update_fields is None:
        update_fields = OxmlElement("w:updateFields")
        settings.append(update_fields)
    update_fields.set(qn("w:val"), "true")

    for fld_char in document._element.xpath(".//w:fldChar[@w:fldCharType='begin']"):
        fld_char.set(qn("w:dirty"), "true")


def replace_toc_cache(document, toc_entries):
    for sdt in document._element.body.xpath("./w:sdt"):
        if "TOC " not in "".join(sdt.itertext()):
            continue
        content = sdt.find(qn("w:sdtContent"))
        if content is None:
            return
        for child in list(content):
            content.remove(child)
        content.append(build_toc_title_paragraph())
        for title, page_no in toc_entries:
            content.append(build_toc_entry_paragraph(title, page_no))
        return


def build_toc_title_paragraph():
    paragraph = OxmlElement("w:p")
    p_pr = OxmlElement("w:pPr")
    jc = OxmlElement("w:jc")
    jc.set(qn("w:val"), "center")
    p_pr.append(jc)
    paragraph.append(p_pr)
    run = toc_run("目    录", size=20, bold=True)
    paragraph.append(run)
    return paragraph


def build_toc_entry_paragraph(title, page_no):
    paragraph = OxmlElement("w:p")
    p_pr = OxmlElement("w:pPr")
    tabs = OxmlElement("w:tabs")
    tab = OxmlElement("w:tab")
    tab.set(qn("w:val"), "right")
    tab.set(qn("w:leader"), "dot")
    tab.set(qn("w:pos"), "8500")
    tabs.append(tab)
    p_pr.append(tabs)
    paragraph.append(p_pr)
    paragraph.append(toc_run(title, size=14))
    run_tab = OxmlElement("w:r")
    run_tab.append(OxmlElement("w:tab"))
    paragraph.append(run_tab)
    paragraph.append(toc_run(str(page_no), size=14))
    return paragraph


def toc_run(text, size=14, bold=False):
    run = OxmlElement("w:r")
    r_pr = OxmlElement("w:rPr")
    fonts = OxmlElement("w:rFonts")
    fonts.set(qn("w:ascii"), FONT_NAME)
    fonts.set(qn("w:hAnsi"), FONT_NAME)
    fonts.set(qn("w:eastAsia"), FONT_NAME)
    r_pr.append(fonts)
    if bold:
        r_pr.append(OxmlElement("w:b"))
        r_pr.append(OxmlElement("w:bCs"))
    sz = OxmlElement("w:sz")
    sz.set(qn("w:val"), str(size * 2))
    r_pr.append(sz)
    sz_cs = OxmlElement("w:szCs")
    sz_cs.set(qn("w:val"), str(size * 2))
    r_pr.append(sz_cs)
    run.append(r_pr)
    t = OxmlElement("w:t")
    t.text = text
    run.append(t)
    return run


def remove_report_body_after_first_chapter(document):
    body = document._element.body
    children = list(body)
    start_index = None
    for index, child in enumerate(children):
        if child.tag != qn("w:p"):
            continue
        text = "".join(child.itertext()).strip()
        if text.startswith("一、总表"):
            start_index = index
            break

    if start_index is None:
        return

    sect_pr = body.sectPr
    for child in children[start_index:]:
        if child.tag == qn("w:sectPr"):
            continue
        if child.getparent() is body:
            body.remove(child)
    if sect_pr is not None and sect_pr.getparent() is None:
        body.append(sect_pr)


def append_report_body(document, data, template_tables, template_heading):
    toc_entries = []
    page_no = 1

    start_report_body_section(document)
    add_heading(document, "一、总表", template_heading, break_before=False)
    toc_entries.append(("一、总表", page_no))
    summary_table = append_template_table(document, template_tables[0])
    fill_summary_table(summary_table, data)

    overview_projects = data.get("assistant", {}).get("overview", {}).get("projects", [])
    page_no += 1
    page_no += add_subproject_section(
        document,
        "二、子项目表（概况）",
        overview_projects,
        data,
        template_tables[1],
        template_heading,
        toc_entries,
        page_no,
    )

    power_projects = data.get("assistant", {}).get("power", {}).get("projects", [])
    page_no += add_subproject_section(
        document,
        "三、子项目表（低压电源系统）",
        power_projects,
        data,
        template_tables[10],
        template_heading,
        toc_entries,
        page_no,
    )

    electronic_projects = data.get("assistant", {}).get("electronic", {}).get("projects", [])
    if electronic_projects:
        page_no += add_subproject_section(
            document,
            "四、子项目表（电子信息系统）",
            electronic_projects,
            data,
            template_tables[10],
            template_heading,
            toc_entries,
            page_no,
        )

    grounding_pages = measurement_page_count(data.get("reportTables", {}).get("grounding", []), template_tables[24], "grounding")
    if grounding_pages:
        add_heading(document, "五、子项目表（接地电阻）", template_heading)
        toc_entries.append(("五、子项目表（接地电阻）", page_no))
        add_template_measurement_tables(
        document,
        data.get("reportTables", {}).get("grounding", []),
        template_tables[24],
        "grounding",
        data,
        )
        page_no += grounding_pages

    transition_pages = measurement_page_count(data.get("reportTables", {}).get("transition", []), template_tables[35], "transition")
    if transition_pages:
        add_heading(document, "六、子项目表（过渡电阻）", template_heading)
        toc_entries.append(("六、子项目表（过渡电阻）", page_no))
        add_template_measurement_tables(
        document,
        data.get("reportTables", {}).get("transition", []),
        template_tables[35],
        "transition",
        data,
        )
        page_no += transition_pages

    spd_pages = measurement_page_count(data.get("reportTables", {}).get("spd", []), template_tables[48], "spd")
    if spd_pages:
        add_heading(document, "七、子项目表（SPD明细表）", template_heading)
        toc_entries.append(("七、子项目表（SPD明细表）", page_no))
        add_template_measurement_tables(
        document,
        data.get("reportTables", {}).get("spd", []),
        template_tables[48],
        "spd",
        data,
        )
        page_no += spd_pages

    legend = data.get("legend", {})
    legend_page_count = legend_content_page_count(legend)
    if legend_page_count:
        add_heading(document, "八、现场平面示意图", template_heading)
        toc_entries.append(("八、现场平面示意图", page_no))
        add_legend_tables(document, legend)
        page_no += legend_page_count

    return toc_entries


def append_template_table(document, table_element):
    tbl = deepcopy(table_element)
    document._element.body.insert(len(document._element.body) - 1, tbl)
    return document.tables[-1]


def first_report_heading_paragraph(document):
    for paragraph in document.paragraphs:
        if paragraph.text.strip().startswith("一、总表"):
            return deepcopy(paragraph._p)
    return None


def start_report_body_section(document):
    section = document.sections[-1]
    restart_section_page_numbering(section)
    section.footer.is_linked_to_previous = False
    paragraph = section.footer.paragraphs[0]
    clear_paragraph_runs(paragraph)
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    add_footer_text(paragraph, "第 ")
    add_field(paragraph, "PAGE")
    add_footer_text(paragraph, " 页 共 ")
    add_field(paragraph, "SECTIONPAGES")
    add_footer_text(paragraph, " 页")


def restart_section_page_numbering(section):
    sect_pr = section._sectPr
    pg_num_type = sect_pr.find(qn("w:pgNumType"))
    if pg_num_type is None:
        pg_num_type = OxmlElement("w:pgNumType")
        sect_pr.append(pg_num_type)
    pg_num_type.set(qn("w:start"), "1")


def clear_paragraph_runs(paragraph):
    for child in list(paragraph._p):
        if child.tag == qn("w:r"):
            paragraph._p.remove(child)


def add_footer_text(paragraph, text):
    run = paragraph.add_run(text)
    set_run_font(run, size=9, bold=False)


def add_field(paragraph, field_code):
    run_begin = paragraph.add_run()
    fld_begin = OxmlElement("w:fldChar")
    fld_begin.set(qn("w:fldCharType"), "begin")
    run_begin._r.append(fld_begin)

    run_instr = paragraph.add_run()
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = f" {field_code} "
    run_instr._r.append(instr)

    run_sep = paragraph.add_run()
    fld_sep = OxmlElement("w:fldChar")
    fld_sep.set(qn("w:fldCharType"), "separate")
    run_sep._r.append(fld_sep)

    run_text = paragraph.add_run("1")
    set_run_font(run_text, size=9, bold=False)

    run_end = paragraph.add_run()
    fld_end = OxmlElement("w:fldChar")
    fld_end.set(qn("w:fldCharType"), "end")
    run_end._r.append(fld_end)


def fill_summary_table(table, data):
    general = data.get("assistant", {}).get("general", {})
    project_items = general.get("projectItems", [])
    equipment = list(general.get("equipment", []))[:6]

    set_cell(table.cell(0, 3), general.get("clientName"), align=WD_ALIGN_PARAGRAPH.CENTER)
    set_cell(table.cell(1, 3), general.get("projectName"), align=WD_ALIGN_PARAGRAPH.CENTER)
    set_cell(table.cell(2, 3), general.get("projectAddress"), align=WD_ALIGN_PARAGRAPH.CENTER)
    set_cell(table.cell(3, 3), general.get("contactDepartment"), align=WD_ALIGN_PARAGRAPH.CENTER)
    set_cell(table.cell(3, 6), general.get("contactName"), align=WD_ALIGN_PARAGRAPH.CENTER)
    set_cell(table.cell(3, 14), general.get("contactPhone"), align=WD_ALIGN_PARAGRAPH.CENTER)
    set_cell(table.cell(4, 3), general.get("inspectedDate"), align=WD_ALIGN_PARAGRAPH.CENTER)
    set_cell(table.cell(5, 3), general.get("nextDate"), align=WD_ALIGN_PARAGRAPH.CENTER)
    set_cell(table.cell(5, 11), general.get("detectionType"), align=WD_ALIGN_PARAGRAPH.CENTER)
    set_cell(table.cell(6, 3), general.get("detectionBasis"), align=WD_ALIGN_PARAGRAPH.LEFT)

    for row_index in range(8, 14):
        item = equipment[row_index - 8] if row_index - 8 < len(equipment) else {}
        set_cell(table.cell(row_index, 1), item.get("name"), align=WD_ALIGN_PARAGRAPH.CENTER, empty_text="")
        set_cell(table.cell(row_index, 4), item.get("model"), align=WD_ALIGN_PARAGRAPH.CENTER, empty_text="")
        set_cell(table.cell(row_index, 7), item.get("serial"), align=WD_ALIGN_PARAGRAPH.CENTER, empty_text="")
        set_cell(table.cell(row_index, 10), item.get("range"), align=WD_ALIGN_PARAGRAPH.CENTER, empty_text="")
        set_cell(table.cell(row_index, 14), item.get("calibrationDate"), align=WD_ALIGN_PARAGRAPH.CENTER, empty_text="")

    for row_index in range(16, 27):
        item = project_items[row_index - 16] if row_index - 16 < len(project_items) else {}
        set_cell(table.cell(row_index, 0), item.get("id"), align=WD_ALIGN_PARAGRAPH.CENTER, empty_text="")
        set_cell(table.cell(row_index, 2), item.get("name"), align=WD_ALIGN_PARAGRAPH.CENTER, empty_text="")
        set_cell(table.cell(row_index, 5), item.get("lightningCategory"), align=WD_ALIGN_PARAGRAPH.CENTER, empty_text="")
        set_cell(table.cell(row_index, 9), item.get("lightningProtectionLevel"), align=WD_ALIGN_PARAGRAPH.CENTER, empty_text="")
        set_cell(table.cell(row_index, 13), item.get("pages"), align=WD_ALIGN_PARAGRAPH.CENTER, empty_text="")

    conclusion = general.get("conclusion")
    set_cell(table.cell(27, 2), conclusion, align=WD_ALIGN_PARAGRAPH.LEFT)
    set_cell(table.cell(28, 2), general.get("tester"), align=WD_ALIGN_PARAGRAPH.CENTER, empty_text="")
    set_cell(table.cell(28, 8), general.get("reviewer"), align=WD_ALIGN_PARAGRAPH.CENTER, empty_text="")
    set_cell(table.cell(28, 15), general.get("signer"), align=WD_ALIGN_PARAGRAPH.CENTER, empty_text="")
    trim_summary_optional_rows(table, len(equipment), len(project_items))


def trim_summary_optional_rows(table, equipment_count, project_count):
    if project_count:
        for row_index in range(26, 16 + project_count - 1, -1):
            table._tbl.remove(table.rows[row_index]._tr)
    else:
        for row_index in range(26, 13, -1):
            table._tbl.remove(table.rows[row_index]._tr)

    if equipment_count:
        for row_index in range(13, 8 + equipment_count - 1, -1):
            table._tbl.remove(table.rows[row_index]._tr)
    else:
        for row_index in range(13, 6, -1):
            table._tbl.remove(table.rows[row_index]._tr)


def add_subproject_section(document, heading, projects, data, template_table, template_heading, toc_entries=None, page_no=None):
    projects = [project for project in projects if project.get("rows")]
    if not projects:
        return 0

    add_heading(document, heading, template_heading)
    if toc_entries is not None and page_no is not None:
        toc_entries.append((heading, page_no))
    for index, project in enumerate(projects):
        if index:
            document.add_page_break()
            add_table_top_spacing(document)
        add_subproject_table(document, project, data, template_table)
    return len(projects)


def format_detection_type(raw):
    if raw == "验收检测":
        return "☑验收检测  □定期检测"
    if raw == "定期检测":
        return "□验收检测  ☑定期检测"
    return raw


def add_subproject_table(document, project, data, template_table):
    rows = project.get("rows", [])
    table = append_template_table(document, template_table)
    trim_subproject_template_rows(table, len(rows))
    positions = subproject_positions(table)
    row_meta = []

    set_cell(table.cell(0, positions["name"]), subproject_name(project, data), bold=True, align=WD_ALIGN_PARAGRAPH.CENTER)
    set_cell(table.cell(0, positions["date"]), project.get("inspectionDate"), bold=True, align=WD_ALIGN_PARAGRAPH.CENTER)

    for index, row_data in enumerate(rows):
        row_index = index + 2
        if row_index >= len(table.rows) - 1:
            break
        row_meta.append((row_data.get("category"), row_data.get("subcategory")))
        if positions.get("category") is not None:
            category_cell = table.cell(row_index, positions["category"])
            set_cell(category_cell, row_data.get("category"), bold=True, align=WD_ALIGN_PARAGRAPH.CENTER, empty_text="")
        if positions.get("subcategory") is not None:
            subcategory_cell = table.cell(row_index, positions["subcategory"])
            if positions.get("category") is None or subcategory_cell._tc is not table.cell(row_index, positions["category"])._tc:
                set_cell(subcategory_cell, row_data.get("subcategory"), bold=True, align=WD_ALIGN_PARAGRAPH.CENTER, empty_text="")
        set_cell(table.cell(row_index, positions["content"]), row_data.get("content"), bold=True)
        set_cell(table.cell(row_index, positions["standard"]), row_data.get("standard"))
        set_cell(table.cell(row_index, positions["result"]), row_data.get("result"), align=WD_ALIGN_PARAGRAPH.CENTER)
        set_cell(table.cell(row_index, positions["conclusion"]), row_data.get("conclusion"), align=WD_ALIGN_PARAGRAPH.CENTER)
    merge_subproject_category_columns(table, 2, row_meta, positions)


def subproject_positions(table):
    if len(table.columns) == 7:
        return {
            "name": 2,
            "date": 5,
            "category": 0,
            "subcategory": None,
            "content": 1,
            "standard": 3,
            "result": 4,
            "conclusion": 6,
        }
    return {
        "name": 3,
        "date": 6,
        "category": 0,
        "subcategory": 1,
        "content": 2,
        "standard": 4,
        "result": 5,
        "conclusion": 7,
    }


def trim_subproject_template_rows(table, data_row_count):
    first_data_row = 2
    remark_row_index = len(table.rows) - 1
    keep_until = first_data_row + data_row_count
    for row_index in range(remark_row_index - 1, keep_until - 1, -1):
        table._tbl.remove(table.rows[row_index]._tr)
    stretch_template_remark_row(table, data_row_count)


def stretch_template_remark_row(table, data_row_count):
    remark_row = table.rows[-1]
    if is_long_text_subproject_table(table, data_row_count):
        remark_height = SUBPROJECT_REMARK_MIN_HEIGHT_CM
    else:
        used_height = estimate_subproject_table_height(table, data_row_count)
        remark_height = max(SUBPROJECT_REMARK_MIN_HEIGHT_CM, SUBPROJECT_TARGET_HEIGHT_CM - used_height)
    remark_row.height = Cm(remark_height)
    remark_row.height_rule = WD_ROW_HEIGHT_RULE.EXACTLY
    for cell in unique_row_cells(remark_row):
        cell.vertical_alignment = WD_ALIGN_VERTICAL.TOP
        set_cell(cell, "备注：", bold=True, align=WD_ALIGN_PARAGRAPH.LEFT, empty_text="")


def is_long_text_subproject_table(table, data_row_count):
    return len(table.columns) == 7 and data_row_count >= 16


def estimate_subproject_table_height(table, data_row_count):
    header_height = 1.75
    data_height = 0
    for row_index in range(2, min(2 + data_row_count, len(table.rows) - 1)):
        max_lines = 1
        for cell in unique_row_cells(table.rows[row_index]):
            max_lines = max(max_lines, estimate_cell_lines(cell.text))
        data_height += max(SUBPROJECT_DATA_ROW_HEIGHT_CM, 0.34 + (max_lines - 1) * 0.22)
    return header_height + data_height


def estimate_cell_lines(text):
    if not text:
        return 1
    lines = 0
    for part in str(text).splitlines() or [""]:
        length = len(part.strip())
        if length <= 12:
            lines += 1
        elif length <= 26:
            lines += 2
        elif length <= 44:
            lines += 3
        else:
            lines += 4
    return max(1, lines)


def merge_subproject_category_columns(table, first_data_row, row_meta, positions):
    if not row_meta:
        return

    category_col = positions.get("category")
    subcategory_col = positions.get("subcategory")
    if category_col is None:
        return

    index = 0
    while index < len(row_meta):
        category = row_meta[index][0]
        end = index
        while end + 1 < len(row_meta) and row_meta[end + 1][0] == category:
            end += 1

        row_start = first_data_row + index
        row_end = first_data_row + end
        if row_start != row_end:
            merged = safe_merge_vertical(table, row_start, row_end, category_col)
            set_cell(merged, category, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER, empty_text="")
        else:
            set_cell(table.cell(row_start, category_col), category, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER, empty_text="")

        if subcategory_col is not None:
            for sub_index in range(index, end + 1):
                subcategory = row_meta[sub_index][1]
                set_cell(
                    table.cell(first_data_row + sub_index, subcategory_col),
                    subcategory,
                    bold=True,
                    align=WD_ALIGN_PARAGRAPH.CENTER,
                    empty_text="",
                )
        index = end + 1


def safe_merge_vertical(table, row_start, row_end, col):
    try:
        return table.cell(row_start, col).merge(table.cell(row_end, col))
    except ValueError:
        return table.cell(row_start, col)


def add_remark_row(table, data_row_count):
    row = table.add_row()
    cell = row.cells[0].merge(row.cells[-1])
    cell.vertical_alignment = WD_ALIGN_VERTICAL.TOP
    set_sub_cell(cell, "备注：", bold=True, align=WD_ALIGN_PARAGRAPH.LEFT)

    total_rows_before_remark = data_row_count + 2
    if data_row_count >= 16:
        remark_height_cm = 0.35
    else:
        remark_height_cm = max(0.45, 13.8 - total_rows_before_remark * 0.42)
    row.height = Cm(remark_height_cm)
    row.height_rule = WD_ROW_HEIGHT_RULE.AT_LEAST


def compact_subproject_table(table):
    for row in table.rows[:-1]:
        row.height = Cm(0.25)
        row.height_rule = WD_ROW_HEIGHT_RULE.AT_LEAST
    for row in table.rows:
        for cell in row.cells:
            set_cell_margins(cell, top=0, start=25, bottom=0, end=25)


def subproject_name(project, data):
    return project.get("projectName")


def add_measurement_table(document, rows, columns):
    table = document.add_table(rows=1, cols=len(columns))
    table.autofit = True
    style_table(table)
    for idx, (_, header) in enumerate(columns):
        set_cell(table.cell(0, idx), header, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER)
    for row_data in rows:
        cells = table.add_row().cells
        for idx, (key, _) in enumerate(columns):
            align = WD_ALIGN_PARAGRAPH.CENTER if key in {"marker", "standardValue", "measuredValue", "result"} else WD_ALIGN_PARAGRAPH.LEFT
            set_cell(cells[idx], row_data.get(key), align=align)


def add_template_measurement_tables(document, rows, template_table, kind, data):
    groups = group_rows_by_place(rows)
    rows_per_table = measurement_rows_per_table(template_table, kind)
    table_index = 0
    for place_name, group_rows in groups:
        for chunk in chunk_rows(group_rows, rows_per_table):
            if table_index:
                document.add_page_break()
                add_measurement_continuation_spacing(document)
            add_template_measurement_table(document, chunk, template_table, kind, data, place_name, rows_per_table)
            table_index += 1


def measurement_page_count(rows, template_table, kind):
    rows_per_table = measurement_rows_per_table(template_table, kind)
    page_count = 0
    for _, group_rows in group_rows_by_place(rows):
        page_count += len(chunk_rows(group_rows, rows_per_table))
    return page_count


def add_measurement_continuation_spacing(document):
    add_table_top_spacing(document)


def add_table_top_spacing(document):
    paragraph = document.add_paragraph()
    paragraph.paragraph_format.space_before = Pt(0)
    paragraph.paragraph_format.space_after = Pt(18)


def add_template_measurement_table(document, group_rows, template_table, kind, data, place_name, rows_per_table):
    table = append_template_table(document, template_table)
    trim_measurement_rows_to_fit(table, len(group_rows), rows_per_table)
    prepare_measurement_template_rows(table)
    positions = measurement_positions(kind)
    set_cell(table.cell(0, positions["name"]), place_name, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER)
    set_cell(table.cell(0, positions["date"]), measurement_date(data), bold=True, align=WD_ALIGN_PARAGRAPH.CENTER, size=9)
    for row_index, row_data in enumerate(group_rows, start=2):
        if row_index >= len(table.rows) - 1:
            break
        fill_measurement_row(table, row_index, row_data, kind)


def chunk_rows(rows, size):
    if not rows:
        return [[]]
    return [rows[index : index + size] for index in range(0, len(rows), size)]


def measurement_rows_per_table(template_table, kind):
    template_row_count = len(template_table.xpath("./w:tr"))
    reserve_rows = {
        "grounding": 4,
        "transition": 5,
        "spd": 4,
    }.get(kind, 4)
    return max(1, template_row_count - reserve_rows)


def trim_measurement_rows_to_fit(table, data_row_count, max_data_rows):
    first_data_row = 2
    remark_row_index = len(table.rows) - 1
    keep_data_rows = max(data_row_count, max_data_rows)
    keep_until = min(first_data_row + keep_data_rows, remark_row_index)
    for row_index in range(remark_row_index - 1, keep_until - 1, -1):
        table._tbl.remove(table.rows[row_index]._tr)


def add_template_measurement_tables_old(document, rows, template_table, kind, data):
    groups = group_rows_by_place(rows)
    for index, (place_name, group_rows) in enumerate(groups):
        if index:
            document.add_page_break()
        table = append_template_table(document, template_table)
        prepare_measurement_template_rows(table)
        positions = measurement_positions(kind)
        set_cell(table.cell(0, positions["name"]), place_name, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER)
        set_cell(table.cell(0, positions["date"]), measurement_date(data), bold=True, align=WD_ALIGN_PARAGRAPH.CENTER)
        for row_index, row_data in enumerate(group_rows, start=2):
            if row_index >= len(table.rows) - 1:
                break
            fill_measurement_row(table, row_index, row_data, kind)


def group_rows_by_place(rows):
    groups = []
    indexes = {}
    for row in rows:
        place_name = row.get("placeName") or ""
        if place_name not in indexes:
            indexes[place_name] = len(groups)
            groups.append((place_name, []))
        groups[indexes[place_name]][1].append(row)
    return groups


def measurement_date(data):
    raw = (
        data.get("legend", {}).get("inspectionDateValue")
        or data.get("legend", {}).get("inspectionDate")
        or data.get("assistant", {}).get("general", {}).get("inspectedDate")
    )
    return format_chinese_date(raw)


def format_chinese_date(raw):
    if not raw:
        return raw
    text = str(raw).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            date = datetime.strptime(text, fmt)
            return f"{date.year}年{date.month}月{date.day}日"
        except ValueError:
            pass
    return text


def measurement_positions(kind):
    if kind == "grounding":
        return {"name": 2, "date": 6}
    if kind == "transition":
        return {"name": 2, "date": 6}
    if kind == "spd":
        return {"name": 2, "date": 6}
    return {"name": 2, "date": 6}


def fill_measurement_row(table, row_index, row_data, kind):
    if kind == "grounding":
        set_cell(table.cell(row_index, 0), row_data.get("marker"), align=WD_ALIGN_PARAGRAPH.CENTER)
        set_cell(table.cell(row_index, 1), join_nonempty(row_data.get("workLocation"), row_data.get("equipmentName")))
        set_cell(table.cell(row_index, 3), row_data.get("conductorSpec"))
        set_cell(table.cell(row_index, 4), row_data.get("protectionZone"), align=WD_ALIGN_PARAGRAPH.CENTER)
        set_cell(table.cell(row_index, 5), row_data.get("standardValue"), align=WD_ALIGN_PARAGRAPH.CENTER)
        set_cell(table.cell(row_index, 6), row_data.get("measuredValue"), align=WD_ALIGN_PARAGRAPH.CENTER)
        set_cell(table.cell(row_index, 7), row_data.get("result"), align=WD_ALIGN_PARAGRAPH.CENTER)
    elif kind == "transition":
        set_cell(table.cell(row_index, 0), row_data.get("marker"), align=WD_ALIGN_PARAGRAPH.CENTER)
        set_cell(
            table.cell(row_index, 1),
            join_nonempty(row_data.get("workLocation"), row_data.get("equipmentName"), row_data.get("referencePoint")),
        )
        set_cell(table.cell(row_index, 3), row_data.get("conductorSpec"))
        set_cell(table.cell(row_index, 4), row_data.get("protectionZone"), align=WD_ALIGN_PARAGRAPH.CENTER)
        set_cell(table.cell(row_index, 5), row_data.get("standardValue"), align=WD_ALIGN_PARAGRAPH.CENTER)
        set_cell(table.cell(row_index, 6), row_data.get("measuredValue"), align=WD_ALIGN_PARAGRAPH.CENTER)
        set_cell(table.cell(row_index, 7), row_data.get("result"), align=WD_ALIGN_PARAGRAPH.CENTER)
    elif kind == "spd":
        set_cell(table.cell(row_index, 0), row_data.get("marker"), align=WD_ALIGN_PARAGRAPH.CENTER)
        set_cell(table.cell(row_index, 1), row_data.get("spdModel"))
        set_cell(table.cell(row_index, 2), row_data.get("installLocation"))
        set_cell(table.cell(row_index, 3), row_data.get("wireLength"), align=WD_ALIGN_PARAGRAPH.CENTER)
        set_cell(table.cell(row_index, 4), row_data.get("spdLevel"), align=WD_ALIGN_PARAGRAPH.CENTER)
        set_cell(table.cell(row_index, 5), row_data.get("installQuantity"), align=WD_ALIGN_PARAGRAPH.CENTER)
        set_cell(table.cell(row_index, 6), row_data.get("measuredValue"), align=WD_ALIGN_PARAGRAPH.CENTER)
        set_cell(table.cell(row_index, 7), row_data.get("result"), align=WD_ALIGN_PARAGRAPH.CENTER)


def trim_measurement_template_rows(table, data_row_count):
    first_data_row = 2
    remark_row_index = len(table.rows) - 1
    keep_until = first_data_row + data_row_count
    for row_index in range(remark_row_index - 1, keep_until - 1, -1):
        table._tbl.remove(table.rows[row_index]._tr)
    stretch_template_remark_row(table, data_row_count)


def prepare_measurement_template_rows(table):
    for row_index in range(2, len(table.rows) - 1):
        clear_measurement_data_row(table, row_index)
    normalize_measurement_remark_row(table)


def normalize_measurement_remark_row(table):
    row = table.rows[-1]
    row.height = Cm(MEASUREMENT_REMARK_HEIGHT_CM)
    row.height_rule = WD_ROW_HEIGHT_RULE.EXACTLY
    for cell in unique_row_cells(row):
        cell.vertical_alignment = WD_ALIGN_VERTICAL.TOP
        set_cell(cell, "备注：", bold=True, align=WD_ALIGN_PARAGRAPH.LEFT, empty_text="")


def clear_measurement_data_row(table, row_index):
    for cell in unique_row_cells(table.rows[row_index]):
        set_cell(cell, None, align=WD_ALIGN_PARAGRAPH.CENTER)


def unique_row_cells(row):
    seen = set()
    cells = []
    for cell in row.cells:
        cell_id = id(cell._tc)
        if cell_id in seen:
            continue
        seen.add(cell_id)
        cells.append(cell)
    return cells


def join_nonempty(*items):
    return "，".join(str(item) for item in items if item is not None and str(item) != "")


def legend_content_page_count(legend):
    if not legend:
        return 0
    images = legend_image_sources(legend)
    if images:
        return len(images)
    canvases = extract_legend_canvases(legend)
    if canvases:
        return len(canvases)
    return 1 if legend.get("testPoints") else 0


def add_legend_tables(document, legend):
    rendered_images = render_legend_images(legend)
    if rendered_images:
        for index, image in enumerate(rendered_images):
            if index:
                document.add_page_break()
            add_legend_image(document, image)
        return

    test_points = legend.get("testPoints", [])
    if test_points:
        add_subheading(document, "检测点位")
        rows = []
        for item in test_points:
            fields = item.get("reportFields", {})
            rows.append(
                {
                    "label": item.get("label"),
                    "reportType": item.get("reportType"),
                    "equipmentName": fields.get("equipmentName"),
                    "workLocation": fields.get("workLocation"),
                    "standardValue": fields.get("standardValue"),
                    "measuredValue": fields.get("measuredValue"),
                    "result": fields.get("result"),
                }
            )
        add_measurement_table(
            document,
            rows,
            [
                ("label", "点位编号"),
                ("reportType", "检测类型"),
                ("equipmentName", "设备名称"),
                ("workLocation", "位置"),
                ("standardValue", "标准值"),
                ("measuredValue", "实测值"),
                ("result", "结论"),
            ],
        )


def render_legend_images(legend):
    images = []
    for source in legend_image_sources(legend):
        image = load_legend_image_source(source)
        if image:
            images.append(image)
    if images:
        return images

    for canvas in extract_legend_canvases(legend):
        svg_text = build_legend_svg(canvas)
        if not svg_text:
            continue
        width, height = legend_canvas_dimensions(canvas)
        image = svg_text_to_png_image(svg_text, width, height)
        if image:
            images.append(image)
            continue
        images.append(
            {
                "stream": BytesIO(svg_text.encode("utf-8")),
                "format": "svg",
                "width": width,
                "height": height,
            }
        )
    return images


def legend_image_sources(legend):
    sources = []
    for key in (
        "image",
        "imageData",
        "imageDataUrl",
        "imageBase64",
        "png",
        "pngData",
        "pngDataUrl",
        "screenshot",
        "previewImage",
    ):
        source = legend.get(key)
        if source:
            sources.append(source)
    return sources


def load_legend_image_source(source):
    if not isinstance(source, str):
        return None
    text = source.strip()
    if not text:
        return None

    if text.startswith("data:"):
        header, payload = text.split(",", 1)
        mime_type = header.split(";", 1)[0][5:].lower()
        if ";base64" in header:
            raw = b64decode(payload)
        else:
            raw = unquote(payload).encode("utf-8")
        image_format = "svg" if mime_type == "image/svg+xml" else "raster"
        return {"stream": BytesIO(raw), "format": image_format, "width": 900, "height": 600}

    if text.lstrip().startswith("<svg"):
        return {"stream": BytesIO(text.encode("utf-8")), "format": "svg", "width": 900, "height": 600}

    possible_path = Path(text)
    if possible_path.exists() and possible_path.is_file():
        raw = possible_path.read_bytes()
        image_format = "svg" if possible_path.suffix.lower() == ".svg" else "raster"
        return {"stream": BytesIO(raw), "format": image_format, "width": 900, "height": 600}

    try:
        raw = b64decode(text)
    except Exception:
        return None
    return {"stream": BytesIO(raw), "format": "raster", "width": 900, "height": 600}


def extract_legend_canvases(legend):
    workspace = normalize_legend_workspace(legend)
    if not workspace:
        return []

    if workspace.get("boardWidth") and workspace.get("boardHeight"):
        return [workspace]

    if has_canvas_content(workspace):
        return [workspace]

    tab_data = workspace.get("tabData") or {}
    if not isinstance(tab_data, dict) or not tab_data:
        return []

    if workspace.get("exportAllTabs"):
        return [canvas for canvas in tab_data.values() if isinstance(canvas, dict) and has_canvas_content(canvas)]

    active_tab_id = workspace.get("activeTabId")
    if active_tab_id is not None:
        canvas = tab_data.get(str(active_tab_id))
        if isinstance(canvas, dict) and has_canvas_content(canvas):
            return [canvas]

    for canvas in tab_data.values():
        if isinstance(canvas, dict) and has_canvas_content(canvas):
            return [canvas]
    return []


def has_canvas_content(canvas):
    return bool(
        isinstance(canvas, dict)
        and (
            canvas.get("importedBoard")
            or canvas.get("blocks")
            or canvas.get("paths")
            or canvas.get("texts")
            or canvas.get("testPoints")
        )
    )


def normalize_legend_workspace(legend):
    if not isinstance(legend, dict):
        return None
    for key in ("workspace", "drawingWorkspace", "drawingData", "legendWorkspace"):
        nested = legend.get(key)
        if isinstance(nested, dict):
            return nested
    return legend


def build_legend_svg(canvas):
    width, height = legend_canvas_dimensions(canvas)
    view_box = legend_canvas_view_box(canvas)
    if width <= 0 or height <= 0:
        return ""

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:g}" height="{height:g}" viewBox="{view_box}">',
        "<style>",
        "text{font-family:'Microsoft YaHei','SimSun',Arial,sans-serif;fill:#111}",
        ".legend-path{fill:none;stroke:#222;stroke-width:1.2}",
        ".legend-point{fill:#000;stroke:#000;stroke-width:1}",
        "</style>",
        '<rect x="0" y="0" width="100%" height="100%" fill="#fff"/>',
    ]

    imported_board = canvas.get("importedBoard")
    if isinstance(imported_board, dict):
        parts.append(nested_svg_element(imported_board, 0, 0, width, height))

    for path in canvas.get("paths") or []:
        parts.append(path_to_svg(path))

    for index, block in enumerate(canvas.get("blocks") or []):
        parts.append(nested_svg_element(block, block.get("x", 0), block.get("y", 0), block.get("width", 40), block.get("height", 40), block.get("rotation", 0), f"block{index}_"))

    for text in canvas.get("texts") or []:
        parts.append(text_to_svg(text))

    for point in canvas.get("testPoints") or []:
        parts.append(test_point_to_svg(point))

    parts.append("</svg>")
    return "".join(part for part in parts if part)


def legend_canvas_dimensions(canvas):
    if canvas.get("boardWidth") and canvas.get("boardHeight"):
        return float(canvas.get("boardWidth")), float(canvas.get("boardHeight"))
    if canvas.get("width") and canvas.get("height"):
        return float(canvas.get("width")), float(canvas.get("height"))
    min_x, min_y, max_x, max_y = legend_canvas_bounds(canvas)
    return max(max_x - min_x + 40, 1), max(max_y - min_y + 40, 1)


def legend_canvas_view_box(canvas):
    if canvas.get("boardWidth") and canvas.get("boardHeight"):
        return f'0 0 {float(canvas.get("boardWidth")):g} {float(canvas.get("boardHeight")):g}'
    if canvas.get("width") and canvas.get("height"):
        return f'0 0 {float(canvas.get("width")):g} {float(canvas.get("height")):g}'
    min_x, min_y, max_x, max_y = legend_canvas_bounds(canvas)
    return f"{min_x - 20:g} {min_y - 20:g} {max(max_x - min_x + 40, 1):g} {max(max_y - min_y + 40, 1):g}"


def legend_canvas_bounds(canvas):
    xs = []
    ys = []

    def add_rect(x, y, width=0, height=0):
        x = float(x or 0)
        y = float(y or 0)
        width = float(width or 0)
        height = float(height or 0)
        xs.extend([x, x + width])
        ys.extend([y, y + height])

    imported_board = canvas.get("importedBoard")
    if isinstance(imported_board, dict):
        add_rect(0, 0, imported_board.get("width") or canvas.get("boardWidth"), imported_board.get("height") or canvas.get("boardHeight"))

    for block in canvas.get("blocks") or []:
        add_rect(block.get("x"), block.get("y"), block.get("width"), block.get("height"))

    for text in canvas.get("texts") or []:
        add_rect(text.get("x"), text.get("y"), text.get("width"), text.get("height"))

    for point in canvas.get("testPoints") or []:
        x = float(point.get("x") or 0)
        y = float(point.get("y") or 0)
        xs.extend([x - 12, x + 35])
        ys.extend([y - 18, y + 22])

    for path in canvas.get("paths") or []:
        for point in path.get("points") or []:
            xs.append(float(point.get("x") or 0))
            ys.append(float(point.get("y") or 0))

    if not xs or not ys:
        return 0, 0, 900, 600
    return min(xs), min(ys), max(xs), max(ys)


def nested_svg_element(item, x, y, width, height, rotation=0, prefix="nested_"):
    raw_svg = item.get("svg")
    if not raw_svg:
        return ""
    x = float(x or 0)
    y = float(y or 0)
    width = float(width or item.get("width") or 40)
    height = float(height or item.get("height") or 40)
    rotation = float(rotation or 0)
    transform = f"translate({x:g} {y:g})"
    if rotation:
        transform += f" rotate({rotation:g} {width / 2:g} {height / 2:g})"
    return f'<g transform="{transform}">{place_svg_at_size(namespace_svg_ids(raw_svg, prefix), width, height)}</g>'


def namespace_svg_ids(raw_svg, prefix):
    svg = raw_svg
    ids = re.findall(r'\bid="([^"]+)"', svg)
    for element_id in ids:
        safe_id = prefix + re.sub(r"[^A-Za-z0-9_.:-]", "_", element_id)
        svg = svg.replace(f'id="{element_id}"', f'id="{safe_id}"')
        svg = svg.replace(f'url(#{element_id})', f'url(#{safe_id})')
        svg = svg.replace(f'href="#{element_id}"', f'href="#{safe_id}"')
        svg = svg.replace(f'xlink:href="#{element_id}"', f'xlink:href="#{safe_id}"')
    return svg


def place_svg_at_size(raw_svg, width, height):
    svg = raw_svg.strip()
    svg = re.sub(r"^<\?xml[^>]*>\s*", "", svg)
    match = re.match(r"(<svg\b)([^>]*)(>)", svg)
    if not match:
        return svg
    attrs = re.sub(r'\s(?:x|y|width|height)="[^"]*"', "", match.group(2))
    opening = f'{match.group(1)} x="0" y="0" width="{width:g}" height="{height:g}"{attrs}{match.group(3)}'
    return opening + svg[match.end() :]


def path_to_svg(path):
    points = path.get("points") or []
    if not points:
        return ""
    point_text = " ".join(f'{float(point.get("x", 0)):g},{float(point.get("y", 0)):g}' for point in points)
    if path.get("closed"):
        return f'<polygon class="legend-path" points="{point_text}"/>'
    return f'<polyline class="legend-path" points="{point_text}"/>'


def text_to_svg(text):
    content = escape(str(text.get("text") or text.get("name") or ""))
    if not content:
        return ""
    x = float(text.get("x") or 0)
    y = float(text.get("y") or 0)
    size = float(text.get("fontSize") or 14)
    width = float(text.get("width") or 0)
    height = float(text.get("height") or 0)
    orientation = text.get("orientation")
    attrs = f'x="{x:g}" y="{y + size:g}" font-size="{size:g}"'
    if orientation == "vertical":
        cx = x + width / 2
        cy = y + height / 2
        attrs += f' transform="rotate(90 {cx:g} {cy:g})"'
    return f"<text {attrs}>{content}</text>"


def test_point_to_svg(point):
    x = float(point.get("x") or 0)
    y = float(point.get("y") or 0)
    size = max(float(point.get("size") or 1), 1) * 5
    label = escape(str(point.get("label") or ""))
    side = point.get("side") or "right"
    rotations = {"top": 0, "right": 90, "bottom": 180, "left": 270}
    rotation = rotations.get(side, 90)
    label_dx = 7 if side != "left" else -22
    label_dy = -7 if side in {"top", "right"} else 14
    return (
        f'<g transform="translate({x:g} {y:g}) rotate({rotation:g})">'
        f'<polygon class="legend-point" points="0,{-size:g} {size:g},{size:g} {-size:g},{size:g}"/>'
        "</g>"
        f'<text x="{x + label_dx:g}" y="{y + label_dy:g}" font-size="8">{label}</text>'
    )


def svg_text_to_png_image(svg_text, width, height):
    try:
        from reportlab.graphics import renderPM
        from svglib.svglib import svg2rlg
    except ImportError:
        return None

    try:
        drawing = svg2rlg(BytesIO(svg_text.encode("utf-8")))
        if drawing is None:
            return None
        png_bytes = renderPM.drawToString(drawing, fmt="PNG")
    except Exception:
        return None
    return {
        "stream": BytesIO(png_bytes),
        "format": "raster",
        "width": width,
        "height": height,
    }


def add_legend_image(document, image):
    image_stream = image["stream"]
    width = image.get("width") or 900
    height = image.get("height") or 600
    image_stream.seek(0)
    paragraph = document.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph.paragraph_format.space_before = Pt(8)
    paragraph.paragraph_format.space_after = Pt(0)

    display_width = LEGEND_IMAGE_MAX_WIDTH_CM
    display_height = display_width * float(height or 1) / float(width or 1)
    if display_height > LEGEND_IMAGE_MAX_HEIGHT_CM:
        display_height = LEGEND_IMAGE_MAX_HEIGHT_CM
        display_width = display_height * float(width or 1) / float(height or 1)

    run = paragraph.add_run()
    if image.get("format") == "svg":
        add_svg_picture(document, run, image_stream.getvalue(), display_width, display_height)
    else:
        run.add_picture(image_stream, width=Cm(display_width), height=Cm(display_height))


def add_svg_picture(document, run, svg_bytes, width_cm, height_cm):
    package = document.part.package
    partname = package.next_partname("/word/media/legend%d.svg")
    image_part = Part(partname, "image/svg+xml", svg_bytes, package)
    relationship_id = document.part.relate_to(image_part, RT.IMAGE)
    shape_id = document.part.next_id
    inline = CT_Inline.new_pic_inline(shape_id, relationship_id, partname.filename, Cm(width_cm), Cm(height_cm))
    run._r.add_drawing(inline)


def add_heading(document, text, template_heading=None, break_before=True):
    if break_before:
        document.add_page_break()
    if template_heading is not None:
        paragraph_element = deepcopy(template_heading)
        document._element.body.insert(len(document._element.body) - 1, paragraph_element)
        paragraph = document.paragraphs[-1]
        replace_paragraph_text_keep_style(paragraph, text)
    else:
        paragraph = document.add_paragraph(style="Heading 1")
        replace_paragraph_text_keep_style(paragraph, text)


def add_subheading(document, text):
    paragraph = document.add_paragraph()
    paragraph.paragraph_format.space_before = Pt(6)
    paragraph.paragraph_format.space_after = Pt(3)
    run = paragraph.add_run(text)
    set_run_font(run, size=10.5, bold=True)


def add_spacing(document, points):
    paragraph = document.add_paragraph()
    paragraph.paragraph_format.space_after = Pt(points)


def replace_paragraph_text_keep_style(paragraph, text):
    template_rpr = first_paragraph_run_properties(paragraph)
    for child in list(paragraph._p):
        if child.tag == qn("w:r"):
            paragraph._p.remove(child)
    run = paragraph.add_run(text)
    if template_rpr is not None:
        run._r.insert(0, deepcopy(template_rpr))
        set_run_font(run, size=run.font.size.pt if run.font.size else None, bold=bool(run.bold) if run.bold is not None else None)
    else:
        set_run_font(run, size=None, bold=None)


def first_paragraph_run_properties(paragraph):
    for run in paragraph.runs:
        if run._r.rPr is not None:
            return deepcopy(run._r.rPr)
    return None


def style_table(table):
    try:
        table.style = "Table Grid"
    except KeyError:
        pass
    set_table_borders(table)
    for row in table.rows:
        for cell in row.cells:
            cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
            set_cell_margins(cell, top=20, start=45, bottom=20, end=45)


def set_cell(
    cell,
    text,
    bold=False,
    align=WD_ALIGN_PARAGRAPH.LEFT,
    size=None,
    line_spacing=1,
    empty_text=EMPTY,
):
    template_rpr = first_cell_run_properties(cell)
    is_empty = text is None or text == ""
    rendered_text = empty_text if is_empty else normalize_dash_text(str(text))
    if rendered_text == EMPTY:
        align = WD_ALIGN_PARAGRAPH.CENTER
    cell.text = ""
    lines = rendered_text.splitlines() or [""]
    for index, line in enumerate(lines):
        paragraph = cell.paragraphs[0] if index == 0 else cell.add_paragraph()
        paragraph.alignment = align
        paragraph.paragraph_format.space_before = Pt(0)
        paragraph.paragraph_format.space_after = Pt(0)
        paragraph.paragraph_format.line_spacing = line_spacing
        run = paragraph.add_run(line)
        if template_rpr is not None and size is None:
            run._r.insert(0, deepcopy(template_rpr))
        set_run_font(run, size=size, bold=bold)


def first_cell_run_properties(cell):
    for paragraph in cell.paragraphs:
        for run in paragraph.runs:
            if run._r.rPr is not None:
                return deepcopy(run._r.rPr)
    return None


def normalize_dash_text(text):
    return EMPTY if text.strip() in {"-", "－", "–", "—"} else text


def set_sub_cell(cell, text, bold=False, align=WD_ALIGN_PARAGRAPH.LEFT):
    set_cell(cell, text, bold=bold, align=align, size=COMPACT_TABLE_FONT_SIZE, line_spacing=0.86)


def merge_write(table, top, left, bottom, right, text, bold=False, align=WD_ALIGN_PARAGRAPH.LEFT, size=None):
    cell = table.cell(top, left).merge(table.cell(bottom, right))
    set_cell(cell, text, bold=bold, align=align, size=size, line_spacing=0.86 if size else 1)
    return cell


def set_run_font(run, size=9, bold=False):
    if bold is not None:
        run.bold = bold
    run.font.name = FONT_NAME
    run._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:ascii"), FONT_NAME)
    run._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:hAnsi"), FONT_NAME)
    run._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), FONT_NAME)
    if size is not None:
        run.font.size = Pt(size)


def set_table_borders(table):
    borders = table._tbl.tblPr.first_child_found_in("w:tblBorders")
    if borders is None:
        borders = OxmlElement("w:tblBorders")
        table._tbl.tblPr.append(borders)
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        element = borders.find(qn(f"w:{edge}"))
        if element is None:
            element = OxmlElement(f"w:{edge}")
            borders.append(element)
        element.set(qn("w:val"), "single")
        element.set(qn("w:sz"), "4")
        element.set(qn("w:space"), "0")
        element.set(qn("w:color"), "000000")


def set_cell_margins(cell, top=0, start=0, bottom=0, end=0):
    tc_pr = cell._tc.get_or_add_tcPr()
    tc_mar = tc_pr.first_child_found_in("w:tcMar")
    if tc_mar is None:
        tc_mar = OxmlElement("w:tcMar")
        tc_pr.append(tc_mar)
    for margin_name, margin_value in (("top", top), ("start", start), ("bottom", bottom), ("end", end)):
        node = tc_mar.find(qn(f"w:{margin_name}"))
        if node is None:
            node = OxmlElement(f"w:{margin_name}")
            tc_mar.append(node)
        node.set(qn("w:w"), str(margin_value))
        node.set(qn("w:type"), "dxa")


def set_table_grid(table, widths):
    tbl = table._tbl
    tbl_grid = tbl.tblGrid
    if tbl_grid is None:
        tbl_grid = OxmlElement("w:tblGrid")
        tbl.insert(0, tbl_grid)
    for child in list(tbl_grid):
        tbl_grid.remove(child)
    for width in widths:
        grid_col = OxmlElement("w:gridCol")
        grid_col.set(qn("w:w"), str(width))
        tbl_grid.append(grid_col)
    for row in table.rows:
        for index, cell in enumerate(row.cells):
            tc_w = cell._tc.get_or_add_tcPr().first_child_found_in("w:tcW")
            if tc_w is None:
                tc_w = OxmlElement("w:tcW")
                cell._tc.get_or_add_tcPr().append(tc_w)
            tc_w.set(qn("w:w"), str(widths[min(index, len(widths) - 1)]))
            tc_w.set(qn("w:type"), "dxa")


def value(raw):
    if raw is None or raw == "":
        return EMPTY
    return str(raw)


if __name__ == "__main__":
    main()
