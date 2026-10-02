from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context


def _load_target_metadata():
    """
    解析插件的声明式基类 metadata。

    MoviePilot V3 不再保证插件以 app.plugins.<插件名> 的固定路径加载，
    而 Alembic 以 ``exec`` 方式执行 env.py（``__package__`` 可能为 None），
    因此这里按"相对导入 → 常见绝对路径 → 动态发现"依次尝试。
    """
    errors = []

    # 1) 相对导入（包内正常 import 场景）
    try:
        from ..db_manager import P115StrmHelperBase  # type: ignore[import-not-found]

        return P115StrmHelperBase.metadata
    except Exception as error:  # pragma: no cover - 运行环境相关
        errors.append(f"relative: {error}")

    # 2) 常见绝对路径
    for mod_path in (
        "app.plugins.p115strmhelper.db_manager",
        "plugins.p115strmhelper.db_manager",
    ):
        try:
            import importlib

            module = importlib.import_module(mod_path)
            return module.P115StrmHelperBase.metadata
        except Exception as error:  # pragma: no cover - 运行环境相关
            errors.append(f"{mod_path}: {error}")

    # 3) 全量兜底：从已导入的插件模块里找
    import sys

    for name, module in list(sys.modules.items()):
        if name.endswith("db_manager") and hasattr(module, "P115StrmHelperBase"):
            return module.P115StrmHelperBase.metadata

    raise RuntimeError(
        "无法解析 P115StrmHelperBase.metadata，尝试记录：" + "; ".join(errors)
    )


target_metadata = _load_target_metadata()

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use
config = context.config

# Interpret the config file for Python logging
# This line sets up loggers basically
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
# （target_metadata 已在文件顶部通过 _load_target_metadata() 解析）

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available

    Calls to context.execute() here emit the given string to the
    script output

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
