# 日数据抓取优化

网页的“拉取数据”提交后台任务，可关闭页面后再回来查看进度。默认跳过活动相关商品和 `deal` 采集；站点、ASIN、配送邮编、币种和关键字段校验继续执行。

墨西哥默认配送邮编为 `11000`，加拿大为 `T4B 2T3`。可分别通过环境变量 `AMAZON_POSTAL_M`、`AMAZON_POSTAL_C` 覆盖；浏览器已有的其他邮编必须重新设置并通过页头校验，不能直接沿用。

- 首轮：优先使用 Selenium 商品主页面，每个商品通常一次尝试；页面不完整则保留待补抓标记。首次配送设置失败也直接留待补抓。
- 会话复用：同站点使用同一个浏览器和 requests 会话，配送状态每页验证，币种设置按站点复用；价格仍逐页检查币种。
- 补抓：首轮结果先发布，后台等待 30 秒后只处理未完成记录，最多两轮。每个商品最多三次路径尝试，验证码及配送/身份校验失败提前结束。浏览器会话意外丢失时允许一次重建。
- 并发：最多两个不同站点同时处理，同站点串行；各站点使用独立数据库连接和浏览器配置。连接已有调试浏览器时自动限制为单线程。
- 断点：每个商品处理后提交数据库，恢复时按 `data_id + count_day` 跳过完整记录。相同 ASIN 的不同国家分别计数。已确认不存在的字段仍以“无”记录，未核实的字段保持待补抓。
- 互斥：API 的同步及后台抓取共享操作系统文件锁，阻止多个服务实例重复拉取。进度持久化在 `.crawl_jobs/jobs.sqlite3`；异常退出后显示已中断，可在当天点击继续。系统不会自行重启已暂停的任务。

暂停按钮会等待当前商品的有界请求结束、保存进度，再退出任务。日数据网页服务可继续访问。定时任务的启用/禁用状态由 Windows 任务计划程序控制，代码更新不会自动启用它。

## 定时任务的认证处理

`scheduled_crawl.py` 现在使用各站专用的持久化 Chrome 配置，沿用今天已认证的 Cookie；浏览器关闭后会尝试以同一配置重新打开。认证可能过期，程序不会自动点击或破解验证码。运行前请保持 6471 服务可用，并使用能显示浏览器的 Windows 登录会话。

定时批次逐站运行，墨西哥、加拿大、德国、英国、意大利、法国、西班牙、日本、澳大利亚、美国；同站串行，美国最后。遇到认证时仅暂停当前站，不做反复请求或认证补偿；其他站继续。程序设置并核验邮编、币种及 ASIN，定时任务纳入广告活动商品，跳过 deal 和最多两轮普通缺失补抓。全新一天从当天数据开始，同一天的有效完整记录不会重复抓取。

若 Amazon 明确返回 “Sorry, we couldn't find that page” 或 “The web address you entered is not a functioning page on our site” 等商品失效页，会将其记入本次抓取结果和日抓取诊断，并直接进入下一个商品。失效状态不写入商品主表；后续任务仍会重新验证该链接。此规则与验证码识别分开，验证码仍然暂停当前站等待人工认证。

日数据跟进页面持续显示批次状态和待认证站点；`scheduled_crawl.log` 也记录 `AUTH_REQUIRED` 提醒。请在对应专用 Chrome 中手动完成认证，待其余站点结束后点击“认证后补拉”。只恢复当天待认证站点，并跳过已完整商品；仍有认证的站点继续保留提醒。也可运行 `python scheduled_crawl.py --resume-auth`，不能跨日补写。

状态持久化在 `.crawl_jobs/scheduled_state.json`，不依赖“最新任务”是否已经变成其他站点。重复启动、重复点击及与一次性顺序脚本重叠由文件锁拦截；监控失败或提交结果不明确时停止，不重试 POST。手动暂停某一任务会停止整个批次，或点击“暂停整个定时批次”；手动暂停不会自动恢复。进程异常退出不自动重新提交。

接口：`GET /api/amazon-day-data/scheduled` 查看批次与待认证站点，`POST /api/amazon-day-data/scheduled/resume-auth` 认证后补拉，`POST /api/amazon-day-data/scheduled/stop` 暂停整个批次。代码修改不会自动启用 Windows 定时任务。2026-10-20 经用户确认已恢复 `AmazonDataDailyCrawl`，每天北京时间 06:00 执行，核验下次执行时间为 2026-10-21 06:00；启用时未立即启动抓取。

## 接口

