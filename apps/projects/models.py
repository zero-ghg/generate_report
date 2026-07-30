from django.db import models

from apps.users.models import UserInfo
from generate_report.utils.models import BaseModel


class Project(BaseModel):
    owner = models.ForeignKey(UserInfo, on_delete=models.CASCADE, related_name="projects", verbose_name="所属用户")
    name = models.CharField(max_length=120, verbose_name="项目名称")
    description = models.CharField(max_length=500, blank=True, default="", verbose_name="项目说明")
    data_file = models.CharField(max_length=500, blank=True, default="", verbose_name="工作区 JSON 文件")

    class Meta:
        db_table = "tb_project"
        ordering = ["-update_time", "-id"]
        verbose_name = "项目"
