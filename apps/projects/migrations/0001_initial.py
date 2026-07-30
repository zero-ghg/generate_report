import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = [("users", "0003_userinfo_is_admin_and_password_hash")]

    operations = [
        migrations.CreateModel(
            name="Project",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("create_time", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("update_time", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
                ("is_delete", models.BooleanField(default=False, verbose_name="逻辑删除")),
                ("name", models.CharField(max_length=120, verbose_name="项目名称")),
                ("description", models.CharField(blank=True, default="", max_length=500, verbose_name="项目说明")),
                ("data_file", models.CharField(blank=True, default="", max_length=500, verbose_name="工作区 JSON 文件")),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="projects", to="users.userinfo", verbose_name="所属用户")),
            ],
            options={"verbose_name": "项目", "ordering": ["-update_time", "-id"], "db_table": "tb_project"},
        ),
    ]
