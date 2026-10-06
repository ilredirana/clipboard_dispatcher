"""保护配置下载 URL 中的授权票据，避免查询参数写入应用日志。"""

import copy
import logging
import re

_PROVISIONING_QUERY = re.compile(r"(/setup/tasker/[^\s?\"']+)\?[^\s\"']+")


class ProvisioningLogFormatter(logging.Formatter):
    """在日志输出接口中删除配置 URL 查询参数，并保留原始日志记录。"""

    def format(self, record: logging.LogRecord) -> str:
        formatted = super().format(copy.copy(record))
        return _PROVISIONING_QUERY.sub(r"\1", formatted)
