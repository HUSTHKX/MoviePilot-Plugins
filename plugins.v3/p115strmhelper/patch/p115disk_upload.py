from pathlib import Path
from typing import Optional, Any, Callable

from app.log import logger
from app.schemas import FileItem

from ..core.config import configer
from ..core.p115disk import P115DiskCore
from ..utils.plugin_bridge import get_p115_api_class


class P115DiskPatcher:
    """
    P115Disk 上传增强补丁
    """

    _original_upload: Optional[Callable[..., Any]] = None
    _patched_class: Optional[Any] = None
    _active: bool = False

    @staticmethod
    def _patch_upload(
        self_instance: Any,
        target_dir: FileItem,
        local_path: Path,
        new_name: Optional[str] = None,
    ) -> Optional[FileItem]:
        """
        使用 P115DiskCore 上传
        """
        client = getattr(self_instance, "client", None)
        if not client:
            return None
        helper = P115DiskCore(client=client)
        logger.debug("【P115Disk】调用补丁接口上传")
        return helper.upload(
            target_dir=target_dir, local_path=local_path, new_name=new_name
        )

    @classmethod
    def enable(cls) -> None:
        """
        应用补丁

        MoviePilot V3 禁止插件间直接 import，这里改为动态解析 P115Api。
        解析不到（未安装 P115Disk 插件）时直接跳过，不影响插件其他功能。
        """
        if not configer.upload_module_enhancement:
            return
        if cls._active:
            return

        p115_api_cls = get_p115_api_class()
        if p115_api_cls is None:
            logger.info(
                "【P115Disk】未检测到 P115Disk 插件，跳过上传接口增强补丁"
            )
            return

        cls._original_upload = p115_api_cls.upload
        p115_api_cls.upload = cls._patch_upload
        cls._patched_class = p115_api_cls
        cls._active = True
        logger.info("【P115Disk】上传接口补丁应用成功")

    @classmethod
    def disable(cls) -> None:
        """
        禁用补丁
        """
        if (
            not cls._active
            or cls._original_upload is None
            or cls._patched_class is None
        ):
            return
        cls._patched_class.upload = cls._original_upload
        cls._original_upload = None
        cls._patched_class = None
        cls._active = False
        logger.info("【P115Disk】上传接口恢复原始状态成功")
