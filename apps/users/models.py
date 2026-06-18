from django.db import models
from django.utils import timezone
from generate_report.utils.models import BaseModel


class UserInfo(BaseModel):
    username = models.CharField(unique=True, max_length=32, verbose_name="用户名")
    password = models.CharField(max_length=32, verbose_name="密码")
    gender = models.IntegerField(verbose_name="gender", default=1)
    # permission_id = models.IntegerField(choices=,verbose_name="权限" )
    class Meta:
        # 表名
        db_table = 'tb_user_info'
        verbose_name = '用户'
