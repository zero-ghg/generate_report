class StatusCode:
    MYSQL_ERROR_CODE = {"code": 1001, "message": "数据库操作失败", "data": {}}
    OLD_PASS_ERROR_CODE = {"code": 1002, "message": "旧密码填写错误", "data": {}}
    USER_ERROR_CODE = {"code": 1003, "message": "用户不存在", "data": {}}
    USERNAME_PASS_ERROR_CODE = {"code": 1004, "message": "用户名或密码错误", "data": {}}
    BUCKET_ERROR_CODE = {"code": 1005, "message": "向存储桶内添加数据失败！", "data": {}}
    PHONE_ERROR_CODE = {"code": 1006, "message": "手机号格式不正确！", "data": {}}
    EMAIL_ERROR_CODE = {"code": 1007, "message": "邮箱格式不正确！", "data": {}}
    FILE_NOTFOUND_CODE = {"code": 1009, "message": "文件未找到！", "data": {}}
    FILE_NAME_NOTFOUND_CODE = {"code": 1009, "message": "没有找到对应file_id或file_name！", "data": {}}

    VALIDATION_ERROR_CODE = 1010  # 处理验证错误
    NOT_AUTHENTICATED_CODE = 1011  # 处理认证错误
    UNKNOWN_ERROR_CODE = 1012  # 未知错误
    PERMISSION_DENIED_CODE = 1013  # 处理权限错误
    NOT_FOUND_CODE = 1014  # 处理资源未找到错误

    TEACHER_NOTFOUND_CODE = {"code": 1009, "message": "未找到符合条件的教师！", "data": {}}
    STUDENT_NOTFOUND_CODE = {"code": 1009, "message": "未找到符合条件的学生！", "data": {}}
    CLASS_NOTFOUND_CODE = {"code": 1009, "message": "未找到符合条件的班级！", "data": {}}
    ROLE_NOTFOUND_CODE = {"code": 1009, "message": "未找到符合条件的角色！", "data": {}}
    USER_NOTFOUND_CODE = {"code": 1009, "message": "未找到符合条件的用户！", "data": {}}
    EXAM_NOTFOUND_CODE = {"code": 1009, "message": "未找到符合条件的试卷！", "data": {}}
    FILE_NAME_ERROR_CODE = {"code": 1015, "message": "所上传文件名缺少文件类型后缀！", "data": {}}
    FILE_TYPE_ERROR_CODE = {"code": 1016, "message": "所上传文件名的文件类型错误！", "data": {}}

    VERIFY_ERROR_CODE = {"code": 1017, "message": "验证码不正确！", "data": {}}
    CAPTCHA_NOT_FOUND_CODE = {"code": 1018, "message": "验证码找不到！", "data": {}}
    EXCEL_IMPORT_ERROR_CODE = 1019
    ROLE_FORBID_UPDATE_CODE = {"code": 1020, "message": "修改教师或学生角色的数据是不允许的！", "data": {}}
    ROLE_FORBID_DELETE_CODE = {"code": 1021, "message": "删除教师或学生角色的数据是不允许的！", "data": {}}

    JUDGE_TOPIC_NOTFOUND_CODE = {"code": 1022, "message": "未找到符合条件的判断题！", "data": {}}
    SINGLE_CHOICE_TOPIC_NOTFOUND_CODE = {"code": 1022, "message": "未找到符合条件的单选题！", "data": {}}
    FIELD_EMPTY_CODE = 1023  # 硬件信息获取,字段为空缺失

    CHAT_TOKEN_ERROR_CODE = 1023
    TOKEN_DECODE_ERROR_CODE = 1024
    NOT_STUDENT_CODE = 1025

    CREATE_CODE = 200  # 创建成功/增加成功
    PUT_CODE = 200  # 修改成功
    DELETE_CODE = 200  # 删除成功
