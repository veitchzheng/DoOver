# DoOver 错题本

> 错题本，小题大做。

给三、四年级孩子与家长协作使用的**家用局域网错题管理系统**。

核心闭环：**拍照收集 → 三秒归因 → 艾宾浩斯间隔重练 → 消灭进光荣榜**。
数据只存在你家里的服务器上，不经过任何外部服务——孩子的作业、笔迹、姓名不外流。

---

## 功能速览

| 能力 | 说明 |
|---|---|
| 双形态录入 | **照片题**：手机拍照 → 拖框裁剪 → 30 秒录完；**错字词**：拼音/中文提示 + 错写 + 正写三件套，连录模式 10 秒一个 |
| 三秒归因 | 科目 / 知识点（三、四年级预置）/ 错因四分类（概念不会 · 审题马虎 · 计算失误 · 完全没思路） |
| 间隔复习引擎 | 录入后第 **1 / 3 / 7 / 15 / 30 天**各考一次，通过第 5 档"消灭"；拖堂自动顺延 |
| 三键判定 | 孩子独立做对 ✅ / 提示后才对 ⚠️ / 又做错了 ❌；❌ 归零回炉，错次 ≥3 自动标记**顽固题** |
| 防放水抽查 | 孩子自评的"做对了"默认标待复核，家长抽查通过才转正，驳回则追回星星、该题回炉 |
| 每日上限 | 默认每天最多 8 道（3~15 可调），到期题自动排队，不给孩子堆量 |
| 小怪兽激励 | 错题=小怪兽，做对得星，消灭进光荣榜；6 枚勋章由复习日志自动推算 |
| PDF 双模板 | **收藏本**（题+错因+正解+状态，归档翻阅）/ **重练卷**（题+演算留白，语文自动排田字格、英语排四线三格，答案集中末页可沿虚线撕下） |
| 统计看板 | 四数字卡、薄弱知识点 TOP5、错因占比、科目分布、待复核清单 |
| 自动备份 | 每日 03:30 全量 zip（库+照片+密钥），保留最近 30 份滚动；一键手动备份；`--verify-backup` 可恢复性校验 |

---

## 快速开始（本机体验）

前置：Python 3.10+（建议 3.12）

```bash
./start.sh          # 自动建 venv、装依赖并启动
```

- 浏览器打开 <http://localhost:8200>
- 首次启动自动生成 6 位**访问码**，见 `data/初始访问码.txt`（终端也会打印）
- 手机 / 平板连**同一 Wi-Fi**，访问 `http://<电脑IP>:8200` 即可拍照录入
- （可选）灌一组演示数据先体验：`.venv/bin/python seed.py --demo`（仅空库时写入）

## 一天的使用节奏

| 时机 | 谁 | 做什么 |
|---|---|---|
| 每晚作业后 | 家长 | 手机拍照录入当天错题，选好知识点与错因（30 秒/题） |
| 睡前 10 分钟 | 孩子 | 「今日」页一题一卡：纸上演算 → 翻面对照 → 三键判定 |
| 随时 | 家长 | 「统计」页抽查待复核判定，看薄弱知识点 |
| 周五晚 | 家长 | 导出「重练卷」PDF 打印，周末纸质重练 |
| 学期末 | 家长 | 导出「收藏本」归档；「我的」页开启新学期 |

---

## 部署到飞牛 NAS（Docker）

笔记本合盖就断服务，长期使用建议部署在常开的 NAS 上。

### 1. 构建镜像（从源码全量构建）

```bash
# 方式 A：NAS 可访问你内网 registry，构建双架构推送
docker buildx build --platform linux/amd64,linux/arm64 \
  -t <你的registry>/doover/doover:1.0.0 --push .

# 方式 B：离线交付——单架构构建 + 导出 tar 传到 NAS 再 load（fnOS 为 amd64）
docker buildx build --platform linux/amd64 -t doover/doover:1.0.0 --load .
docker save doover/doover:1.0.0 | gzip > doover-1.0.0-amd64.tar.gz
# NAS 上：docker load < doover-1.0.0-amd64.tar.gz
```

架构说明：镜像 tag 不带架构后缀，实际架构以构建平台为准；fnOS 常见为 x86_64（amd64）。

### 2. 启动

```bash
# 把 docker-compose.yml 中 volumes 左侧改成 NAS 实际路径，如：
#   /vol1/1000/docker/doover/data:/app/data
docker compose up -d
```

全家设备访问 `http://<NAS的IP>:8200`。

### 3. 升级与回滚

```bash
docker buildx build --platform linux/amd64 -t doover/doover:1.0.1 --load .   # 构建新版
docker compose down && docker compose up -d                                  # 切新版
# 回滚：compose 里 image 改回旧 tag（或 docker tag doover/doover:1.0.0 ...）再 up -d
```

数据卷与镜像解耦，升级/回滚不影响 `data/` 里的错题与备份。

---

## 备份与恢复

**自动**：每日 03:30 全量备份（db + 照片 + 缩略图 + 密钥 + manifest）→ `data/backups/doover-backup-*.zip`，保留最近 30 份滚动删除；每月 1 日 04:10 自动 VACUUM 压缩数据库。

**手动**：「统计」页或「我的」页一键「立即备份」；「下载」把最新备份另存一份到第二落盘（NAS 其他卷 / 移动硬盘）。

**自检**：

```bash
./check.sh                            # 服务健康 + 库完整性 + 磁盘 + 备份年龄（>48h 告警）
./check.sh --verify-backup data/backups/doover-backup-XXXX.zip   # 校验某备份可完整恢复
```