日本站代码为 `J`，域名 `www.amazon.co.jp`，币种 `JPY`，默认配送邮编 `100-0001`（可用 `AMAZON_POSTAL_J` 覆盖）。日本商品请求固定 `language=ja_JP`，支持日文评分、评价数、类目排名、整数日元价格和两段式邮编输入。日元符号只在日本站接受，明确的人民币、美元等其他币种仍拒绝；缺价保持待补抓。

`POST /api/amazon-day-data/crawl`，示例请求体：

```json
{
  "async": true,
  "resume": true,
  "workers": 2,
  "exclude_activity": true,
  "auto_compensate": true,
  "compensation_rounds": 2
}
```

响应 HTTP 202，包含 `job.id`。`data_ids` 可限制测试范围，空列表代表零个目标。`resume: false` 可对范围内已完成商品重新拉取。若需要只做首轮，使用 `auto_compensate: false`。同步调用（`async: false` 或不传）返回首轮结果，补抓使用独立接口。

- `GET /api/amazon-day-data/jobs/latest`：最新任务及首轮/最终汇总。
- `GET /api/amazon-day-data/jobs/<id>`：指定任务状态。
- `POST /api/amazon-day-data/jobs/<id>/stop`：暂停指定任务。
- `POST /api/amazon-day-data/jobs/<id>/resume`：恢复当天任务，保留原目标范围和浏览器设置。
- `POST /api/amazon-day-data/compensate`：独立补抓，支持相同的后台和范围参数。

`processed / total` 表示当前阶段的进度，包括该阶段跳过的有效断点；`pending` 是最近一次阶段核对的待处理数量。最终完整数来自数据库有效记录，不按日志行数累计。`finished` 表示本次任务结束，仍需看 `result.complete` 和 `result.remaining_re_pull` 判断数据是否全部完整。

活动过滤规则：deal 标记为空或未开启/否/0，且活动开始/结束日期为空。`exclude_activity: false` 可显式纳入活动商品；`deal` 字段的采集始终跳过。

## 验证

### Chrome 本地驱动

使用 `browser: "chrome"` 时，驱动按以下顺序查找：环境变量 `CHROMEDRIVER_PATH`、项目的 `.browser_drivers/chrome/chromedriver.exe`、系统 PATH。缺失时立即报错，不再在抓取线程中调用 Selenium Manager 自动下载。Edge 的现有启动方式不变。

Chrome 程序可通过 `CHROME_BINARY_PATH` 指定，也会搜索系统安装目录。启动前读取本地浏览器和驱动版本，比较 `MAJOR.MINOR.BUILD`；补丁号可以不同。Chrome 升级导致版本不兼容时，会明确提示更新本地驱动，不能使用旧驱动继续抓取。

2026-10-20 验证的组合为 Chrome `155.0.8059.85` / ChromeDriver `155.0.8059.84`。驱动来自 Google 官方 Chrome for Testing：

`https://storage.googleapis.com/chrome-for-testing-public/155.0.8059.84/win64/chromedriver-win64.zip`

升级时先查询官方 `https://googlechromelabs.github.io/chrome-for-testing/latest-patch-versions-per-build.json`，选取与新浏览器构建号匹配的驱动，再从官方提供的地址下载并替换本地驱动；不要长期固定旧版本。更换驱动前应停止抓取并确认没有正在使用该文件的 ChromeDriver 进程。

如需沿用人工认证的测试会话，可在请求中设置 `browser: "chrome"`、`debugger_address: "127.0.0.1:9223"`。仅对专用测试窗口开启本机调试端口，不应暴露到外网或关闭用户日常浏览器。连接现有窗口时使用单线程，不添加用户代理覆盖参数，任务结束不会关闭该窗口。此模式验证的是现有认证会话，不能据此宣称全新 Chrome 配置也能跳过站点认证。

两站同时复用已有会话时，改用 `debugger_addresses: {"C": "127.0.0.1:9223", "M": "127.0.0.1:9224"}` 和 `workers: 2`。两个端口必须对应不同 Chrome 配置目录和独立浏览器进程，禁止共用端口或同时传入 `debugger_address`。每个站点由自己的浏览器、数据库连接处理，站内串行，最多双站并行；新会话如遇认证应由用户完成。暂停后可只对原批次尚未完成的 `data_ids` 重新提交，避免重复已成功处理的商品。

### 自动化检查

```powershell
python -m unittest discover -q
node --check static/app.js
python -m py_compile web_app.py scheduled_crawl.py amazon_importer/day_crawler.py amazon_importer/crawl_jobs.py
```

自动化测试覆盖请求预算、验证码与错误邮编短路、ASIN 校验、断点、跨站点并发隔离、任务互斥、进度持久化及暂停。真实速度和成功率需要在同一批商品上分别记录首轮用时、完整数与补抓后的完整数；不能仅凭任务结束时间评价数据质量。
