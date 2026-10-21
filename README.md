# Amazon 表格导入项目

从 `C:\Users\Administrator\Desktop\运营链接-模板（全）.xlsx` 的第一个 sheet 导入数据到本地 MySQL `amazon.amazon_data`。

## 特性

- 按 `asin + country` 去重
- `销售建议 -> remark`
- `国家 -> country_name`
- `country` 由“简称”列最后一个字母生成
- `产品 -> name`
- `运营 -> operation`
- `小组 -> group`
- `接手月份 -> select_date`
- `ASIN+国家 -> asin_country`
- 新增数据的 `status` 默认为 `1`（正常）
- Excel 中存在的记录会更新非空字段，并自动恢复为正常状态
- 数据库中存在但 Excel 中缺失的记录会标记为淘汰，并记录淘汰日期
- 淘汰记录不会出现在业务列表中，也不会参与每日数据拉取
- 刷新使用单个事务；Excel 无有效链接时会报错且不修改数据库
- 支持 `--dry-run` 预演导入

## 安装

```bash
pip install -r requirements.txt
```

## 运行

```bash
python main.py
```

## 启动页面

```bash
python web_app.py
```

启动后打开 `http://127.0.0.1:6471`。

页面中“刷新连接信息”按钮会读取默认 Excel 文件，执行新增、非空字段更新、淘汰和恢复，然后重新加载列表。

试运行：

```bash
python main.py --dry-run --show-unmapped
```

## 参数

- `--excel` Excel 路径
- `--host` MySQL 主机
- `--port` MySQL 端口
- `--user` MySQL 用户
- `--password` MySQL 密码
- `--database` 数据库名
- `--table` 表名

## 仓库范围

请在项目根目录安装依赖和执行上述命令。仓库包含 `amazon_importer/` 抓取包、`web_app.py` 页面服务、`static/`、`templates/`、定时入口及测试。

浏览器登录配置、Cookie、本地驱动、虚拟环境、抓取状态、日志和业务数据不纳入 Git；新环境需要单独配置数据库、浏览器驱动及人工认证会话。抓取配置和验证方式见 `CRAWL_OPTIMIZATION.md`。
