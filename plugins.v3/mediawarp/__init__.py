import os
import platform
import tarfile
import tempfile
import shutil
from typing import Any, List, Dict, Tuple
from pathlib import Path
from datetime import datetime, timedelta

import pytz
import psutil
import requests
from ruamel.yaml import YAML
from ruamel.yaml.representer import RoundTripRepresenter
from apscheduler.schedulers.background import BackgroundScheduler

# --- MoviePilot V3 兼容导入 ---
# V2 写法：
#   from app.core.config import settings
#   from app.helper.mediaserver import MediaServerHelper
#   from app.log import logger
#   from app.plugins import _PluginBase
# V3 把上述路径挪到了 app.sdk.*，但 app.sdk 是"稳定层"，V2/V3 都不会变。
from app.sdk.config import settings
from app.sdk.services import MediaServerHelper
from app.sdk.logging import logger
from app.sdk.plugin import _PluginBase

from .version import VERSION


# ==================== MediaWarp 上游二进制管理 ====================
# 下载源：上游官方仓库（原 Akimio521，作者改名后为 AkimioJR）
# 注意：DDSRem-Dev/MediaWarp 是早期 fork，已停在 v0.1.12，配置 schema 与上游
#       0.2.x 完全不同，因此这里统一切换到上游。
MEDIAWARP_REPO = "AkimioJR/MediaWarp"

# 默认跟随的版本。填 None 表示启动时查询 GitHub API 取最新正式版（更省心但依赖网络）。
DEFAULT_MEDIAWARP_VERSION = "v0.2.5"

# 上游发布资产的命名模板：MediaWarp_v0.2.5_linux_amd64.tar.gz
# 注意版本号带 "v" 前缀 —— 与 DDSRem 旧版（无 v）不同。
MEDIAWARP_ASSET_TEMPLATE = (
    "https://github.com/" + MEDIAWARP_REPO + "/releases/download/{tag}/"
    "MediaWarp_{tag}_{os}_{arch}.tar.gz"
)


