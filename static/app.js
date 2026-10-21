const { createApp } = Vue;

function todayString() {
    const now = new Date();
    const offset = now.getTimezoneOffset() * 60000;
    return new Date(now.getTime() - offset).toISOString().slice(0, 10);
}

const app = createApp({
    data() {
        return {
            activeView: "data-overview",
            overviewLoading: false,
            overviewLoaded: false,
            overviewErrorMessage: "",
            overviewDate: todayString(),
            overviewStockDate: "",
            overviewSummary: {
                risk_count: 0,
                attention_count: 0,
                senior_a_missing: 0,
                deal_active: 0,
                ad_inactive: 0,
                title_unmodified: 0,
                todo_pending: 0,
                pending_order: 0,
                adjustment_amount: 0,
                overdue_reassignment: 0,
                stocking_method: 0,
            },
            rows: [],
            search: "",
            basicOperation: "",
            operationOptions: [],
            page: 1,
            pageSize: 50,
            total: 0,
            totalPages: 1,
            loading: false,
            basicMetric: "",
            basicSummary: {
                senior_a_missing: 0,
                deal_active: 0,
                ad_inactive: 0,
                title_unmodified: 0,
            },
            dayRows: [],
            daySearch: "",
            dayOperation: "",
            dayDate: todayString(),
            dayPage: 1,
            dayPageSize: 50,
            dayTotal: 0,
            dayTotalPages: 1,
            dayLoading: false,
            dayErrorMessage: "",
            dayMetric: "all",
            daySummary: {
                risk_count: 0,
                attention_count: 0,
                price_changed: 0,
                rank_up: 0,
                star_down: 0,
                reviews_down: 0,
                buy_box_missing: 0,
                missing: 0,
                comparison_available: 0,
            },
            stockRows: [],
            stockSearch: "",
            stockDate: "",
            stockPage: 1,
            stockPageSize: 50,
            stockTotal: 0,
            stockTotalPages: 1,
            stockLoading: false,
            stockErrorMessage: "",
            stockMetric: "",
            stockSummary: {
                pending_order: 0,
                adjustment_amount: 0,
                overdue_reassignment: 0,
                stocking_method: 0,
            },
            stockImporting: false,
            stockDetailRows: [],
            stockDetailLoading: false,
            stockDetailErrorMessage: "",
            todoRows: [],
            todoTotal: 0,
            todoMetric: "",
            todoLoading: false,
            todoErrorMessage: "",
            todoCreateOpen: false,
            todoSaving: false,
            todoCompletingId: null,
            todoSaveError: "",
            todoForm: {
                create_date: todayString(),
                content: "",
                status: "0",
                end_date: "",
                important: "3",
                remark: "",
            },
            syncing: false,
            crawling: false,
            compensating: false,
            crawlJob: null,
            crawlJobTimer: null,
            scheduledBatch: null,
            scheduledNames: {},
            scheduledTimer: null,
            scheduledResuming: false,
            errorMessage: "",
            toastMessage: "",
            detailRow: null,
            detailColumns: [],
            detailIsDayData: false,
            detailIsStockData: false,
            detailEditing: false,
            detailForm: {},
            detailSaving: false,
            detailSaveError: "",
            detailTrend: null,
            trendMarkup: "",
            trendSeries: [
                { key: "price", label: "价格", color: "#147d92", axis: "left", decimals: 2 },
                { key: "bsr", label: "BSR", color: "#d46a00", axis: "right", decimals: 0 },
                { key: "reviews", label: "评论数", color: "#7a4dd8", axis: "right", decimals: 0 },
                { key: "star", label: "星级", color: "#2e9b53", axis: "left", decimals: 1 },
            ],
            selectedTrendMetrics: ["price", "bsr", "reviews", "star"],
            trendLoading: false,
            trendErrorMessage: "",
            searchTimer: null,
            daySearchTimer: null,
            stockSearchTimer: null,
            toastTimer: null,
            menuItems: [
                { key: "data-overview", label: "数据概览", icon: "▦" },
                { key: "basic-info", label: "基本信息", icon: "B" },
                { key: "todo-list", label: "待办事项", icon: "✓" },
                { key: "day-data", label: "日数据跟进", icon: "D" },
                { key: "stock-data", label: "库存跟进", icon: "K" },
                { key: "ad-activity", label: "广告活动", icon: "A" },
                { key: "settings", label: "系统设置", icon: "T" },
            ],
            overviewSections: [
                {
                    key: "basic",
                    label: "基本信息",
                    cards: [
                        { key: "senior_a_missing", label: "A+未创作", hint: "A+ 字段为无", view: "basic-info", metric: "senior_a_missing" },
                        { key: "deal_active", label: "活动开启", hint: "活动状态为开启", view: "basic-info", metric: "deal_active" },
                        { key: "ad_inactive", label: "广告未开启", hint: "广告活动为未开启", view: "basic-info", metric: "ad_inactive" },
                        { key: "title_unmodified", label: "标题未修改", hint: "标题状态为未修改", view: "basic-info", metric: "title_unmodified" },
                    ],
                },
                {
                    key: "todo",
                    label: "待办事项",
                    cards: [
                        { key: "todo_pending", label: "待办数", hint: "尚未完成的待办事项", view: "todo-list", metric: "pending" },
                    ],
                },
                {
                    key: "day",
                    label: "日数据风险与关注",
                    cards: [
                        { key: "risk_count", label: "风险项", hint: "价格变化或购物车无", view: "day-data", metric: "risk" },
                        { key: "attention_count", label: "关注项", hint: "排名、星级、评论或数据缺失", view: "day-data", metric: "attention" },
                    ],
                },
                {
                    key: "stock",
                    label: "库存跟进",
                    cards: [
                        { key: "pending_order", label: "厚存建单数", hint: "厚街待建单数量 > 0", view: "stock-data", metric: "pending_order" },
                        { key: "adjustment_amount", label: "调整数", hint: "调整量待备货数量 > 0", view: "stock-data", metric: "adjustment_amount" },
                        { key: "overdue_reassignment", label: "超期改派数", hint: "货件超期关闭等待改派数量 > 0", view: "stock-data", metric: "overdue_reassignment" },
                        { key: "stocking_method", label: "备货数", hint: "销售建议头程方式不为空", view: "stock-data", metric: "stocking_method" },
                    ],
                },
            ],
            dayMetricCards: [
                { key: "risk_count", label: "风险项", hint: "价格变化或购物车无", metric: "risk" },
                { key: "attention_count", label: "关注项", hint: "排名、星级、评论或数据缺失", metric: "attention" },
            ],
            columns: [
                { key: "id", label: "ID" },
                { key: "name", label: "产品" },
                { key: "operation", label: "运营" },
                { key: "picture", label: "图床" },
                { key: "asin", label: "ASIN" },
                { key: "link", label: "链接" },
                { key: "label", label: "标签" },
                { key: "country_name", label: "国家" },
                { key: "title", label: "标题" },
                { key: "point", label: "五点" },
                { key: "senior_a", label: "A+" },
                { key: "ad_activity", label: "广告活动" },
                { key: "deal_date", label: "活动开始日期" },
                { key: "deal_end_date", label: "活动结束日期" },
                { key: "stock_remark", label: "库存备注" },
                { key: "status", label: "Status" },
                { key: "remark", label: "销售建议" },
            ],
            basicMetricCards: [
                { key: "senior_a_missing", label: "A+未创作", hint: "A+ 字段为无" },
                { key: "deal_active", label: "活动开启", hint: "活动状态为开启" },
                { key: "ad_inactive", label: "广告未开启", hint: "广告活动为未开启" },
                { key: "title_unmodified", label: "标题未修改", hint: "标题状态为未修改" },
            ],
            basicDetailColumns: [
                { key: "id", label: "ID" },
                { key: "name", label: "产品" },
                { key: "operation", label: "运营" },
                { key: "picture", label: "图床" },
                { key: "asin", label: "ASIN" },
                { key: "seller_sku", label: "Seller SKU" },
                { key: "country", label: "国家简称" },
                { key: "country_name", label: "国家" },
                { key: "title", label: "标题" },
                { key: "point", label: "五点" },
                { key: "senior_a", label: "A+" },
                { key: "group", label: "小组" },
                { key: "select_date", label: "接手日期" },
                { key: "obsolete_date", label: "淘汰日期" },
                { key: "status", label: "Status" },
                { key: "remark", label: "销售建议" },
                { key: "ad_activity", label: "广告活动" },
                { key: "deal", label: "活动" },
                { key: "deal_date", label: "活动开始日期" },
                { key: "deal_end_date", label: "活动结束日期" },
                { key: "asin_country", label: "ASIN+国家" },
                { key: "link", label: "链接" },
                { key: "label", label: "标签" },
                { key: "stock_remark", label: "库存备注" },
            ],
            editReadonlyFields: [
                { key: "id", label: "ID" },
                { key: "asin", label: "ASIN" },
                { key: "country", label: "国家简称" },
                { key: "asin_country", label: "ASIN+国家" },
                { key: "link", label: "链接" },
            ],
            editableFields: [
                { key: "name", label: "产品", type: "text", maxlength: 255 },
                { key: "seller_sku", label: "Seller SKU", type: "text", maxlength: 50 },
                { key: "country_name", label: "国家", type: "text", maxlength: 50 },
                { key: "group", label: "小组", type: "text", maxlength: 20 },
                { key: "select_date", label: "接手日期", type: "date" },
                { key: "obsolete_date", label: "淘汰日期", type: "date" },
                {
                    key: "status",
                    label: "Status",
                    type: "select",
                    options: [
                        { value: "1", label: "正常" },
                        { value: "0", label: "淘汰" },
                    ],
                },
                { key: "title", label: "标题", type: "text", maxlength: 100 },
                { key: "point", label: "五点", type: "text", maxlength: 100 },
                { key: "senior_a", label: "A+", type: "text", maxlength: 100 },
                { key: "ad_activity", label: "广告活动", type: "text", maxlength: 255 },
                { key: "deal", label: "活动", type: "text", maxlength: 100 },
                { key: "deal_date", label: "活动开始日期", type: "text", maxlength: 100 },
                { key: "deal_end_date", label: "活动结束日期", type: "text", maxlength: 100 },
                { key: "label", label: "标签", type: "select", options: [
                    { value: "", label: "正常" },
                    { value: "新品链接", label: "新品链接" },
                    { value: "动销活动", label: "动销活动" },
                    { value: "清仓", label: "清仓" },
                    { value: "链接异常", label: "链接异常" },
                    { value: "链接挂了", label: "链接挂了" },
                ] },
                { key: "remark", label: "销售建议", type: "textarea", maxlength: 255 },
                { key: "stock_remark", label: "库存备注", type: "textarea", maxlength: 1000 },
            ],
            dayDetailColumns: [
                { key: "operation", label: "运营" },
                { key: "link", label: "链接" },
                { key: "picture", label: "图床" },
                { key: "price", label: "价格" },
                { key: "deal", label: "优惠" },
                { key: "bsr", label: "BSR" },
                { key: "follow_sale", label: "跟卖数量" },
                { key: "inventory", label: "库存" },
                { key: "star", label: "星级" },
                { key: "reviews", label: "评论" },
                { key: "count_day", label: "日期" },
            ],
            dayColumns: [
                { key: "id", label: "ID" },
                { key: "name", label: "产品" },
                { key: "operation", label: "运营" },
                { key: "tags", label: "标签" },
                { key: "deal", label: "活动" },
                { key: "link", label: "链接" },
                { key: "picture", label: "图床" },
                { key: "country_name", label: "国家" },
                { key: "buy_box", label: "Buy Box" },
                { key: "price", label: "价格" },
                { key: "star", label: "星级" },
                { key: "reviews", label: "评论" },
                { key: "bsr", label: "BSR" },
                { key: "follow_sale", label: "跟卖数量" },
                { key: "inventory", label: "库存" },
                { key: "count_day", label: "日期" },
            ],
            stockColumns: [
                { key: "stock_id", label: "ID" },
                { key: "name", label: "产品" },
                { key: "asin", label: "ASIN" },
                { key: "link", label: "链接" },
                { key: "stock_date", label: "日期" },
                { key: "available_inventory", label: "FBA可用库存" },
                { key: "in_transit", label: "国际在途" },
                { key: "pending_order", label: "厚街待建单数量" },
                { key: "adjustment_amount", label: "调整量d待备货数量" },
                { key: "overdue_reassignment", label: "货件超期关闭等待改派数量" },
                { key: "stocking_method", label: "销售建议头程方式" },
                { key: "sea_quantity", label: "销售建议空运数量" },
                { key: "air_quantity", label: "销售建议其他运输方式数量" },
            ],
            stockDetailColumns: [
                { key: "stock_date", label: "日期" },
                { key: "available_inventory", label: "FBA可用库存" },
                { key: "in_transit", label: "国际在途" },
                { key: "pending_order", label: "厚街待建单数量" },
                { key: "adjustment_amount", label: "调整量d待备货数量" },
                { key: "overdue_reassignment", label: "货件超期关闭等待改派数量" },
                { key: "stocking_method", label: "销售建议头程方式" },
                { key: "sea_quantity", label: "销售建议空运数量" },
                { key: "air_quantity", label: "销售建议其他运输方式数量" },
            ],
            stockMetricCards: [
                { key: "pending_order", label: "厚存建单数", hint: "厚街待建单数量 > 0" },
                { key: "adjustment_amount", label: "调整数", hint: "调整量d待备货数量 > 0" },
                { key: "overdue_reassignment", label: "超期改派数", hint: "货件超期关闭等待改派数量 > 0" },
                { key: "stocking_method", label: "备货数", hint: "销售建议头程方式不为空" },
            ],
        };
    },
    computed: {
        currentMenu() {
            return this.menuItems.find((item) => item.key === this.activeView) || this.menuItems[0];
        },
        currentTitle() {
            const titles = {
                "data-overview": "数据概览",
                "basic-info": "基本信息",
                "todo-list": "待办事项",
                "day-data": "日数据跟进",
                "stock-data": "库存跟进",
            };
            return titles[this.activeView] || "敬请期待";
        },
    },
    mounted() {
        this.loadOverview();
        this.pollCrawlJob();
        this.pollScheduledBatch();
        document.addEventListener("keydown", this.handleKeydown);
        window.addEventListener("resize", this.handleResize);
    },
    beforeUnmount() {
        document.removeEventListener("keydown", this.handleKeydown);
        window.removeEventListener("resize", this.handleResize);
        window.clearTimeout(this.searchTimer);
        window.clearTimeout(this.daySearchTimer);
        window.clearTimeout(this.stockSearchTimer);
        window.clearTimeout(this.toastTimer);
        window.clearTimeout(this.crawlJobTimer);
        window.clearTimeout(this.scheduledTimer);
    },
    methods: {
        formatStatus(value) {
            const normalized = String(value ?? "").trim();
            if (normalized === "1") {
                return "正常";
            }
            if (normalized === "0") {
                return "淘汰";
            }
            return "未知";
        },
        statusClass(value) {
            const normalized = String(value ?? "").trim();
            if (normalized === "1") {
                return "status-normal";
            }
            if (normalized === "0") {
                return "status-obsolete";
            }
            return "status-unknown";
        },
        formatBuyBox(value) {
            const normalized = String(value ?? "").trim();
            if (normalized === "1") {
                return "有";
            }
            if (normalized === "0") {
                return "无";
            }
            return "未知";
        },
        formatTodoStatus(value) {
            return String(value ?? "").trim() === "1" ? "已完成" : "未完成";
        },
        todoStatusClass(value) {
            return String(value ?? "").trim() === "1" ? "status-normal" : "todo-status-pending";
        },
        formatImportance(value) {
            const labels = { "1": "低", "2": "普通", "3": "中", "4": "高", "5": "紧急" };
            return labels[String(value ?? "").trim()] || "未设置";
        },
        labelClass(value) {
            const normalized = String(value ?? "").trim();
            const classes = {
                "新品链接": "label-new",
                "动销活动": "label-active",
                "清仓": "label-clearance",
                "链接异常": "label-error",
            };
            return classes[normalized] || "label-empty";
        },
        handlePictureError(event) {
            const image = event.currentTarget;
            image.hidden = true;
            const fallback = image.parentElement.querySelector(".picture-fallback");
            if (fallback) {
                fallback.hidden = false;
            }
        },
        formatLabel(value) {
            return String(value ?? "").trim() || "正常";
        },
        displayValue(value, fallback = "暂无") {
            const normalized = String(value ?? "").trim();
            return normalized === "" ? fallback : normalized;
        },
        dealColor(type) {
            return ({ price_discount: "yellow", coupon: "red", promotion: "orange" })[type] || "gray";
        },
        fieldKey(field) {
            if (!field || typeof field !== "object") {
                return "";
            }
            return String(field.key ?? "").trim();
        },
        fieldLabel(field) {
            if (!field || typeof field !== "object") {
                return "";
            }
            return String(field.label ?? "").trim();
        },
        isField(field, key) {
            return this.fieldKey(field) === key;
        },
        rawFieldValue(row, field) {
            const key = this.fieldKey(field);
            if (!key || !row || typeof row !== "object") {
                return "";
            }
            return row[key];
        },
        displayFieldValue(row, field, fallback = "暂无") {
            return this.displayValue(this.rawFieldValue(row, field), fallback);
        },
        summaryValue(card) {
            const key = this.fieldKey(card);
            return key ? (this.daySummary[key] || 0) : 0;
        },
        basicSummaryValue(card) {
            const key = this.fieldKey(card);
            return key ? (this.basicSummary[key] || 0) : 0;
        },
        overviewValue(card) {
            const key = this.fieldKey(card);
            return key ? (this.overviewSummary[key] || 0) : 0;
        },
        async loadOverview() {
            this.overviewLoading = true;
            this.overviewErrorMessage = "";
            const day = todayString();
            try {
                const responses = await Promise.all([
                    fetch(`/api/basic-info?page=1&page_size=1&search=&metric=`),
                    fetch(`/api/todo-list?metric=pending`),
                    fetch(`/api/day-data?page=1&page_size=1&date=${encodeURIComponent(day)}&metric=all`),
                    fetch(`/api/stock-data?page=1&page_size=1&search=&metric=`),
                ]);
                const results = await Promise.all(responses.map((response) => response.json()));
                results.forEach((result, index) => {
                    if (!responses[index].ok || !result.success) {
                        throw new Error(result.message || "概览数据加载失败");
                    }
                });
                const [basicData, todoData, dayData, stockData] = results;
                this.overviewSummary = {
                    senior_a_missing: basicData.summary?.senior_a_missing || 0,
                    deal_active: basicData.summary?.deal_active || 0,
                    ad_inactive: basicData.summary?.ad_inactive || 0,
                    title_unmodified: basicData.summary?.title_unmodified || 0,
                    todo_pending: todoData.total || 0,
                    risk_count: dayData.summary?.risk_count || 0,
                    attention_count: dayData.summary?.attention_count || 0,
                    pending_order: stockData.summary?.pending_order || 0,
                    adjustment_amount: stockData.summary?.adjustment_amount || 0,
                    overdue_reassignment: stockData.summary?.overdue_reassignment || 0,
                    stocking_method: stockData.summary?.stocking_method || 0,
                };
                this.overviewDate = dayData.date || day;
                this.overviewStockDate = stockData.date || "";
                this.overviewLoaded = true;
            } catch (error) {
                this.overviewErrorMessage = `数据概览加载失败：${error.message}`;
                this.showToast(error.message);
            } finally {
                this.overviewLoading = false;
            }
        },
        async openOverviewMetric(card) {
            if (!card || this.overviewLoading) {
                return;
            }
            this.activeView = card.view;
            this.closeDetail();
            if (card.view === "basic-info") {
                this.search = "";
                this.basicOperation = "";
                this.basicMetric = card.metric;
                this.page = 1;
                await this.loadOperationOptions();
                await this.loadBasicInfo();
            } else if (card.view === "day-data") {
                this.daySearch = "";
                this.dayOperation = "";
                this.dayDate = this.overviewDate || todayString();
                this.dayMetric = card.metric;
                this.dayPage = 1;
                await this.loadOperationOptions();
                await this.loadDayData();
            } else if (card.view === "todo-list") {
                this.todoMetric = "pending";
                await this.loadTodoList();
            } else if (card.view === "stock-data") {
                this.stockSearch = "";
                this.stockMetric = card.metric;
                this.stockPage = 1;
                await this.loadStockData();
            }
        },
        stockSummaryValue(card) {
            const key = this.fieldKey(card);
            return key ? (this.stockSummary[key] || 0) : 0;
        },
        async loadBasicInfo() {
            this.loading = true;
            this.errorMessage = "";
            const query = new URLSearchParams({
                page: this.page,
                page_size: this.pageSize,
                search: this.search,
                operation: this.basicOperation,
                metric: this.basicMetric,
            });

            try {
                const response = await fetch(`/api/basic-info?${query.toString()}`);
                const result = await response.json();
                if (!response.ok || !result.success) {
                    throw new Error(result.message || "数据加载失败");
                }
                this.rows = result.rows;
                this.total = result.total;
                this.totalPages = result.total_pages;
                this.basicMetric = result.metric || "";
                this.basicSummary = {
                    senior_a_missing: 0,
                    deal_active: 0,
                    ad_inactive: 0,
                    title_unmodified: 0,
                    ...(result.summary || {}),
                };
            } catch (error) {
                this.errorMessage = `数据加载失败：${error.message}`;
                this.showToast(error.message);
            } finally {
                this.loading = false;
            }
        },
        async loadOperationOptions() {
            try {
                const response = await fetch("/api/basic-info/operations");
                const result = await response.json();
                if (!response.ok || !result.success) {
                    throw new Error(result.message || "运营选项加载失败");
                }
                this.operationOptions = result.operations || [];
            } catch (error) {
                this.showToast(error.message);
            }
        },
        async loadDayData() {
            this.dayLoading = true;
            this.dayErrorMessage = "";
            const query = new URLSearchParams({
                page: this.dayPage,
                page_size: this.dayPageSize,
                search: this.daySearch,
                operation: this.dayOperation,
                date: this.dayDate,
                metric: this.dayMetric,
            });

            try {
                const response = await fetch(`/api/day-data?${query.toString()}`);
                const result = await response.json();
                if (!response.ok || !result.success) {
                    throw new Error(result.message || "日数据加载失败");
                }
                this.dayRows = result.rows;
                this.daySummary = {
                    risk_count: 0,
                    attention_count: 0,
                    price_changed: 0,
                    rank_up: 0,
                    star_down: 0,
                    reviews_down: 0,
                    buy_box_missing: 0,
                    missing: 0,
                    comparison_available: 0,
                    ...(result.summary || {}),
                };
                this.dayTotal = result.total;
                this.dayTotalPages = result.total_pages;
                this.dayDate = result.date || this.dayDate;
            } catch (error) {
                this.dayErrorMessage = `日数据加载失败：${error.message}`;
                this.showToast(error.message);
            } finally {
                this.dayLoading = false;
            }
        },
        async loadStockData() {
            this.stockLoading = true;
            this.stockErrorMessage = "";
            const query = new URLSearchParams({
                page: this.stockPage,
                page_size: this.stockPageSize,
                search: this.stockSearch,
                metric: this.stockMetric,
            });

            try {
                const response = await fetch(`/api/stock-data?${query.toString()}`);
                const result = await response.json();
                if (!response.ok || !result.success) {
                    throw new Error(result.message || "库存数据加载失败");
                }
                this.stockRows = result.rows;
                this.stockTotal = result.total;
                this.stockTotalPages = result.total_pages;
                this.stockDate = result.date || "";
                this.stockMetric = result.metric || "";
                this.stockSummary = {
                    pending_order: 0,
                    adjustment_amount: 0,
                    overdue_reassignment: 0,
                    stocking_method: 0,
                    ...(result.summary || {}),
                };
            } catch (error) {
                this.stockErrorMessage = `库存数据加载失败：${error.message}`;
                this.showToast(error.message);
            } finally {
                this.stockLoading = false;
            }
        },
        async loadTodoList() {
            this.todoLoading = true;
            this.todoErrorMessage = "";
            try {
                const query = new URLSearchParams({ metric: this.todoMetric });
                const response = await fetch(`/api/todo-list?${query.toString()}`);
                const result = await response.json();
                if (!response.ok || !result.success) {
                    throw new Error(result.message || "待办事项加载失败");
                }
                this.todoRows = result.rows || [];
                this.todoTotal = result.total || 0;
                this.todoMetric = result.metric || "";
            } catch (error) {
                this.todoErrorMessage = `待办事项加载失败：${error.message}`;
                this.showToast(error.message);
            } finally {
                this.todoLoading = false;
            }
        },
        openTodoCreate() {
            this.todoForm = {
                create_date: todayString(),
                content: "",
                status: "0",
                end_date: "",
                important: "3",
                remark: "",
            };
            this.todoSaveError = "";
            this.todoCreateOpen = true;
        },
        closeTodoCreate() {
            if (this.todoSaving) {
                return;
            }
            this.todoCreateOpen = false;
            this.todoSaveError = "";
        },
        async saveTodo() {
            if (this.todoSaving) {
                return;
            }
            if (!String(this.todoForm.content || "").trim()) {
                this.todoSaveError = "事项内容不能为空";
                return;
            }

            this.todoSaving = true;
            this.todoSaveError = "";
            try {
                const response = await fetch("/api/todo-list", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(this.todoForm),
                });
                const result = await response.json();
                if (!response.ok || !result.success) {
                    throw new Error(result.message || "新增待办事项失败");
                }
                this.todoCreateOpen = false;
                this.showToast(result.message || "待办事项新增成功");
                await this.loadTodoList();
            } catch (error) {
                this.todoSaveError = `保存失败：${error.message}`;
            } finally {
                this.todoSaving = false;
            }
        },
        async completeTodo(item) {
            if (!item || !item.id || this.todoCompletingId !== null) {
                return;
            }
            this.todoCompletingId = item.id;
            try {
                const response = await fetch(`/api/todo-list/${item.id}/complete`, {
                    method: "PATCH",
                });
                const result = await response.json();
                if (!response.ok || !result.success) {
                    throw new Error(result.message || "更新待办状态失败");
                }
                this.showToast(result.message || "待办事项已完成");
                await this.loadTodoList();
            } catch (error) {
                this.showToast(`操作失败：${error.message}`);
            } finally {
                this.todoCompletingId = null;
            }
        },
        triggerStockImport() {
            const input = this.$refs.stockImportInput;
            if (!input || this.stockImporting) {
                return;
            }
            input.value = "";
            input.click();
        },
        async importStockData(event) {
            const file = event.target.files && event.target.files[0];
            if (!file || this.stockImporting) {
                return;
            }

            this.stockImporting = true;
            const formData = new FormData();
            formData.append("file", file);

            try {
                const response = await fetch("/api/stock-data/import", {
                    method: "POST",
                    body: formData,
                });
                const result = await response.json();
                if (!response.ok || !result.success) {
                    throw new Error(result.message || "库存数据导入失败");
                }
                const stats = result.stats || {};
                this.showToast(
                    `导入完成：新增 ${stats.inserted_rows || 0} 条，未匹配 ${stats.skipped_unmatched_sku || 0} 条`
                );
                this.stockPage = 1;
                await this.loadStockData();
            } catch (error) {
                this.showToast(`库存数据导入失败：${error.message}`);
            } finally {
                this.stockImporting = false;
                event.target.value = "";
            }
        },
        async openStockDetail(row) {
            const sellerSku = String(row?.seller_sku || "").trim();
            this.detailRow = row;
            this.detailColumns = [];
            this.detailIsDayData = false;
            this.detailIsStockData = true;
            this.detailEditing = false;
            this.detailForm = {};
            this.detailSaving = false;
            this.detailSaveError = "";
            this.detailTrend = null;
            this.trendMarkup = "";
            this.trendErrorMessage = "";
            this.stockDetailRows = [];
            this.stockDetailLoading = true;
            this.stockDetailErrorMessage = "";

            if (!sellerSku) {
                this.stockDetailLoading = false;
                this.stockDetailErrorMessage = "缺少 Seller SKU，无法查询库存历史";
                return;
            }

            try {
                const query = new URLSearchParams({ seller_sku: sellerSku });
                const response = await fetch(`/api/stock-data/history?${query.toString()}`);
                const result = await response.json();
                if (!response.ok || !result.success) {
                    throw new Error(result.message || "库存历史加载失败");
                }
                this.stockDetailRows = result.rows || [];
            } catch (error) {
                this.stockDetailErrorMessage = `库存历史加载失败：${error.message}`;
                this.showToast(error.message);
            } finally {
                this.stockDetailLoading = false;
            }
        },
        async loadDetailTrend(row) {
            if (!row || row.data_id === undefined || row.data_id === null || row.data_id === "") {
                this.detailTrend = null;
                this.trendMarkup = "";
                this.trendErrorMessage = "缺少 data_id，无法加载趋势图";
                return;
            }

            this.trendLoading = true;
            this.trendErrorMessage = "";
            this.detailTrend = null;
            this.trendMarkup = "";

            try {
                const query = new URLSearchParams({
                    data_id: row.data_id,
                    end_date: row.count_day || this.dayDate,
                });
                const response = await fetch(`/api/day-data/trend?${query.toString()}`);
                const result = await response.json();
                if (!response.ok || !result.success) {
                    throw new Error(result.message || "趋势数据加载失败");
                }
                this.detailTrend = result;
                this.trendMarkup = this.buildTrendMarkup(result.points || [], this.selectedTrendMetrics);
                return;
            } catch (error) {
                this.trendErrorMessage = `趋势数据加载失败：${error.message}`;
            } finally {
                this.trendLoading = false;
            }
        },
        toggleTrendMetric(key) {
            if (this.selectedTrendMetrics.includes(key)) {
                this.selectedTrendMetrics = this.selectedTrendMetrics.filter((item) => item !== key);
            } else {
                this.selectedTrendMetrics = [...this.selectedTrendMetrics, key];
            }
            this.trendMarkup = this.buildTrendMarkup(this.detailTrend?.points || [], this.selectedTrendMetrics);
        },
        buildTrendMarkup(points, selectedMetrics = this.selectedTrendMetrics) {
            if (!points || !points.length) {
                return "";
            }

            const width = 720;
            const height = 320;
            const padding = { top: 36, right: 72, bottom: 42, left: 56 };
            const plotWidth = width - padding.left - padding.right;
            const plotHeight = height - padding.top - padding.bottom;
            const dates = points.map((item) => item.count_day);
            const toNumber = (value) => {
                if (value === null || value === undefined || value === "") {
                    return null;
                }
                const normalized = Number(String(value).replace(/,/g, ""));
                return Number.isFinite(normalized) ? normalized : null;
            };
            const seriesValues = Object.fromEntries(this.trendSeries.map((series) => [
                series.key,
                points.map((item) => toNumber(item[`${series.key}_value`])),
            ]));
            const selectedSeries = this.trendSeries.filter((series) => selectedMetrics.includes(series.key));
            const valuesForAxis = (axis) => selectedSeries
                .filter((series) => series.axis === axis)
                .flatMap((series) => seriesValues[series.key])
                .filter((value) => value !== null && value !== undefined);
            const leftValues = valuesForAxis("left");
            const rightValues = valuesForAxis("right");

            const padRange = (min, max, factor = 0.12) => {
                if (!Number.isFinite(min) || !Number.isFinite(max)) {
                    return { min: 0, max: 1 };
                }
                if (min === max) {
                    const offset = min === 0 ? 1 : Math.abs(min) * factor || 1;
                    return { min: min - offset, max: max + offset };
                }
                const span = max - min;
                const offset = span * factor;
                return { min: min - offset, max: max + offset };
            };

            const leftRange = padRange(leftValues.length ? Math.min(...leftValues) : 0, leftValues.length ? Math.max(...leftValues) : 1);
            const rightRange = padRange(rightValues.length ? Math.min(...rightValues) : 0, rightValues.length ? Math.max(...rightValues) : 1);
            const leftMin = leftRange.min;
            const leftMax = leftRange.max;
            const rightMin = rightRange.min;
            const rightMax = rightRange.max;

            const xAt = (index) => {
                if (dates.length <= 1) {
                    return padding.left + plotWidth / 2;
                }
                return padding.left + (index * plotWidth) / (dates.length - 1);
            };

            const yAt = (value, min, max) => {
                if (value === null || value === undefined) {
                    return null;
                }
                if (max === min) {
                    return padding.top + plotHeight / 2;
                }
                const ratio = (value - min) / (max - min);
                return padding.top + plotHeight - ratio * plotHeight;
            };

            const ticks = 4;
            const axisTicks = (min, max) => {
                const result = [];
                for (let index = 0; index < ticks; index += 1) {
                    const ratio = ticks === 1 ? 0 : index / (ticks - 1);
                    result.push(min + (max - min) * ratio);
                }
                return result;
            };

            const axisLabel = (value, decimals) => Number(value).toFixed(decimals);

            const buildPath = (values, min, max) => {
                let path = "";
                let active = false;
                values.forEach((value, index) => {
                    const y = yAt(value, min, max);
                    if (y === null) {
                        active = false;
                        return;
                    }
                    const x = xAt(index);
                    path += active ? ` L ${x.toFixed(2)} ${y.toFixed(2)}` : `M ${x.toFixed(2)} ${y.toFixed(2)}`;
                    active = true;
                });
                return path.trim();
            };

            const escapeXml = (value) => String(value).replace(/[&<>\"']/g, (character) => ({
                "&": "&amp;",
                "<": "&lt;",
                ">": "&gt;",
                "\"": "&quot;",
                "'": "&apos;",
            })[character]);
            const seriesMarkup = (series) => {
                const values = seriesValues[series.key];
                const min = series.axis === "left" ? leftMin : rightMin;
                const max = series.axis === "left" ? leftMax : rightMax;
                const path = buildPath(values, min, max);
                const line = path
                    ? `<path d="${path}" fill="none" stroke="${series.color}" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round" />`
                    : "";
                const circles = values.map((value, index) => {
                    const y = yAt(value, min, max);
                    if (y === null) {
                        return "";
                    }
                    const datePoint = points[index];
                    const tooltipValues = selectedSeries.map((selected) => {
                        const selectedValue = seriesValues[selected.key][index];
                        const display = selectedValue === null
                            ? "无"
                            : Number(selectedValue).toFixed(selected.decimals);
                        return `${selected.label}：${display}`;
                    });
                    const tooltip = escapeXml(`${datePoint.count_day}\n${tooltipValues.join("\n")}`);
                    return `<circle cx="${xAt(index).toFixed(2)}" cy="${y.toFixed(2)}" r="5" fill="${series.color}" stroke="#fff" stroke-width="1.5" tabindex="0"><title>${tooltip}</title></circle>`;
                }).join("");
                return `${line}${circles}`;
            };

            const xLabels = dates.map((label, index) => {
                const x = xAt(index);
                return `<text x="${x.toFixed(2)}" y="${height - 14}" text-anchor="middle" fill="#73808c" font-size="11">${label}</text>`;
            }).join("");

            const leftTicks = axisTicks(leftMin, leftMax).map((value) => {
                const y = yAt(value, leftMin, leftMax);
                return `
                    <line x1="${padding.left}" y1="${y.toFixed(2)}" x2="${width - padding.right}" y2="${y.toFixed(2)}" stroke="#eef2f3" stroke-width="1" />
                    <text x="${padding.left - 10}" y="${(y + 4).toFixed(2)}" text-anchor="end" fill="#73808c" font-size="11">${axisLabel(value, 2)}</text>
                `;
            }).join("");

            const rightTicks = axisTicks(rightMin, rightMax).map((value) => {
                const y = yAt(value, rightMin, rightMax);
                return `<text x="${width - padding.right + 10}" y="${(y + 4).toFixed(2)}" text-anchor="start" fill="#73808c" font-size="11">${axisLabel(value, 0)}</text>`;
            }).join("");

            return `
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${width} ${height}" width="100%" height="100%" preserveAspectRatio="none" role="img" aria-label="最近7天趋势图">
                    <rect x="0" y="0" width="${width}" height="${height}" fill="#fff" />
                    ${leftTicks}
                    <line x1="${padding.left}" y1="${padding.top}" x2="${padding.left}" y2="${height - padding.bottom}" stroke="#dfe6e8" stroke-width="1.2" />
                    <line x1="${width - padding.right}" y1="${padding.top}" x2="${width - padding.right}" y2="${height - padding.bottom}" stroke="#dfe6e8" stroke-width="1.2" />
                    <line x1="${padding.left}" y1="${height - padding.bottom}" x2="${width - padding.right}" y2="${height - padding.bottom}" stroke="#dfe6e8" stroke-width="1.2" />
                    ${xLabels}
                    ${rightTicks}
                    ${selectedSeries.map(seriesMarkup).join("")}
                </svg>
            `;
        },
        disposeChart() {
            return;
        },
        async syncConnectionInfo() {
            if (this.syncing) {
                return;
            }

            this.syncing = true;
            try {
                const response = await fetch("/api/basic-info/refresh", {
                    method: "POST",
                });
                const result = await response.json();
                if (!response.ok || !result.success) {
                    throw new Error(result.message || "连接信息刷新失败");
                }

                const stats = result.stats;
                this.showToast(
                    `刷新完成：新增 ${stats.inserted_rows} 条，更新 ${stats.updated_existing_rows} 条，淘汰 ${stats.obsoleted_rows} 条，恢复 ${stats.reactivated_rows} 条`
                );
                await this.loadOperationOptions();
                await this.loadBasicInfo();
            } catch (error) {
                this.showToast(`连接信息刷新失败：${error.message}`);
            } finally {
                this.syncing = false;
            }
        },
        async crawlDayData() {
            await this.startCrawlJob("crawl");
        },
        async pollScheduledBatch() {
            window.clearTimeout(this.scheduledTimer);
            try {
                const response = await fetch('/api/amazon-day-data/scheduled');
                const result = await response.json();
                if (response.ok && result.success) {
                    this.scheduledBatch = result.batch;
                    this.scheduledNames = result.site_names || {};
                    if (result.batch?.current_job && result.batch.current_job !== this.crawlJob?.id) {
                        await this.pollCrawlJob();
                    }
                }
            } catch (error) {
                // Keep the last reminder during a brief connection failure.
            } finally {
                this.scheduledTimer = window.setTimeout(() => this.pollScheduledBatch().catch(() => {}), 5000);
            }
        },
        async controlScheduledBatch(action) {
            if (this.scheduledResuming) return;
            this.scheduledResuming = true;
            try {
                const response = await fetch(`/api/amazon-day-data/scheduled/${action}`, {method: 'POST'});
                const result = await response.json();
                if (!response.ok || !result.success) throw new Error(result.message || '操作失败');
                this.showToast(action === 'stop' ? '已请求暂停整个定时批次' : '已提交待认证站点补拉，仍有验证码的站点会继续保留提醒');
                await this.pollScheduledBatch();
                await this.pollCrawlJob();
            } catch (error) {
                this.showToast(error.message);
            } finally {
                this.scheduledResuming = false;
            }
        },
        async compensateDayData() {
            await this.startCrawlJob("compensate");
        },
        async startCrawlJob(kind) {
            if (this.crawling || this.compensating) return;
            this.crawling = kind === "crawl";
            this.compensating = kind === "compensate";
            try {
                const response = await fetch(`/api/amazon-day-data/${kind}`, {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({
                        async: true, resume: true, workers: 2,
                        skip_deal: true, exclude_activity: true,
                        ...(kind === "compensate" ? {browser: "chrome"} : {}),
                    }),
                });
                const result = await response.json();
                if (!response.ok || !result.success) throw new Error(result.message || "提交失败");
                this.crawlJob = result.job;
                this.showToast("任务已提交，可在此查看进度或暂停");
                await this.pollCrawlJob();
            } catch (error) {
                this.showToast(error.message);
                this.crawling = false;
                this.compensating = false;
            }
        },
        async pollCrawlJob() {
            window.clearTimeout(this.crawlJobTimer);
            try {
                const response = await fetch("/api/amazon-day-data/jobs/latest");
                const result = await response.json();
                if (!response.ok || !result.success) throw new Error(result.message || "进度读取失败");
                const wasActive = this.crawling || this.compensating;
                this.crawlJob = result.job;
                const active = result.job && ["queued", "running", "stopping"].includes(result.job.status);
                this.crawling = !!active && result.job.kind === "crawl";
                this.compensating = !!active && result.job.kind === "compensate";
                if (active) {
                    this.crawlJobTimer = window.setTimeout(() => this.pollCrawlJob(), 3000);
                } else if (wasActive && result.job) {
                    const final = result.job.result || {};
                    this.showToast(result.job.error || `任务已${result.job.status === "finished" ? "结束" : "暂停"}，仍待处理 ${final.remaining_re_pull ?? result.job.pending ?? 0} 个`);
                    if (this.activeView === "day-data") await this.loadDayData();
                }
            } catch (error) {
                // A network interruption is not a completed crawl. Keep the
                // controls busy until status can be read again.
                this.crawlJobTimer = window.setTimeout(() => this.pollCrawlJob(), 5000);
            }
        },
        async controlCrawlJob(action) {
            if (!this.crawlJob) return;
            try {
                const response = await fetch(`/api/amazon-day-data/jobs/${this.crawlJob.id}/${action}`, {method: "POST"});
                const result = await response.json();
                if (!response.ok || !result.success) throw new Error(result.message || "操作失败");
                this.crawlJob = result.job;
                await this.pollCrawlJob();
            } catch (error) {
                this.showToast(error.message);
            }
        },
        setDayMetric(metric) {
            this.dayMetric = metric;
            this.dayPage = 1;
            this.loadDayData();
        },
        clearDayMetric() {
            this.dayMetric = "all";
            this.dayPage = 1;
            this.loadDayData();
        },
        setBasicMetric(metric) {
            this.basicMetric = metric;
            this.page = 1;
            this.loadBasicInfo();
        },
        setBasicOperation(operation) {
            this.basicOperation = operation;
            this.page = 1;
            this.loadBasicInfo();
        },
        setDayOperation(operation) {
            this.dayOperation = operation;
            this.dayPage = 1;
            this.loadDayData();
        },
        clearBasicMetric() {
            this.basicMetric = "";
            this.page = 1;
            this.loadBasicInfo();
        },
        setStockMetric(metric) {
            this.stockMetric = metric;
            this.stockPage = 1;
            this.loadStockData();
        },
        clearStockMetric() {
            this.stockMetric = "";
            this.stockPage = 1;
            this.loadStockData();
        },
        clearTodoMetric() {
            this.todoMetric = "";
            this.loadTodoList();
        },
        scheduleSearch() {
            window.clearTimeout(this.searchTimer);
            this.searchTimer = window.setTimeout(() => {
                this.page = 1;
                this.loadBasicInfo();
            }, 280);
        },
        scheduleDaySearch() {
            window.clearTimeout(this.daySearchTimer);
            this.daySearchTimer = window.setTimeout(() => {
                this.dayPage = 1;
                this.loadDayData();
            }, 280);
        },
        scheduleStockSearch() {
            window.clearTimeout(this.stockSearchTimer);
            this.stockSearchTimer = window.setTimeout(() => {
                this.stockPage = 1;
                this.loadStockData();
            }, 280);
        },
        clearSearch() {
            this.search = "";
            this.basicOperation = "";
            this.page = 1;
            this.loadBasicInfo();
        },
        clearDaySearch() {
            this.daySearch = "";
            this.dayOperation = "";
            this.dayMetric = "all";
            this.dayPage = 1;
            this.loadDayData();
        },
        clearStockSearch() {
            this.stockSearch = "";
            this.stockPage = 1;
            this.loadStockData();
        },
        changePage(nextPage) {
            if (nextPage < 1 || nextPage > this.totalPages || this.loading) {
                return;
            }
            this.page = nextPage;
            this.loadBasicInfo();
        },
        changePageSize() {
            this.page = 1;
            this.loadBasicInfo();
        },
        changeDayPage(nextPage) {
            if (nextPage < 1 || nextPage > this.dayTotalPages || this.dayLoading) {
                return;
            }
            this.dayPage = nextPage;
            this.loadDayData();
        },
        changeDayPageSize() {
            this.dayPage = 1;
            this.loadDayData();
        },
        changeStockPage(nextPage) {
            if (nextPage < 1 || nextPage > this.stockTotalPages || this.stockLoading) {
                return;
            }
            this.stockPage = nextPage;
            this.loadStockData();
        },
        changeStockPageSize() {
            this.stockPage = 1;
            this.loadStockData();
        },
        changeDayDate() {
            this.dayPage = 1;
            this.dayMetric = "all";
            this.loadDayData();
        },
        async selectMenu(item) {
            this.activeView = item.key;
            this.closeDetail();
            if (item.key === "data-overview") {
                this.loadOverview();
            }
            if (item.key === "basic-info" && this.rows.length === 0) {
                await this.loadOperationOptions();
                this.loadBasicInfo();
            }
            if (item.key === "day-data" && this.dayRows.length === 0) {
                await this.loadOperationOptions();
                this.loadDayData();
            }
            if (item.key === "stock-data" && this.stockRows.length === 0) {
                this.loadStockData();
            }
            if (item.key === "todo-list" && this.todoRows.length === 0) {
                this.loadTodoList();
            }
        },
        openDetail(row, columns = this.columns, isDayData = false) {
            this.detailRow = row;
            this.detailColumns = columns;
            this.detailIsDayData = isDayData;
            this.detailIsStockData = false;
            this.detailEditing = false;
            this.detailForm = {};
            this.detailSaving = false;
            this.detailSaveError = "";
            this.detailTrend = null;
            this.trendMarkup = "";
            this.selectedTrendMetrics = this.trendSeries.map((series) => series.key);
            this.trendErrorMessage = "";
            if (this.detailIsDayData) {
                this.$nextTick(() => {
                    this.loadDetailTrend(row);
                });
            }
        },
        openEdit(row) {
            this.detailRow = row;
            this.detailColumns = [];
            this.detailIsDayData = false;
            this.detailIsStockData = false;
            this.detailEditing = true;
            this.detailForm = { ...row };
            this.detailSaving = false;
            this.detailSaveError = "";
            this.detailTrend = null;
            this.trendMarkup = "";
            this.trendErrorMessage = "";
        },
        async saveDetail() {
            if (!this.detailRow || !this.detailRow.id || this.detailSaving) {
                return;
            }

            this.detailSaving = true;
            this.detailSaveError = "";
            const payload = {};
            this.editableFields.forEach((field) => {
                payload[field.key] = this.detailForm[field.key] ?? "";
            });

            try {
                const response = await fetch(`/api/basic-info/${this.detailRow.id}`, {
                    method: "PATCH",
                    headers: {
                        "Content-Type": "application/json",
                    },
                    body: JSON.stringify(payload),
                });
                const result = await response.json();
                if (!response.ok || !result.success) {
                    throw new Error(result.message || "保存失败");
                }

                const index = this.rows.findIndex((item) => String(item.id) === String(this.detailRow.id));
                if (index !== -1 && result.row) {
                    this.rows[index] = { ...this.rows[index], ...result.row };
                }
                this.detailSaving = false;
                this.closeDetail();
                this.showToast("商品信息保存成功");
                await this.loadBasicInfo();
            } catch (error) {
                this.detailSaveError = `保存失败：${error.message}`;
                this.showToast(error.message);
            } finally {
                this.detailSaving = false;
            }
        },
        closeDetail() {
            if (this.detailSaving) {
                return;
            }
            this.detailRow = null;
            this.detailColumns = [];
            this.detailIsDayData = false;
            this.detailIsStockData = false;
            this.detailEditing = false;
            this.detailForm = {};
            this.detailSaving = false;
            this.detailSaveError = "";
            this.detailTrend = null;
            this.trendMarkup = "";
            this.trendErrorMessage = "";
            this.stockDetailRows = [];
            this.stockDetailLoading = false;
            this.stockDetailErrorMessage = "";
        },
        showComingSoon(label, row) {
            this.showToast(`${label}功能暂未接入，当前商品：${row.name || row.asin || row.asin_country}`);
        },
        showToast(message) {
            this.toastMessage = message;
            window.clearTimeout(this.toastTimer);
            this.toastTimer = window.setTimeout(() => {
                this.toastMessage = "";
            }, 3500);
        },
        handleKeydown(event) {
            if (event.key === "Escape") {
                if (this.todoCreateOpen) {
                    this.closeTodoCreate();
                    return;
                }
                this.closeDetail();
            }
        },
        handleResize() {
            return;
        },
    },
});

app.config.compilerOptions.delimiters = ["[[", "]]"];
app.mount("#app");
