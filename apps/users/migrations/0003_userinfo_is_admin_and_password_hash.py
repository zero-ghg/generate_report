from django.contrib.auth.hashers import make_password
from django.db import migrations, models


def create_initial_admin(apps, schema_editor):
    UserInfo = apps.get_model("users", "UserInfo")
    if not UserInfo.objects.filter(is_delete=False).exists():
        UserInfo.objects.create(
            username="admin",
            password=make_password("admin123"),
            is_admin=True,
        )


class Migration(migrations.Migration):
    dependencies = [("users", "0002_userinfo_update_time_alter_userinfo_create_time_and_more")]

    operations = [
        migrations.AlterField(
            model_name="userinfo",
            name="password",
            field=models.CharField(max_length=128, verbose_name="密码哈希"),
        ),
        migrations.AddField(
            model_name="userinfo",
            name="is_admin",
            field=models.BooleanField(default=False, verbose_name="是否管理员"),
        ),
        migrations.RunPython(create_initial_admin, migrations.RunPython.noop),
    ]
