# MoviePilot-Plugins

> [!NOTE]
> MoviePilot 个人插件仓库（V3）

本仓库fork自 [DDSRem-Dev/MoviePilot-Plugins](https://github.com/DDSRem-Dev/MoviePilot-Plugins)，
包含在自身 NAS 环境实测验证过的插件版本。

## 仓库地址

在 MoviePilot 中添加插件仓库时填写：

```
https://github.com/HUSTHKX/MoviePilot-Plugins
```

## 插件列表

#### 网盘类插件

- [115网盘STRM助手 V3](https://github.com/HUSTHKX/MoviePilot-Plugins/tree/main/plugins.v3/p115strmhelper)：
  115网盘STRM生成一条龙服务。适配 MoviePilot V3 SDK。

#### 工具类插件

- [MediaWarp V3](https://github.com/HUSTHKX/MoviePilot-Plugins/tree/main/plugins.v3/mediawarp)：
  EmbyServer/Jellyfin 中间件：优化播放 Strm 文件、自定义前端样式、自定义允许访问客户端、嵌入脚本。
  适配 MoviePilot V3 SDK。

## 说明：mediawarp 的二进制与配置版本

`mediawarp` **不携带二进制**，插件启动时按需从上游 GitHub Release 下载并解压到
`/config/plugins/mediawarp/MediaWarp`，版本号记录在 `version.txt`。

- **下载源**：`AkimioJR/MediaWarp`（上游官方）
- **默认版本**：`v0.2.5`（常量 `DEFAULT_MEDIAWARP_VERSION`，置空则启动时查询 GitHub API 取最新正式版）
- **资产命名**：`MediaWarp_v{tag}_{os}_{arch}.tar.gz`（**版本号带 `v` 前缀**）

> ⚠️ 注意区分两个仓库：
> - `DDSRem-Dev/MediaWarp` —— 早期 fork，**已停在 v0.1.12**，配置 schema 是旧的大写驼峰
> - `AkimioJR/MediaWarp` —— **上游官方**，当前 v0.2.5，配置 schema 为小写下划线
>
> 本插件统一切换到**上游官方**，因此配置文件 schema 与 0.1.x 不兼容，升级时需迁移。

### 0.1.12 → 0.2.5 配置 schema 变化（易猜错项）

| 旧 key（0.1.x） | 新 key（0.2.x） | 备注 |
|---|---|---|
| `ClientFilter` | **`client`** | ⚠️ 不是 `client_filter` |
| `ClientFilter.ClientList` | **`client.list`** | ⚠️ 不是 `client.client_list` |
| `HTTPStrm` | **`http_strm`** | ⚠️ 不是 `h_t_t_p_strm` |
| `MediaServer.ADDR` | `server.addr` | ⚠️ 不是 `a_d_d_r` |
| `HTTPStrm.TransCode` | `http_strm.proxy` | **语义相反**：`TransCode=False` → `proxy=false` |
| `AlistStrm.TransCode` | `alist_strm.proxy` | 同上 |
| `Logger.ServiceLogger` | `log.service` | 上游**保留**（未移除） |
| `Web.Head` | `web.head` | 上游**保留**（未移除） |

新增段落：`cache`（含 `http_strm_ttl`、`alist_api_ttl`、`image_ttl`、`subtitle_ttl`）。

> 权威 schema 以仓库内 `config/config.yaml.example` 为准：
> `https://raw.githubusercontent.com/AkimioJR/MediaWarp/main/config/config.yaml.example`

## 说明：p115strmhelper 为什么自带 wheels

`plugins.v3/p115strmhelper/wheels/` 目录存放该插件的全部 Python 依赖 wheel。
**这个目录必须随源码一起提交，不能删除**，原因：

1. `full_strm_sync`、`txt_tree_storage`、`share_strm_scan` 三个 Rust 编译包
   **未发布到 PyPI**（查询返回 404），只由上游仓库的 GitHub Actions 从
   `rust_utils/` 源码手工编译后提交回 `wheels/`。删除后 Rust Mode 功能会直接失效。
2. MoviePilot V3 宿主原生支持插件自带 wheels 目录，会把它作为 `--find-links`
   传给 `uv pip install`，实现离线自足安装。

wheels 已裁剪为仅保留 **Linux x86_64（cp312 / cp314）+ 纯 Python（py3-none-any）**，
体积约 3.6MB。如需支持其他平台（macOS / Windows / ARM），
可从[上游仓库](https://github.com/DDSRem-Dev/MoviePilot-Plugins)重新获取完整 wheels。

## 依赖版本锁定说明

`p115strmhelper` 锁定 `p115client==0.0.9.6.5.1` + `python-concurrenttools==0.1.8`，
这是**唯一可行组合**，请勿擅自升级：

| p115client | concurrenttools ≤0.1.8 | concurrenttools 0.1.9 | `iter_file_list` |
|---|---|---|---|
| 0.0.9.6.5.1 | ✅ | ❌ | ✅ 存在 |
| 0.0.9.6.6 ~ 0.0.9.6.7 | ✅ | ❌ | ❌ 已删除 |
| 0.0.9.7 ~ 0.0.9.7.2 | ❌ | ✅ | ❌ 已删除 |

- **矛盾一**：插件源码依赖 `p115client.tool.iterdir` 的 `iter_file_list` /
  `iter_files_with_path_skim` / `iter_files_with_path`，这三个符号在 `0.0.9.6.6`
  的重构中被移除（`iterdir.py` 顶层函数 47→19，改为 `iter_files_skim`）。
- **矛盾二**：`p115client ≤0.0.9.6.7` 内部调用 `concurrenttools.threadpool_map` /
  `taskgroup_map`，而 `python-concurrenttools 0.1.9` 将二者改名为
  `thread_conmap` / `async_conmap`，会导致 `ImportError`。

上游 `DDSRem-Dev/MoviePilot-Plugins` 的 `main` 分支 `p115disk` 同样 pin
`p115client==0.0.9.6.5.1`，与本仓库保持一致。

## 致谢

- [MoviePilot](https://github.com/jxxghp/MoviePilot)
- [MoviePilot-Plugins](https://github.com/jxxghp/MoviePilot-Plugins)
- [DDSRem-Dev/MoviePilot-Plugins](https://github.com/DDSRem-Dev/MoviePilot-Plugins)
- [p115client](https://github.com/ChenyangGao/p115client)
