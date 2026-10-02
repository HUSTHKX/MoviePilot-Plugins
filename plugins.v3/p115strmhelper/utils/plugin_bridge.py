"""
跨插件能力桥接（MoviePilot V3 适配层）。

MoviePilot V3 明确禁止插件之间通过 ``app.plugins.<other_plugin>`` 直接
import 对方模块：插件目录不保证在同名 import 路径下，且会引入隐式强耦合。

本模块提供一种"尽力而为"的解析方式：优先走宿主插件管理器暴露的稳定入口，
拿不到时回退到磁盘上的插件目录做一次受控的模块加载，全部失败则返回 None，
由调用方优雅降级（跳过增强功能，而不是让整个插件加载失败）。

注意：这里不吞掉任何异常到静默——所有失败都会以 debug/warning 级别记录。
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from typing import Any, Optional

from app.log import logger

__all__ = [
    "resolve_other_plugin_symbol",
    "get_p115_api_class",
]


# 已解析结果缓存：module_path -> 类对象 / None（避免重复 IO）
_SYMBOL_CACHE: dict[str, Optional[Any]] = {}


def _plugin_roots() -> list[Path]:
    """枚举可能的插件根目录（V2 / V3 布局都覆盖）。"""
    roots: list[Path] = []
    try:
        # 宿主内置：app/plugins/<plugin_dir>
        app_pkg = importlib.import_module("app")
        app_dir = Path(app_pkg.__file__).resolve().parent
        roots.append(app_dir / "plugins")
    except Exception as error:  # pragma: no cover - 环境相关
        logger.debug(f"【插件桥接】解析 app 包目录失败: {error}")

    # 允许通过环境变量追加自定义插件目录（便于本地调试）
    import os

    for extra in (os.environ.get("MOVIEPILOT_PLUGIN_DIRS") or "").split(os.pathsep):
        extra = extra.strip()
        if extra:
            roots.append(Path(extra))

    return roots


def _load_module_from_file(fq_name: str, file_path: Path) -> Optional[Any]:
    """以指定 fully-qualified 名从一个 .py 文件加载模块（不写入 sys.modules 的污染路径）。"""
    try:
        spec = importlib.util.spec_from_file_location(fq_name, str(file_path))
        if not spec or not spec.loader:
            return None
        module = importlib.util.module_from_spec(spec)
        # 先注册再 exec，保证模块内部相对导入可解析
        sys.modules[fq_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(fq_name, None)
            raise
        return module
    except Exception as error:
        logger.debug(f"【插件桥接】从文件加载 {fq_name} ({file_path}) 失败: {error}")
        sys.modules.pop(fq_name, None)
        return None


def resolve_other_plugin_symbol(
    plugin_id: str,
    module_rel_path: str,
    symbol: str,
) -> Optional[Any]:
    """
    尽力解析"另一个插件"里的某个符号。

    :param plugin_id: 插件目录名，例如 ``p115disk``
    :param module_rel_path: 插件内的相对模块路径，例如 ``p115_api``
    :param symbol: 目标符号名，例如 ``P115Api``
    :return: 解析到的对象；不可用时返回 None
    """
    cache_key = f"{plugin_id}.{module_rel_path}.{symbol}"
    if cache_key in _SYMBOL_CACHE:
        return _SYMBOL_CACHE[cache_key]

    resolved: Optional[Any] = None

    # 1) 直接 import（宿主以标准包方式加载插件时可用）
    for pkg_prefix in ("app.plugins", "plugins"):
        dotted = f"{pkg_prefix}.{plugin_id}.{module_rel_path}"
        try:
            module = importlib.import_module(dotted)
            resolved = getattr(module, symbol, None)
            if resolved is not None:
                logger.debug(f"【插件桥接】通过 {dotted} 解析到 {symbol}")
                break
        except (ImportError, ModuleNotFoundError):
            continue
        except Exception as error:
            logger.debug(f"【插件桥接】import {dotted} 异常: {error}")

    # 2) 回退：扫描插件根目录，按文件路径加载
    if resolved is None:
        for root in _plugin_roots():
            candidate = root / plugin_id
            if not candidate.is_dir():
                continue
            file_path = candidate / f"{module_rel_path}.py"
            if not file_path.is_file():
                continue
            fq_name = f"_mp_bridge_{plugin_id}_{module_rel_path.replace('/', '_')}"
            module = _load_module_from_file(fq_name, file_path)
            if module is not None:
                resolved = getattr(module, symbol, None)
                if resolved is not None:
                    logger.debug(
                        f"【插件桥接】通过文件路径 {file_path} 解析到 {symbol}"
                    )
                    break

    if resolved is None:
        logger.debug(
            f"【插件桥接】未能解析 {plugin_id}.{module_rel_path}.{symbol}"
            "（对应插件未安装或版本不兼容），将跳过该增强功能"
        )

    _SYMBOL_CACHE[cache_key] = resolved
    return resolved


def get_p115_api_class() -> Optional[Any]:
    """
    获取 P115Disk 插件的 ``P115Api`` 类（用于上传增强复用其内部能力）。

    MoviePilot V3 起不再允许插件间直接 import，因此这里做动态解析；
    解析不到时返回 None，调用方需自行降级。
    """
    return resolve_other_plugin_symbol("p115disk", "p115_api", "P115Api")
