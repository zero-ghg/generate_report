from django.db import migrations


def ensure_initial_admin(apps, schema_editor):
    UserInfo = apps.get_model("users", "UserInfo")
    if UserInfo.objects.filter(is_delete=False, is_admin=True).exists():
        return
    first_user = UserInfo.objects.filter(is_delete=False).order_by("id").first()
    if first_user:
        first_user.is_admin = True
        first_user.save(update_fields=["is_admin"])


class Migration(migrations.Migration):
    dependencies = [("users", "0003_userinfo_is_admin_and_password_hash")]
    operations = [migrations.RunPython(ensure_initial_admin, migrations.RunPython.noop)]
