from django.db import models
from django.utils import timezone
from generate_report.utils.models import BaseModel


class UserInfo(BaseModel):
    username = models.CharField(unique=True, max_length=32, verbose_name="用户名")
    # Django's password hashes are deliberately much longer than the old
    # plaintext field.  Existing plaintext records are upgraded on login.
    password = models.CharField(max_length=128, verbose_name="密码哈希")
    is_admin = models.BooleanField(default=False, verbose_name="是否管理员")
    gender = models.IntegerField(verbose_name="gender", default=1)
    # permission_id = models.IntegerField(choices=,verbose_name="权限" )
    class Meta:
        # 表名
        db_table = 'tb_user_info'
        verbose_name = '用户'

    # ``UserInfo`` predates Django's built-in authentication model.  DRF's
    # IsAuthenticated permission still expects these standard attributes on
    # the authenticated object returned by TokenAuthenticate.
    @property
    def is_authenticated(self):
        return not self.is_delete

    @property
    def is_anonymous(self):
        return False

    @property
    def is_active(self):
        return not self.is_delete
