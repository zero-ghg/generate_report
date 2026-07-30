from rest_framework import status
from rest_framework.views import exception_handler
from rest_framework.response import Response
# TODO: 修改包名
from generate_report.settings.status_code import StatusCode


def custom_exception_handler(exc, context):
    response = exception_handler(exc, context)

    # DRF returns None for exceptions it does not own.  Preserve that
    # behaviour instead of masking the original exception with a second
    # AttributeError while formatting the error response.
    if response is None:
        return None

    if response.data is not None:
        # 初始的错误信息
        custom_response_data = {
            'code': response.status_code,  # 默认错误代码
            'message': "",  # 初始化为空字符串
            'data': {}
        }
        # 序列化器的异常
        # 检查是否有非字段错误
        if 'non_field_errors' in response.data:
            error_message = '发生错误'
            error_code = response.status_code
            non_field_errors = response.data.get('non_field_errors', [])
            if non_field_errors:
                # 假设第一个错误包含我们需要的信息
                error_detail = non_field_errors[0]
                if isinstance(error_detail, dict):
                    # 提取错误信息，如果存在
                    error_message = error_detail.get('message', error_message)
                    error_code = error_detail.get('code', error_code)
            # 构造自定义响应数据
            custom_response_data = {
                'code': error_code,
                'message': error_message,
                'data': {}
            }
        else:
            if response.status_code == 400:
                error_messages = []
                for field, messages in response.data.items():
                    error_messages.append(f"{field}: {''.join([str(msg) for msg in messages])}")
                # 用适当的 StatusCode 更新 code
                custom_response_data['code'] = StatusCode.VALIDATION_ERROR_CODE  # 根据需要调整
                # 设置错误消息
                custom_response_data['message'] = "".join(error_messages)
            # 处理资源未找到错误
            elif response.status_code == 404:
                error_messages = []
                for field, messages in response.data.items():
                    error_messages.append(f"{field}: {''.join([str(msg) for msg in messages])}")
                custom_response_data['code'] = StatusCode.NOT_FOUND_CODE
                custom_response_data['message'] = "".join(error_messages)

            # 处理权限错误
            # elif response.status_code == 403:
            #     error_messages = []
            #     for field, messages in response.data.items():
            #         error_messages.append(f"{field}: {''.join([str(msg) for msg in messages])}")
            #     custom_response_data['code'] = StatusCode.PERMISSION_DENIED_CODE
            #     custom_response_data['message'] = "".join(error_messages)

            # 处理认证错误
            elif response.status_code == 401:
                error_messages = []
                for field, messages in response.data.items():
                    error_messages.append(f"{''.join([str(msg) for msg in messages])}")
                custom_response_data['code'] = StatusCode.NOT_AUTHENTICATED_CODE
                custom_response_data['message'] = "".join(error_messages)
        return Response(custom_response_data, status=status.HTTP_200_OK)
    return response