**恢复三步**：停容器 → 把备份 zip 解压覆盖到数据目录（db、photos、thumbs、secret.key 全量还原）→ 起容器。

> 纯前端方案"清一次浏览器缓存全丢"的问题在本架构下不存在：数据全在服务端。剩余风险是 NAS 磁盘物理损坏，靠"每日备份 + 第二落盘 + fnOS 自身快照"三层兜底。

---

## 判定规则与星星

| 判定 | 状态变化 | 星星 |
|---|---|---|
| ✅ 独立做对 | 阶段 +1，按新档排期 | +1 |
| ⚠️ 提示后才对 | 阶段不变，次日再考 | 0 |
| ❌ 又做错 | 归零回 1 天档，错次 +1 | 0 |
| 通过第 5 档（消灭） | 进光荣榜，不再排期 | +5 |
| 当日到期题全部通过 | — | +2 |
| 顽固题（错 ≥3 次）逆袭消灭 | — | +10 |

---

## 技术架构

```
手机（家长·录入）平板（孩子·复习）电脑（家长·统计/导出/打印）
        │  家庭局域网 HTTP · HttpOnly 签名 Cookie 鉴权
        ▼
FastAPI 单进程（server.py）
  ├─ REST API（13 个接口）      ├─ 复习引擎（engine.py，纯函数可测）
  ├─ PDF 双模板（pdfgen.py）   ├─ 定时备份（backup.py + APScheduler）
  └─ 静态托管前端（原生 JS 单页应用，无构建步骤）
        ▼
data/ 数据目录（备份的最小单元）
  ├─ doover.db    SQLite（WAL），仅存元数据，常年 <10MB
  ├─ photos/      题目原图（客户端压至长边 1200px）
  ├─ thumbs/      列表缩略图（360px）
  ├─ backups/     每日自动备份 zip
  └─ exports/     导出 PDF（保留最近 50 份）
```

| 模块 | 职责 |
|---|---|
| `config.py` | 环境变量与业务常量（间隔序列、科目/错因字典） |
| `db.py` | SQLite 连接、建表、预置知识点、settings 读写、安装密钥 |
| `auth.py` | HMAC 签名 Cookie、访问码哈希、登录防爆破 |
| `engine.py` | 复习状态机：排期/三键判定/每日清单/连击/星星/勋章（纯逻辑） |
| `pdfgen.py` | reportlab 双模板导出；田字格、四线三格为自定义 Flowable；内嵌 Noto Sans SC |
| `backup.py` | 全量备份、滚动保留、恢复校验、VACUUM，附 CLI（`--status/--run/--verify`） |
| `seed.py` | 可选演示数据（含 PIL 生成的示例题目照片） |
| `server.py` | FastAPI 全部路由 + 静态托管 + 调度器装配 |

**API 一览**（除 login/health 外全部需要 Cookie 鉴权）：
`POST /api/login`、`GET /api/today`、`POST/GET /api/items`、`PATCH/DELETE /api/items/{id}`、`POST /api/items/{id}/review`、`POST /api/review/{id}/verify`、`GET /api/stats`、`GET /api/me`、`GET/PUT /api/settings`、`POST /api/export/pdf`、`POST /api/backup`、`GET /api/health`、`GET /photos/{id}`。

**数据表**（6 张）：`items`（错题主状态机）、`photos`、`review_log`（只追加）、`star_ledger`（星星流水，勋章由日志推算）、`knowledge_points`（三科预置 + 自定义）、`settings`。

**性能设计**：照片为磁盘文件而非数据库 BLOB，库常年 <10MB；列表只加载 360px 缩略图；分页 + 索引；客户端上传前压缩。几年量级下查询不退化。

## 开发

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m pytest tests/ -v     # 37 项测试（引擎纯逻辑 + API 全链路 + PDF + 备份恢复）
```

测试通过独立临时数据目录运行，不触碰真实 `data/`。

## FAQ

**访问码忘了？** 访问码只存哈希无法找回，重置一条命令：
```bash
.venv/bin/python -c "import db,auth,secrets; c=''.join(secrets.choice('0123456789') for _ in range(6)); conn=db.get_conn(); db.settings_set(conn,'access_code_hash',auth.hash_code(c)); conn.commit(); conn.close(); print('新访问码:',c)"
```

**孩子自评放水怎么办？** 设置里保持"防放水抽查"开启：孩子判的 ✅ 会进入统计页待复核清单，家长抽查通过才转正；驳回会追回星星并把该题回炉。

**多久备份一次？** 每天 03:30 自动全量。建议家长顺手把「下载最新备份」另存到移动硬盘或其他卷，作为第二落盘。

**换手机/平板要重新登录吗？** 会话 30 天有效；过期或新设备用访问码登录即可。

**浏览器清缓存会丢数据吗？** 不会。前端零持久化，所有数据在服务端 `data/` 目录。

**学期怎么切换？** 「我的 → 设置 → 开启新学期」：旧学期错题归档（可按学期筛选回看），光荣榜保留，新学期从零开始。

---

## 版本

- **V1.0.0（当前）**：五个页面、双形态录入、复习引擎、防放水、统计、PDF 双模板、自动备份、Docker 交付
- **V2 展望**：OCR 题目文字识别、语音读题、同知识点变式题、PWA 安装到桌面
- **V3 方向**：多孩支持、学期间对比、错题趋势分析

设计与功能细节详见《错题本产品设计文档.html》。

> AI生成
