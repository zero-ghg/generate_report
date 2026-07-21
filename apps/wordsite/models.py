from django.db import models

from generate_report.utils.models import BaseModel


# 生成的 Word 文档记录表：用于保存导出报告接口生成的 Word 文件信息和对应的源 JSON 数据。
class GeneratedWordDocument(BaseModel):
    """存储生成的 Word 报告文件。"""

    STATUS_CHOICES = (
        ("success", "成功"),
        ("failed", "失败"),
    )

    project_name = models.CharField(max_length=255, blank=True, default="", verbose_name="项目名称")
    report_no = models.CharField(max_length=128, blank=True, default="", verbose_name="报告编号")
    original_filename = models.CharField(max_length=255, blank=True, default="", verbose_name="原始文件名")
    filename = models.CharField(max_length=255, verbose_name="保存文件名")
    relative_path = models.CharField(max_length=500, verbose_name="相对文件路径")
    file_url = models.CharField(max_length=1000, blank=True, default="", verbose_name="文件访问地址")
    file_size = models.BigIntegerField(default=0, verbose_name="文件大小")
    source_json = models.JSONField(null=True, blank=True, verbose_name="源 JSON 数据")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="success", verbose_name="生成状态")
    error_message = models.TextField(blank=True, default="", verbose_name="错误信息")

    class Meta:
        db_table = "tb_generated_word_document"
        verbose_name = "生成的 Word 文档"
        verbose_name_plural = "生成的 Word 文档"
        ordering = ("-create_time",)

    def __str__(self):
        return self.filename


# 解析出的 JSON 文件记录表：用于保存导入 Word 后解析出来的 JSON 文件信息和解析结果。
class ParsedReportJsonFile(BaseModel):
    """存储 Word 报告解析出来的 JSON 文件。"""

    STATUS_CHOICES = (
        ("success", "成功"),
        ("failed", "失败"),
    )

    project_name = models.CharField(max_length=255, blank=True, default="", verbose_name="项目名称")
    report_no = models.CharField(max_length=128, blank=True, default="", verbose_name="报告编号")
    source_docx_name = models.CharField(max_length=255, blank=True, default="", verbose_name="来源 Word 文件名")
    filename = models.CharField(max_length=255, verbose_name="保存文件名")
    relative_path = models.CharField(max_length=500, verbose_name="相对文件路径")
    file_url = models.CharField(max_length=1000, blank=True, default="", verbose_name="文件访问地址")
    file_size = models.BigIntegerField(default=0, verbose_name="文件大小")
    parsed_json = models.JSONField(null=True, blank=True, verbose_name="解析后的 JSON 数据")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="success", verbose_name="解析状态")
    error_message = models.TextField(blank=True, default="", verbose_name="错误信息")

    class Meta:
        db_table = "tb_parsed_report_json_file"
        verbose_name = "解析出的 JSON 文件"
        verbose_name_plural = "解析出的 JSON 文件"
        ordering = ("-create_time",)

    def __str__(self):
        return self.filename
