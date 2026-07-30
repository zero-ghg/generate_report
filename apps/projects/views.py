import json
import uuid
from pathlib import Path

from django.conf import settings
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.projects.models import Project
from generate_report.utils.auth import TokenAuthenticate


def project_payload(project, include_data=False):
    data = {
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "owner_id": project.owner_id,
        "create_time": project.create_time,
        "update_time": project.update_time,
    }
    if include_data:
        data["workspace"] = read_project_workspace(project)
    return data


def storage_path(project):
    return Path(settings.MEDIA_ROOT) / "projects" / f"user_{project.owner_id}" / f"project_{project.id}.json"


def read_project_workspace(project):
    path = Path(project.data_file) if project.data_file else storage_path(project)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_project_workspace(project, workspace):
    if workspace is None:
        return
    path = storage_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(workspace, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temporary.replace(path)
    project.data_file = str(path)
    project.save(update_fields=["data_file", "update_time"])


class ProjectAccessMixin:
    authentication_classes = [TokenAuthenticate]
    permission_classes = [IsAuthenticated]

    def get_project(self, request, project_id):
        project = Project.objects.filter(id=project_id, is_delete=False).first()
        if not project:
            raise NotFound("项目不存在")
        if project.owner_id != request.user.id and not request.user.is_admin:
            raise PermissionDenied("无权访问该项目")
        return project


class ProjectListView(ProjectAccessMixin, APIView):
    def get(self, request):
        include_data = str(request.query_params.get("include_data", "")).lower() in {"1", "true", "yes"}
        projects = Project.objects.filter(owner=request.user, is_delete=False)
        return Response({"code": 200, "data": [project_payload(project, include_data) for project in projects]})

    def post(self, request):
        name = str(request.data.get("name") or "").strip()
        if not name:
            raise ValidationError("请输入项目名称")
        if len(name) > 120:
            raise ValidationError("项目名称不能超过 120 个字符")
        project = Project.objects.create(
            owner=request.user,
            name=name,
            description=str(request.data.get("description") or "").strip(),
        )
        workspace = request.data.get("workspace")
        if workspace is not None:
            write_project_workspace(project, workspace)
        return Response({"code": 201, "data": project_payload(project, True), "msg": "项目已创建"}, status=status.HTTP_201_CREATED)


class ProjectDetailView(ProjectAccessMixin, APIView):
    def get(self, request, project_id):
        return Response({"code": 200, "data": project_payload(self.get_project(request, project_id), True)})

    def put(self, request, project_id):
        project = self.get_project(request, project_id)
        if "name" in request.data:
            name = str(request.data.get("name") or "").strip()
            if not name:
                raise ValidationError("项目名称不能为空")
            project.name = name[:120]
        if "description" in request.data:
            project.description = str(request.data.get("description") or "").strip()[:500]
        project.save(update_fields=["name", "description", "update_time"])
        if "workspace" in request.data:
            write_project_workspace(project, request.data.get("workspace"))
        return Response({"code": 200, "data": project_payload(project, True), "msg": "项目已保存"})

    def delete(self, request, project_id):
        project = self.get_project(request, project_id)
        project.is_delete = True
        project.save(update_fields=["is_delete", "update_time"])
        path = Path(project.data_file) if project.data_file else storage_path(project)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return Response({"code": 200, "msg": "项目已删除"})