class MediaWarp(_PluginBase):
    """
    Emby/Jellyfin 中间件：优化 Strm 播放、自定义前端样式、允许访问客户端控制、脚本注入
    """

    # 插件名称
    plugin_name = "MediaWarp"
    # 插件描述
    plugin_desc = "EmbyServer/Jellyfin 中间件：优化播放 Strm 文件、自定义前端样式、自定义允许访问客户端、嵌入脚本。"
    # 插件图标
    plugin_icon = "https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/refs/heads/main/icons/cloud.png"
    # 插件版本
    plugin_version = VERSION
    # 插件作者
    plugin_author = "DDSRem"
    # 作者主页
    author_url = "https://github.com/DDSRem"
    # 插件配置项ID前缀
    plugin_config_prefix = "mediawarp_"
    # 加载顺序
    plugin_order = 15
    # 可使用的用户级别
    auth_level = 1

    _mediaserver_helper = None
    _mediaserver = None
    _mediaservers = None
    _emby_server = None
    _emby_host = None
    _emby_apikey = None
    # 私有属性
    _scheduler = None
    process = None
    _enabled = False
    _port = None
    _media_strm_path = None
    _crx = False
    _actor_plus = False
    _fanart_show = False
    _external_player_url = False
    _danmaku = False
    _video_together = False
    _srt2ass = False

    def __init__(self):
        """
        初始化
        """
        super().__init__()
        # 类名小写
        class_name = self.__class__.__name__.lower()
        # 插件数据根目录
        self.__data_dir = settings.PLUGIN_DATA_PATH / class_name
        # 二级制文件路径
        self.__mediawarp_path = self.__data_dir / "MediaWarp"
        # 配置文件路径
        self.__config_path = self.__data_dir / "config"
        # 日志路径
        self.__logs_dir = self.__data_dir / "logs"
        # 配置文件名
        self.__config_filename = "config.yaml"
        # 二进制文件版本：默认跟随上游最新稳定版
        self.__mediawarp_version = DEFAULT_MEDIAWARP_VERSION
        self.__mediawarp_version_path = self.__data_dir / "version.txt"

    def init_plugin(self, config: dict = None):
        """
        初始化插件：读取配置，获取媒体服务器信息，启动代理服务

        :param config: 插件配置字典，包含 enabled、port、mediaservers 等
        """
        self._mediaserver_helper = MediaServerHelper()
        self._mediaserver = None

        if config:
            self._enabled = config.get("enabled")
            self._port = config.get("port")
            self._media_strm_path = config.get("media_strm_path")
            self._mediaservers = config.get("mediaservers") or []
            self._crx = config.get("crx")
            self._actor_plus = config.get("actor_plus")
            self._fanart_show = config.get("fanart_show")
            self._external_player_url = config.get("external_player_url")
            self._danmaku = config.get("danmaku")
            self._video_together = config.get("video_together")
            self._srt2ass = config.get("srt2ass")

            # 获取媒体服务器
            if self._mediaservers:
                self._mediaserver = [self._mediaservers[0]]

        # 获取媒体服务信息
        if self._mediaserver:
            emby_servers = self._mediaserver_helper.get_services(
                name_filters=self._mediaserver
            )

            for _, emby_server in emby_servers.items():
                self._emby_server = emby_server.type
                self._emby_apikey = emby_server.config.config.get("apikey")
                self._emby_host = emby_server.config.config.get("host")
                if self._emby_host.endswith("/"):
                    self._emby_host = self._emby_host.rstrip("/")
                if not self._emby_host.startswith("http"):
                    self._emby_host = "http://" + self._emby_host

        self.stop_service()

        if self._enabled:
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)
            logger.info("MediaWarp 服务启动中...")
            self._scheduler.add_job(
                func=self.__run_service,
                trigger="date",
                run_date=datetime.now(tz=pytz.timezone(settings.TZ))
                + timedelta(seconds=2),
                name="MediaWarp启动服务",
            )

            if self._scheduler.get_jobs():
                self._scheduler.print_jobs()
                self._scheduler.start()

    def __update_config(self):
        self.update_config(
            {
                "enabled": self._enabled,
                "port": self._port,
                "media_strm_path": self._media_strm_path,
                "mediaservers": self._mediaservers,
                "crx": self._crx,
                "actor_plus": self._actor_plus,
                "fanart_show": self._fanart_show,
                "external_player_url": self._external_player_url,
                "danmaku": self._danmaku,
                "video_together": self._video_together,
                "srt2ass": self._srt2ass,
            }
        )

    def get_state(self) -> bool:
        """
        返回插件启用状态

        :return: True 表示插件已启用
        """
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        """
        返回插件远程命令列表，本插件无远程命令

        :return: None
        """
        pass

    def get_api(self) -> List[Dict[str, Any]]:
        """
        返回插件 API 端点列表，本插件无自定义 API

        :return: None
        """
        pass

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        拼装插件配置页面，需要返回两块数据：1、页面配置；2、数据结构
        """

        web_ui = [
            {
                "component": "VRow",
                "content": [
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 4},
                        "content": [
                            {
                                "component": "VSwitch",
                                "props": {
                                    "model": "crx",
                                    "label": "CRX美化",
                                    "hint": "crx 美化",
                                    "persistent-hint": True,
                                },
                            }
                        ],
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 4},
                        "content": [
                            {
                                "component": "VSwitch",
                                "props": {
                                    "model": "actor_plus",
                                    "label": "头像过滤",
                                    "hint": "过滤没有头像的演员和制作人员",
                                    "persistent-hint": True,
                                },
                            }
                        ],
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 4},
                        "content": [
                            {
                                "component": "VSwitch",
                                "props": {
                                    "model": "fanart_show",
                                    "label": "显示同人图",
                                    "hint": "显示同人图（fanart 图）",
                                    "persistent-hint": True,
                                },
                            }
                        ],
                    },
                ],
            },
            {
                "component": "VRow",
                "content": [
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 4},
                        "content": [
                            {
                                "component": "VSwitch",
                                "props": {
                                    "model": "external_player_url",
                                    "label": "外置播放器",
                                    "hint": "是否开启外置播放器（仅 Emby）",
                                    "persistent-hint": True,
                                },
                            }
                        ],
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 4},
                        "content": [
                            {
                                "component": "VSwitch",
                                "props": {
                                    "model": "danmaku",
                                    "label": "Web弹幕",
                                    "hint": "Web 弹幕",
                                    "persistent-hint": True,
                                },
                            }
                        ],
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 4},
                        "content": [
                            {
                                "component": "VSwitch",
                                "props": {
                                    "model": "video_together",
                                    "label": "共同观影",
                                    "hint": "共同观影",
                                    "persistent-hint": True,
                                },
                            }
                        ],
                    },
                ],
            },
        ]

        subtitle = [
            {
                "component": "VRow",
                "content": [
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 4},
                        "content": [
                            {
                                "component": "VSwitch",
                                "props": {
                                    "model": "srt2ass",
                                    "label": "SRT转ASS",
                                    "hint": "SRT 字幕转 ASS 字幕",
                                    "persistent-hint": True,
                                },
                            }
                        ],
                    },
                ],
            },
        ]

        return [
            {
                "component": "VCard",
                "props": {"variant": "outlined", "class": "mb-3"},
                "content": [
                    {
                        "component": "VCardTitle",
                        "props": {"class": "d-flex align-center"},
                        "content": [
                            {
                                "component": "VIcon",
                                "props": {
                                    "icon": "mdi-cog",
                                    "color": "primary",
                                    "class": "mr-2",
                                },
                            },
                            {"component": "span", "text": "基础设置"},
                        ],
                    },
                    {"component": "VDivider"},
                    {
                        "component": "VCardText",
                        "content": [
                            {
                                "component": "VForm",
                                "content": [
                                    {
                                        "component": "VRow",
                                        "content": [
                                            {
                                                "component": "VCol",
                                                "props": {"cols": 12, "md": 4},
                                                "content": [
                                                    {
                                                        "component": "VSwitch",
                                                        "props": {
                                                            "model": "enabled",
                                                            "label": "启用插件",
                                                        },
                                                    }
                                                ],
                                            },
                                            {
                                                "component": "VCol",
                                                "props": {"cols": 12, "md": 4},
                                                "content": [
                                                    {
                                                        "component": "VTextField",
                                                        "props": {
                                                            "model": "port",
                                                            "label": "端口",
                                                            "hint": "反代后媒体服务器访问端口",
                                                            "persistent-hint": True,
                                                        },
                                                    }
                                                ],
                                            },
                                            {
                                                "component": "VCol",
                                                "props": {"cols": 12, "md": 4},
                                                "content": [
                                                    {
                                                        "component": "VSelect",
                                                        "props": {
                                                            "multiple": True,
                                                            "chips": True,
                                                            "clearable": True,
                                                            "model": "mediaservers",
                                                            "label": "媒体服务器",
                                                            "items": [
                                                                {
                                                                    "title": config.name,
                                                                    "value": config.name,
                                                                }
                                                                for config in (
                                                                    self._mediaserver_helper.get_configs().values()
                                                                )
                                                                if config.type == "emby"
                                                                or config.type
                                                                == "jellyfin"
                                                            ],
                                                            "hint": "同时只能选择一个",
                                                            "persistent-hint": True,
                                                        },
                                                    }
                                                ],
                                            },
                                        ],
                                    },
                                ],
                            },
                            {
                                "component": "VRow",
                                "content": [
                                    {
                                        "component": "VCol",
                                        "props": {"cols": 12},
                                        "content": [
                                            {
                                                "component": "VTextarea",
                                                "props": {
                                                    "model": "media_strm_path",
                                                    "label": "Emby STRM 媒体库路径",
                                                    "rows": 5,
                                                    "placeholder": "一行一个",
                                                },
                                            },
                                        ],
                                    }
                                ],
                            },
                            {
                                "component": "VAlert",
                                "props": {
                                    "type": "info",
                                    "variant": "tonal",
                                    "density": "compact",
                                    "class": "mt-2",
                                },
                                "content": [
                                    {
                                        "component": "div",
                                        "text": "注意：",
                                    },
                                    {
                                        "component": "div",
                                        "text": "如果 MoviePilot 容器为 bridge 模式需要手动映射配置的端口",
                                    },
                                    {
                                        "component": "div",
                                        "text": "更多配置可以前往 MoviePilot 配置目录找到此插件的配置目录进行详细配置文件配置",
                                    },
                                ],
                            },
                            {
                                "component": "VAlert",
                                "props": {
                                    "type": "info",
                                    "variant": "tonal",
                                    "density": "compact",
                                    "class": "mt-2",
                                },
                                "content": [
                                    {
                                        "component": "div",
                                        "text": "目前支持 115网盘STRM助手，123云盘STRM助手，CloudMediaSync，OneStrm",
                                    },
                                    {
                                        "component": "div",
                                        "text": "Symedia，q115-strm 等软件生成的STRM文件",
                                    },
                                ],
                            },
                            {
                                "component": "VAlert",
                                "props": {
                                    "type": "info",
                                    "variant": "tonal",
                                    "density": "compact",
                                    "class": "mt-2",
                                },
                                "content": [
                                    {
                                        "component": "div",
                                        "text": "感谢项目作者：https://github.com/Akimio521/MediaWarp",
                                    },
                                ],
                            },
                        ],
                    },
                ],
            },
            {
                "component": "VCard",
                "props": {"variant": "outlined"},
                "content": [
                    {
                        "component": "VTabs",
                        "props": {"model": "tab", "grow": True, "color": "primary"},
                        "content": [
                            {
                                "component": "VTab",
                                "props": {"value": "web-ui"},
                                "content": [
                                    {
                                        "component": "VIcon",
                                        "props": {
                                            "icon": "mdi-file-move-outline",
                                            "start": True,
                                            "color": "#1976D2",
                                        },
                                    },
                                    {"component": "span", "text": "Web页面配置"},
                                ],
                            },
                            {
                                "component": "VTab",
                                "props": {"value": "subtitle"},
                                "content": [
                                    {
                                        "component": "VIcon",
                                        "props": {
                                            "icon": "mdi-sync",
                                            "start": True,
                                            "color": "#4CAF50",
                                        },
                                    },
                                    {"component": "span", "text": "字体相关设置"},
                                ],
                            },
                        ],
                    },
                    {"component": "VDivider"},
                    {
                        "component": "VWindow",
                        "props": {"model": "tab"},
                        "content": [
                            {
                                "component": "VWindowItem",
                                "props": {"value": "web-ui"},
                                "content": [
                                    {
                                        "component": "VCardText",
                                        "content": web_ui,
                                    }
                                ],
                            },
                            {
                                "component": "VWindowItem",
                                "props": {"value": "subtitle"},
                                "content": [
                                    {"component": "VCardText", "content": subtitle}
                                ],
                            },
                        ],
                    },
                ],
            },
        ], {
            "enabled": False,
            "port": "",
            "media_strm_path": "",
            "mediaservers": [],
            "crx": False,
            "actor_plus": False,
            "fanart_show": False,
            "external_player_url": False,
            "danmaku": False,
            "video_together": False,
            "srt2ass": False,
            "tab": "web-ui",
        }

    def get_page(self) -> List[dict]:
        """
        返回插件数据页面配置，本插件无数据页面

        :return: None
        """
        pass

    def __run_service(self):
        """
        运行服务
        """
        # 解析目标版本（跟随上游）
        self.__mediawarp_version = self.__resolve_target_version()

        if not Path(self.__mediawarp_path).exists():
            logger.info("尝试自动下载二级制文件中...")
            self.__download_and_extract()
            if not Path(self.__mediawarp_path).exists():
                logger.error("下载失败，MediaWarp 二级制文件不存在，无法启动插件")
                logger.info(
                    f"请将 MediaWarp 二级制文件放入 {self.__data_dir} 文件夹内"
                )
                self.__update_config()
                return
        else:
            # 已存在则比对版本，不一致时更新
            installed = None
            if os.path.exists(self.__mediawarp_version_path):
                with open(self.__mediawarp_version_path, "r", encoding="utf-8") as f:
                    installed = f.read().strip()
            if installed != self.__mediawarp_version:
                logger.info(
                    f"二进制版本不匹配（已装 {installed}，目标 {self.__mediawarp_version}），尝试自动更新..."
                )
                self.__download_and_extract()

        if not Path(self.__config_path / self.__config_filename).exists():
            logger.error("MediaWarp 配置文件不存在，无法启动插件")
            self.__update_config()
            return

        # ==================== 配置映射（对齐上游 0.2.x schema）====================
        # 上游 0.2.x 的 key 全为「小写 + 下划线」，与 0.1.x 的大写驼峰完全不同。
        # 结构参考：https://github.com/AkimioJR/MediaWarp/blob/main/config/config.yaml.example
        changes = {
            # --- 服务 ---
            "port": self._port,
            # --- 日志 ---
            "log.access.file": True,
            "log.access.console": False,
            # --- 媒体服务器 ---
            "server.type": "Jellyfin"
            if self._emby_server == "jellyfin"
            else "Emby",
            "server.addr": self._emby_host,
            "server.auth": self._emby_apikey,
            # --- Web 前端（注意：必须先开 web.enable，否则下面各项均不生效）---
            "web.enable": True,
            "web.index": bool(
                Path(self.__config_path / "static" / "index.html").exists()
            ),
            "web.crx": bool(self._crx),
            "web.actor_plus": bool(self._actor_plus),
            "web.fanart_show": bool(self._fanart_show),
            "web.danmaku": bool(self._danmaku),
            "web.external_player_url": bool(self._external_player_url),
            "web.video_together": bool(self._video_together),
            # --- HTTPStrm 302 直链 ---
            "http_strm.enable": True,
            "http_strm.final_url": True,
            "http_strm.prefix_list": [
                p.strip() for p in (self._media_strm_path or "").split("\n") if p.strip()
            ],
            # --- 字幕（同样需要 subtitle.enable）---
            "subtitle.enable": True,
            "subtitle.srt2ass": bool(self._srt2ass),
        }
        self.__modify_config(Path(self.__config_path / self.__config_filename), changes)

        Path(self.__config_path).mkdir(parents=True, exist_ok=True)
        Path(self.__logs_dir).mkdir(parents=True, exist_ok=True)

        self.process = psutil.Popen([str(self.__mediawarp_path)])

        if self.process.is_running():
            logger.info(
                f"MediaWarp 服务成功启动！（二进制版本 {self.__mediawarp_version}）"
            )

    def __resolve_target_version(self) -> str:
        """
        解析要使用的二进制版本。

        默认使用常量 DEFAULT_MEDIAWARP_VERSION；若其为空，则查询 GitHub API
        取上游最新正式版（跳过 pre-release）。查询失败时回退到已知可用版本。

        :return str: 形如 "v0.2.5" 的 tag
        """
        if DEFAULT_MEDIAWARP_VERSION:
            return DEFAULT_MEDIAWARP_VERSION

        try:
            resp = requests.get(
                f"https://api.github.com/repos/{MEDIAWARP_REPO}/releases/latest",
                timeout=10,
                proxies=settings.PROXY,
            )
            resp.raise_for_status()
            tag = (resp.json() or {}).get("tag_name")
            if tag:
                logger.info(f"检测到 MediaWarp 上游最新版本：{tag}")
                return tag
        except Exception as error:  # noqa: BLE001 - 网络不可用不应中断插件
            logger.warning(f"查询 MediaWarp 最新版本失败，回退默认值：{error}")

        return "v0.2.5"

    def __modify_config(self, config_path, modifications):
        """
        修改配置文件

        :param config_path: 配置文件路径
        :param modifications: 要修改的配置项字典
        :return: None
        """
        yaml = YAML()
        yaml.preserve_quotes = True
        yaml.indent(mapping=2, sequence=4, offset=2)

        def represent_bool(self, data):
            """
            将 Python bool 序列化为 YAML 大写 True/False 字符串

            :param data: 布尔值
            :return: YAML 标量节点
            """
            if data:
                return self.represent_scalar("tag:yaml.org,2002:bool", "True")
            else:
                return self.represent_scalar("tag:yaml.org,2002:bool", "False")

        RoundTripRepresenter.add_representer(bool, represent_bool)

        with open(config_path, "r", encoding="utf-8") as file:
            config = yaml.load(file)

        for key, value in modifications.items():
            keys = key.split(".")
            current = config
            for k in keys[:-1]:
                current = current.setdefault(k, {})
            current[keys[-1]] = value

        with open(config_path, "w", encoding="utf-8") as file:
            yaml.dump(config, file)

    def __get_download_url(self):
        """
        获取下载链接

        上游资产命名：MediaWarp_v0.2.5_linux_amd64.tar.gz
        注意版本号带 "v" 前缀（DDSRem 旧版无 v），且仓库已换成上游官方。

        :return str: 完整的下载 URL
        """
        machine = platform.machine().lower()
        if machine in ("arm64", "aarch64"):
            arch = "arm64"
        elif machine.startswith("armv7") or machine == "armv7l":
            arch = "armv7"
        else:
            arch = "amd64"

        system = platform.system().lower()
        if system == "darwin":
            os_name = "darwin"
        elif system == "windows":
            os_name = "windows"
        else:
            os_name = "linux"

        tag = self.__mediawarp_version

        # Windows 上游提供 .zip，其余平台为 .tar.gz
        if os_name == "windows":
            return (
                f"https://github.com/{MEDIAWARP_REPO}/releases/download/{tag}/"
                f"MediaWarp_{tag}_{os_name}_{arch}.zip"
            )

        return MEDIAWARP_ASSET_TEMPLATE.format(tag=tag, os=os_name, arch=arch)

    def __download_and_extract(self):
        """
        下载并解压上游二进制包。

        上游 tar.gz 内容（实测 v0.2.5）：
            MediaWarp            ← 二进制，位于包根
            LICENSE
            README.md
            config.yaml.example
        """
        url = self.__get_download_url()
        temp_dir = tempfile.mkdtemp()
        temp_file = os.path.join(temp_dir, "MediaWarp.tar.gz")

        try:
            Path(self.__config_path).mkdir(parents=True, exist_ok=True)
            Path(self.__data_dir).mkdir(parents=True, exist_ok=True)

            logger.info(f"正在下载: {url}")
            response = requests.get(
                url, stream=True, proxies=settings.PROXY, timeout=60
            )
            response.raise_for_status()

            with open(temp_file, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

            logger.info("正在解压文件...")
            with tarfile.open(temp_file, "r:gz") as tar:
                # 说明：这里刻意不用 tar.extract()，而是用 extractfile() 手工写出。
                # 原因有二：
                #   1) Python 3.14 起 tarfile 默认启用 extraction filter，
                #      直接 extract 会因 filter 策略不同而改变行为（MoviePilot V3
                #      要求 >= 3.14，本插件必须在该版本下可预期地工作）。
                #   2) 手工写出可显式校验目标文件名，天然免疫路径穿越。
                def _extract_flat(name_predicate, dest: Path) -> bool:
                    """从包中取出第一个满足条件的文件，写入 dest。"""
                    for member in tar.getmembers():
                        if not member.isfile():
                            continue
                        # 只取不含路径分隔符的文件名，规避目录穿越
                        if "/" in member.name or "\\" in member.name:
                            continue
                        if not name_predicate(member.name):
                            continue
                        source = tar.extractfile(member)
                        if source is None:
                            continue
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        with source, open(dest, "wb") as out:
                            shutil.copyfileobj(source, out)
                        return True
                    return False

                # --- 二进制 ---
                if _extract_flat(lambda n: n == "MediaWarp", Path(self.__mediawarp_path)):
                    Path(self.__mediawarp_path).chmod(0o755)
                else:
                    logger.error("压缩包内未找到 MediaWarp 二进制文件")
                    return

                # --- 示例配置（仅在本地尚无配置时落盘）---
                config_target = Path(self.__config_path / self.__config_filename)
                if not config_target.exists():
                    if _extract_flat(
                        lambda n: n == "config.yaml.example", config_target
                    ):
                        logger.info(f"示例配置文件已保存到 {config_target}")
                    else:
                        logger.warning(
                            "压缩包内未找到 config.yaml.example，"
                            "请手动放置配置文件后再启用插件"
                        )

            with open(self.__mediawarp_version_path, "w", encoding="utf-8") as f:
                f.write(self.__mediawarp_version)
            logger.info(
                f"安装完成！MediaWarp {self.__mediawarp_version} 已安装到 {self.__mediawarp_path}"
            )
        except Exception as error:  # noqa: BLE001 - 下载失败不应让插件加载崩溃
            logger.error(f"下载/解压 MediaWarp 失败：{error}", exc_info=True)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def stop_service(self):
        """
        退出插件
        """
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._scheduler.shutdown()
                self._scheduler = None
            if self.process:
                if self.process.is_running():
                    self.process.terminate()
        except Exception as e:
            logger.error(f"退出插件失败：{e}")
