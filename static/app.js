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
            settingsMenuExpanded: false,
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
                title_unmodified: 0,
            },
            statusTagGroups: {},
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
            orderRows: [],
            orderDates: [],
            orderLatestDay: null,
            orderAsin: "",
            orderOperation: "未央",
            orderSite: "",
            orderOperations: [],
            orderSites: [],
            orderPage: 1,
            orderPageSize: 50,
            orderTotal: 0,
            orderTotalPages: 1,
            orderLoading: false,
            orderImporting: false,
            orderError: "",
            orderImportMessage: "",
            orderImportError: false,
            orderRequestId: 0,
            orderSearchTimer: null,
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
            settingsRows: [],
            settingsTotal: 0,
            settingsPage: 1,
            settingsPageSize: 50,
            settingsTotalPages: 1,
            settingsSearch: "",
            productOperationRows: [],
            productOperationTotal: 0,
            productOperationPage: 1,
            productOperationPageSize: 50,
            productOperationTotalPages: 1,
            productOperationSearch: "",
            productOperationTypeFilter: "",
            productOperationTypes: [],
            productOperationProducts: [],
            productOperationProductSearch: "",
            productOperationProductOpen: false,
            productOperationProductIndex: -1,
            productOperationLoading: false,
            productOperationError: "",
            productOperationFormOpen: false,
            productOperationEditingId: null,
            productOperationSaving: false,
            productOperationSaveError: "",
            productOperationForm: { asin_country: "", tag_id: "", remark: "", operate_date: todayString() },
            settingsLoading: false,
            settingsErrorMessage: "",
            tagGroupFormOpen: false,
            tagGroupEditingId: null,
            tagGroupSaving: false,
            tagGroupSaveError: "",
            tagGroupForm: { group_name: "", sort_no: 0, status: 1, remark: "" },
            tagFormOpen: false,
            tagEditingId: null,
            tagSaving: false,
            tagSaveError: "",
            tagGroupOptions: [],
            tagForm: { group_id: "", tag_name: "", color: "", sort_no: 0, status: 1, remark: "" },
            tagColorHex: "#147d92",
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
            orderChartRow: null,
            orderChartStart: "",
            orderChartEnd: "",
            orderChartResult: null,
            orderChartLoading: false,
            orderChartError: "",
            orderChartRequestId: 0,
            orderChartTrigger: null,
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
                { key: "order-data", label: "订单列表", icon: "O" },
                { key: "ad-activity", label: "广告活动", icon: "A" },
                { key: "operation-logs", label: "操作日志", icon: "◷" },
                { key: "product-operation-logs", label: "商品操作日志", icon: "▤" },
                { key: "base-settings", label: "基础设置", icon: "⚙", children: [
                    { key: "tag-groups", label: "标签组" },
                    { key: "tags", label: "标签" },
                ] },
            ],
            overviewSections: [
                {
                    key: "basic",
                    label: "基本信息",
                    cards: [
                        { key: "senior_a_missing", label: "A+未创作", hint: "A+ 字段为无", view: "basic-info", metric: "senior_a_missing" },
                        { key: "deal_active", label: "活动开启", hint: "活动状态为开启", view: "basic-info", metric: "deal_active" },
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
                { key: "link", label: "商品链接" },
                { key: "country_name", label: "国家" },
                { key: "product_status", label: "产品状态" },
                { key: "inventory_status", label: "库存状态" },
                { key: "ad_status", label: "广告状态" },
                { key: "promotion_status", label: "促销状态" },
                { key: "deal_date", label: "活动开始日期" },
                { key: "deal_end_date", label: "活动结束日期" },
                { key: "stock_remark", label: "库存备注" },
            ],
            basicMetricCards: [
                { key: "senior_a_missing", label: "A+未创作", hint: "A+ 字段为无" },
                { key: "deal_active", label: "活动开启", hint: "活动状态为开启" },
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
                { key: "product_status", label: "产品状态" },
                { key: "inventory_status", label: "库存状态" },
                { key: "ad_status", label: "广告状态" },
                { key: "promotion_status", label: "促销状态" },
                { key: "title", label: "标题" },
                { key: "point", label: "五点" },
                { key: "senior_a", label: "A+" },
                { key: "group", label: "小组" },
                { key: "select_date", label: "接手日期" },
                { key: "obsolete_date", label: "淘汰日期" },
                { key: "status", label: "Status" },
                { key: "remark", label: "销售建议" },
                { key: "deal", label: "活动" },
                { key: "deal_date", label: "活动开始日期" },
                { key: "deal_end_date", label: "活动结束日期" },
                { key: "asin_country", label: "ASIN+国家" },
                { key: "link", label: "链接" },
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
                { key: "product_status", label: "产品状态", type: "tag-select", options: [] },
                { key: "inventory_status", label: "库存状态", type: "tag-select", options: [] },
                { key: "ad_status", label: "广告状态", type: "tag-select", options: [] },
                { key: "promotion_status", label: "促销状态", type: "tag-select", options: [] },
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
                { key: "deal", label: "活动", type: "text", maxlength: 100 },
                { key: "deal_date", label: "活动开始日期", type: "text", maxlength: 100 },
                { key: "deal_end_date", label: "活动结束日期", type: "text", maxlength: 100 },
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
        filteredProductOperationProducts() {
            const selected = this.productOperationProducts.find((product) => product.asin_country === this.productOperationForm.asin_country);
            const query = selected && this.productOperationProductSearch === this.productOperationProductLabel(selected)
                ? "" : this.productOperationProductSearch.trim().toLocaleLowerCase();
            return this.productOperationProducts.filter((product) =>
                [product.name, product.asin, product.country_name].some((value) => String(value || "").toLocaleLowerCase().includes(query)));
        },
        orderChartGeometry() {
            const points = this.orderChartResult?.points || [];
            const maximum = points.reduce((value, point) => Math.max(value, point.quantity), 0);
            const rawStep = Math.max(1, maximum / 4);
            const magnitude = 10 ** Math.floor(Math.log10(rawStep));
            const step = [1, 2, 5, 10].find((value) => value * magnitude >= rawStep) * magnitude;
            const width = Math.max(880, 80 + points.length * 32);
            const spacing = (width - 80) / Math.max(1, points.length);
            return {
                width,
                ticks: Array.from({ length: 5 }, (_, i) => ({ value: i * step, y: 260 - i * 55 })),
                bars: points.map((point, index) => ({
                    ...point, x: 56 + (index + 0.5) * spacing,
                    y: 260 - point.quantity / (4 * step) * 220,
                    height: point.quantity / (4 * step) * 220,
                    width: Math.min(24, spacing * 0.65),
                })),
            };
        },
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
                "order-data": "订单列表",
            };
            return titles[this.activeView] || "敬请期待";
        },
    },
    mounted() {
        this.loadOverview();
        this.loadStatusTagOptions();
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
        window.clearTimeout(this.orderSearchTimer);
        window.clearTimeout(this.toastTimer);
        window.clearTimeout(this.crawlJobTimer);
        window.clearTimeout(this.scheduledTimer);
    },
    methods: {
        async openOrderChart(row) {
            this.orderChartTrigger = document.activeElement;
            this.orderChartRow = row;
            this.orderChartStart = "";
            this.orderChartEnd = "";
            this.orderChartResult = null;
            this.orderChartError = "";
            this.$nextTick(() => this.$refs.orderChartClose?.focus());
            await this.loadOrderChart(true);
        },
        async loadOrderChart(useDefault = false) {
            if (!this.orderChartRow) return;
            const requestId = ++this.orderChartRequestId;
            this.orderChartError = "";
            if (!useDefault && (!this.orderChartStart || !this.orderChartEnd || this.orderChartStart > this.orderChartEnd)) {
                this.orderChartError = "请选择有效的开始、结束日期，且开始日期不能晚于结束日期";
                this.orderChartLoading = false;
                return;
            }
            const key = this.orderChartRow.asin_country || (this.orderChartRow.asin && this.orderChartRow.country_name
                ? `${this.orderChartRow.asin}${this.orderChartRow.country_name}` : "");
            if (!key) {
                this.orderChartError = "该商品缺少 ASIN 或国家，无法查询订单";
                return;
            }
            this.orderChartLoading = true;
            const params = new URLSearchParams({ asin_country: key });
            if (!useDefault) {
                params.set("start_date", this.orderChartStart);
                params.set("end_date", this.orderChartEnd);
            }
            try {
                const response = await fetch(`/api/order-data/history?${params}`);
                const result = await response.json();
                if (!response.ok || !result.success) throw new Error(result.message || "订单数据加载失败");
                if (requestId !== this.orderChartRequestId) return;
                this.orderChartResult = result;
                this.orderChartStart = result.start_date || "";
                this.orderChartEnd = result.end_date || "";
            } catch (error) {
                if (requestId === this.orderChartRequestId) this.orderChartError = error.message;
            } finally {
                if (requestId === this.orderChartRequestId) this.orderChartLoading = false;
            }
        },
        closeOrderChart() {
            ++this.orderChartRequestId;
            this.orderChartRow = null;
            this.orderChartResult = null;
            this.orderChartLoading = false;
            this.orderChartError = "";
            const trigger = this.orderChartTrigger;
            this.orderChartTrigger = null;
            this.$nextTick(() => { if (trigger?.isConnected) trigger.focus(); });
        },
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
        isStatusTagField(field) {
            return ["product_status", "inventory_status", "ad_status", "promotion_status"].includes(this.fieldKey(field));
        },
        statusTagFor(row, field) {
            const key = this.fieldKey(field);
            const value = row && row[key];
            if (value === null || value === undefined || String(value).trim() === "") return null;
            const group = this.statusTagGroups[key];
            const tag = (group?.tags || []).find((item) => String(item.id) === String(value));
            return tag || { id: String(value), tag_name: `未知标签 #${value}`, color: "", status: 0 };
        },
        statusTagName(row, field) {
            return this.statusTagFor(row, field)?.tag_name || "暂无";
        },
        statusTagClass(row, field) {
            const tag = this.statusTagFor(row, field);
            if (!tag) return "status-tag-empty";
            const group = this.statusTagGroups[this.fieldKey(field)];
            return Number(tag.status) === 1 && Number(group?.status) === 1 ? "" : "status-tag-disabled";
        },
        statusTagStyle(row, field) {
            return this.tagColorStyle(this.statusTagFor(row, field)?.color);
        },
        tagColorStyle(value) {
            const color = String(value || "").trim();
            let rgb = null;
            const rgbMatch = color.match(/^rgb\s*\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)$/i);
            if (rgbMatch) rgb = rgbMatch.slice(1).map((part) => Math.min(255, Number(part)));
            if (!rgb && /^#[0-9a-f]{6}$/i.test(color)) {
                rgb = [1, 3, 5].map((start) => parseInt(color.slice(start, start + 2), 16));
            }
            if (!rgb) return {};
            return {
                color: `rgb(${rgb.join(",")})`,
                borderColor: `rgb(${rgb.join(",")})`,
                backgroundColor: `rgba(${rgb.join(",")}, 0.08)`,
            };
        },
        statusTagOptions(field) {
            const key = this.fieldKey(field);
            const group = this.statusTagGroups[key];
            const currentId = String(this.detailForm[key] ?? "");
            const enabled = Number(group?.status) === 1
                ? (group.tags || []).filter((tag) => Number(tag.status) === 1).map((tag) => ({
                    ...tag,
                    value: String(tag.id),
                    label: tag.tag_name,
                }))
                : [];
            if (!currentId || enabled.some((tag) => tag.value === currentId)) return enabled;
            const current = (group?.tags || []).find((tag) => String(tag.id) === currentId);
            enabled.push({
                ...(current || { id: currentId, tag_name: `未知标签 #${currentId}` }),
                value: currentId,
                label: current
                    ? `${current.tag_name}（已停用，保留）`
                    : `标签不可用（ID: ${currentId}，保留）`,
                disabled: true,
            });
            return enabled;
        },
        handlePictureError(event) {
            const image = event.currentTarget;
            image.hidden = true;
            const fallback = image.parentElement.querySelector(".picture-fallback");
            if (fallback) {
                fallback.hidden = false;
            }
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
        async loadStatusTagOptions() {
            try {
                const response = await fetch("/api/basic-info/status-tags");
                const result = await response.json();
                if (!response.ok || !result.success) {
                    throw new Error(result.message || "状态标签加载失败");
                }
                this.statusTagGroups = result.groups || {};
            } catch (error) {
                this.statusTagGroups = {};
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
        async loadOrderData() {
            const requestId = ++this.orderRequestId;
            this.orderLoading = true;
            this.orderError = "";
            const query = new URLSearchParams({
                page: this.orderPage, page_size: this.orderPageSize,
                operation: this.orderOperation, asin: this.orderAsin, country_name: this.orderSite,
            });
            try {
                const response = await fetch(`/api/order-data?${query}`);
                const result = await response.json();
                if (!response.ok || !result.success) throw new Error(result.message || "订单列表加载失败");
                if (requestId !== this.orderRequestId) return;
                this.orderRows = result.rows;
                this.orderDates = result.dates;
                this.orderLatestDay = result.latest_day;
                this.orderTotal = result.total;
                this.orderTotalPages = result.total_pages;
            } catch (error) {
                if (requestId === this.orderRequestId) this.orderError = error.message;
            } finally {
                if (requestId === this.orderRequestId) this.orderLoading = false;
            }
        },
        orderOperationTooltip(operations) {
            return (operations || []).map((item) => `${item.operation_type}：${item.remark}`).join("\n");
        },
        async loadOrderFilters() {
            try {
                const response = await fetch("/api/order-data/filters");
                const result = await response.json();
                if (!response.ok || !result.success) throw new Error(result.message || "订单筛选项加载失败");
                this.orderOperations = result.operations;
                this.orderSites = result.sites;
            } catch (error) {
                this.showToast(error.message);
            }
        },
        filterOrders() {
            window.clearTimeout(this.orderSearchTimer);
            this.orderPage = 1;
            this.loadOrderData();
        },
        scheduleOrderSearch() {
            window.clearTimeout(this.orderSearchTimer);
            // Invalidate an older response while the new search is debounced.
            ++this.orderRequestId;
            this.orderSearchTimer = window.setTimeout(() => this.filterOrders(), 300);
        },
        clearOrderFilters() {
            this.orderAsin = "";
            this.orderOperation = "";
            this.orderSite = "";
            this.filterOrders();
        },
        triggerOrderImport() {
            if (this.orderImporting) return;
            const input = this.$refs.orderImportInput;
            if (input) {
                input.value = "";
                input.click();
            }
        },
        async importOrderData(event) {
            const file = event.target.files && event.target.files[0];
            if (!file || this.orderImporting) return;
            this.orderImportMessage = "";
            this.orderImportError = false;
            if (!file.name.toLowerCase().endsWith(".xlsx")) {
                this.orderImportMessage = "请选择 .xlsx 订单文件";
                this.orderImportError = true;
                event.target.value = "";
                return;
            }
            this.orderImporting = true;
            const data = new FormData();
            data.append("file", file);
            try {
                const response = await fetch("/api/order-data/import", { method: "POST", body: data });
                const result = await response.json();
                if (!response.ok || !result.success) throw new Error(result.message || "订单导入失败");
                const s = result.stats;
                this.orderImportMessage = `导入完成：读取 ${s.input_rows} 条订单，合并写入 ${s.inserted_rows} 条，替换旧记录 ${s.replaced_rows} 条，未匹配商品 ${s.unmatched_rows} 条订单（已保存）；日期 ${s.date_start} 至 ${s.date_end}。`;
                window.clearTimeout(this.orderSearchTimer);
                this.orderPage = 1;
                await Promise.all([this.loadOrderData(), this.loadOrderFilters()]);
            } catch (error) {
                this.orderImportMessage = `导入失败：${error.message}`;
                this.orderImportError = true;
            } finally {
                this.orderImporting = false;
                event.target.value = "";
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
                        skip_deal: true, exclude_activity: false,
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
            if (!item || item.children) return;
            this.activeView = item.key;
            this.closeDetail();
            if (item.key === "order-data") {
                this.loadOrderFilters();
                this.loadOrderData();
            }
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
            if (item.key === "product-operation-logs") {
                this.productOperationPage = 1;
                this.loadProductOperationOptions();
                this.loadProductOperationLogs();
            }
            if (item.key === "tag-groups" || item.key === "tags") {
                this.settingsPage = 1;
                this.settingsSearch = "";
                this.loadSettingsTags();
            }
        },
        toggleMenu(item) {
            if (item && item.children) {
                this.settingsMenuExpanded = !this.settingsMenuExpanded;
                return;
            }
            this.selectMenu(item);
        },
        async loadSettingsTags() {
            this.settingsLoading = true;
            this.settingsErrorMessage = "";
            const kind = this.activeView === "tag-groups" ? "tag-groups" : "tags";
            const query = new URLSearchParams({ page: this.settingsPage, page_size: this.settingsPageSize, search: this.settingsSearch });
            try {
                const response = await fetch(`/api/settings/${kind}?${query}`);
                const result = await response.json();
                if (!response.ok || !result.success) throw new Error(result.message || "列表加载失败");
                this.settingsRows = result.rows;
                this.settingsTotal = result.total;
                this.settingsTotalPages = result.total_pages;
            } catch (error) {
                this.settingsErrorMessage = error.message;
            } finally {
                this.settingsLoading = false;
            }
        },
        async loadProductOperationOptions() {
            try {
                const response = await fetch("/api/product-operation-logs/options");
                const result = await response.json();
                if (!response.ok || !result.success) throw new Error(result.message || "选项加载失败");
                this.productOperationTypes = result.types || [];
                this.productOperationProducts = result.products || [];
            } catch (error) {
                this.showToast(error.message);
            }
        },
        async loadProductOperationLogs() {
            this.productOperationLoading = true;
            this.productOperationError = "";
            const query = new URLSearchParams({
                page: this.productOperationPage,
                page_size: this.productOperationPageSize,
                search: this.productOperationSearch,
                tag_id: this.productOperationTypeFilter,
            });
            try {
                const response = await fetch(`/api/product-operation-logs?${query}`);
                const result = await response.json();
                if (!response.ok || !result.success) throw new Error(result.message || "商品操作记录加载失败");
                this.productOperationRows = result.rows;
                this.productOperationTotal = result.total;
                this.productOperationTotalPages = result.total_pages;
            } catch (error) {
                this.productOperationError = error.message;
            } finally {
                this.productOperationLoading = false;
            }
        },
        filterProductOperationLogs() {
            this.productOperationPage = 1;
            this.loadProductOperationLogs();
        },
        openProductOperationForm(row = null) {
            this.productOperationEditingId = row ? Number(row.id) : null;
            this.productOperationForm = row
                ? { asin_country: row.asin_country || "", tag_id: String(row.tag_id), remark: row.remark || "", operate_date: String(row.operate_date || "").slice(0, 10) }
                : { asin_country: "", tag_id: this.productOperationTypes[0] ? String(this.productOperationTypes[0].id) : "", remark: "", operate_date: todayString() };
            this.productOperationSaveError = "";
            const product = this.productOperationProducts.find((item) => item.asin_country === this.productOperationForm.asin_country);
            this.productOperationProductSearch = product ? this.productOperationProductLabel(product) : (row?.asin_country || "");
            this.productOperationProductOpen = false;
            this.productOperationProductIndex = -1;
            this.productOperationFormOpen = true;
        },
        productOperationProductLabel(product) {
            return `${product.name || product.asin} · ${product.asin} · ${product.country_name || ""}`;
        },
        searchProductOperationProducts() {
            this.productOperationForm.asin_country = "";
            this.productOperationProductOpen = true;
            this.productOperationProductIndex = -1;
        },
        selectProductOperationProduct(product) {
            this.productOperationForm.asin_country = product.asin_country;
            this.productOperationProductSearch = this.productOperationProductLabel(product);
            this.productOperationProductOpen = false;
            this.productOperationProductIndex = -1;
            this.productOperationSaveError = "";
        },
        handleProductOperationProductKey(event) {
            if (event.key === "Escape" && this.productOperationProductOpen) {
                event.preventDefault();
                event.stopPropagation();
                this.productOperationProductOpen = false;
            } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                event.preventDefault();
                this.productOperationProductOpen = true;
                const count = this.filteredProductOperationProducts.length;
                this.productOperationProductIndex = count
                    ? (this.productOperationProductIndex + (event.key === "ArrowDown" ? 1 : -1) + count) % count : -1;
                this.$nextTick(() => document.getElementById(`operation-product-option-${this.productOperationProductIndex}`)?.scrollIntoView({ block: "nearest" }));
            } else if (event.key === "Enter" && this.productOperationProductOpen) {
                event.preventDefault();
                const product = this.filteredProductOperationProducts[this.productOperationProductIndex];
                if (product) this.selectProductOperationProduct(product);
            }
        },
        closeProductOperationForm() {
            if (this.productOperationSaving) return;
            this.productOperationFormOpen = false;
        },
        async saveProductOperationLog() {
            if (this.productOperationSaving) return;
            if (!this.productOperationForm.asin_country) {
                this.productOperationSaveError = "请从搜索结果中选择商品";
                return;
            }
            this.productOperationSaving = true;
            this.productOperationSaveError = "";
            const editing = this.productOperationEditingId !== null;
            try {
                const response = await fetch(editing
                    ? `/api/product-operation-logs/${this.productOperationEditingId}`
                    : "/api/product-operation-logs", {
                    method: editing ? "PATCH" : "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ ...this.productOperationForm, tag_id: Number(this.productOperationForm.tag_id) }),
                });
                const result = await response.json();
                if (!response.ok || !result.success) throw new Error(result.message || "保存失败");
                this.productOperationFormOpen = false;
                await this.loadProductOperationLogs();
                this.showToast(editing ? "商品操作记录已更新" : "商品操作记录已新增");
            } catch (error) {
                this.productOperationSaveError = error.message;
            } finally {
                this.productOperationSaving = false;
            }
        },
        openTagGroupForm(row = null) {
            this.tagGroupEditingId = row ? Number(row.id) : null;
            this.tagGroupForm = row
                ? { group_name: row.group_name || "", sort_no: Number(row.sort_no || 0), status: Number(row.status), remark: row.remark || "" }
                : { group_name: "", sort_no: 0, status: 1, remark: "" };
            this.tagGroupSaveError = "";
            this.tagGroupFormOpen = true;
        },
        closeTagGroupForm() {
            if (this.tagGroupSaving) return;
            this.tagGroupFormOpen = false;
        },
        async saveTagGroup() {
            if (this.tagGroupSaving) return;
            this.tagGroupSaving = true;
            this.tagGroupSaveError = "";
            try {
                const editing = this.tagGroupEditingId !== null;
                const response = await fetch(editing ? `/api/settings/tag-groups/${this.tagGroupEditingId}` : "/api/settings/tag-groups", {
                    method: editing ? "PATCH" : "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(this.tagGroupForm),
                });
                const result = await response.json();
                if (!response.ok || !result.success) throw new Error(result.message || "保存失败");
                this.tagGroupFormOpen = false;
                await this.loadSettingsTags();
            } catch (error) {
                this.tagGroupSaveError = error.message;
            } finally {
                this.tagGroupSaving = false;
            }
        },
        async deactivateTagGroup(row) {
            if (!row || Number(row.status) === 0 || !window.confirm(`确定停用标签组“${row.group_name}”吗？记录会保留在数据库中。`)) return;
            try {
                const response = await fetch(`/api/settings/tag-groups/${row.id}`, { method: "DELETE" });
                const result = await response.json();
                if (!response.ok || !result.success) throw new Error(result.message || "停用失败");
                await this.loadSettingsTags();
            } catch (error) {
                this.showToast(error.message);
            }
        },
        async openTagForm(row = null) {
            this.tagSaveError = "";
            try {
                const response = await fetch("/api/settings/tag-groups?page=1&page_size=200");
                const result = await response.json();
                if (!response.ok || !result.success) throw new Error(result.message || "标签组加载失败");
                this.tagGroupOptions = result.rows.filter((group) => Number(group.status) === 1);
                this.tagEditingId = row ? Number(row.id) : null;
                this.tagForm = row
                    ? { group_id: String(row.group_id), tag_name: row.tag_name || "", color: row.color || "", sort_no: Number(row.sort_no || 0), status: Number(row.status), remark: row.remark || "" }
                    : { group_id: this.tagGroupOptions[0] ? String(this.tagGroupOptions[0].id) : "", tag_name: "", color: "", sort_no: 0, status: 1, remark: "" };
                const rgb = String(this.tagForm.color).match(/(?:RGB)?\s*\(?\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)?/i);
                this.tagColorHex = rgb ? `#${rgb.slice(1).map((part) => Number(part).toString(16).padStart(2, "0")).join("")}` : "#147d92";
                this.tagFormOpen = true;
            } catch (error) {
                this.showToast(error.message);
            }
        },
        closeTagForm() {
            if (this.tagSaving) return;
            this.tagFormOpen = false;
        },
        async saveTag() {
            if (this.tagSaving) return;
            this.tagSaving = true;
            this.tagSaveError = "";
            try {
                const editing = this.tagEditingId !== null;
                const response = await fetch(editing ? `/api/settings/tags/${this.tagEditingId}` : "/api/settings/tags", {
                    method: editing ? "PATCH" : "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ ...this.tagForm, color: `RGB(${parseInt(this.tagColorHex.slice(1, 3), 16)},${parseInt(this.tagColorHex.slice(3, 5), 16)},${parseInt(this.tagColorHex.slice(5, 7), 16)})` }),
                });
                const result = await response.json();
                if (!response.ok || !result.success) throw new Error(result.message || "保存失败");
                this.tagFormOpen = false;
                await this.loadSettingsTags();
            } catch (error) {
                this.tagSaveError = error.message;
            } finally {
                this.tagSaving = false;
            }
        },
        async deactivateTag(row) {
            if (!row || Number(row.status) === 0 || !window.confirm(`确定停用标签“${row.tag_name}”吗？记录会保留在数据库中。`)) return;
            try {
                const response = await fetch(`/api/settings/tags/${row.id}`, { method: "DELETE" });
                const result = await response.json();
                if (!response.ok || !result.success) throw new Error(result.message || "停用失败");
                await this.loadSettingsTags();
            } catch (error) {
                this.showToast(error.message);
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
        async openEdit(row) {
            await this.loadStatusTagOptions();
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
                const next = this.detailForm[field.key] ?? "";
                const original = this.detailRow[field.key] ?? "";
                if (String(next) !== String(original)) payload[field.key] = next;
            });
            if (!Object.keys(payload).length) {
                this.detailSaving = false;
                this.showToast("没有修改需要保存");
                return;
            }

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
            if (this.productOperationFormOpen && event.key === "Escape") {
                this.closeProductOperationForm();
                return;
            }
            if (this.orderChartRow) {
                if (event.key === "Escape") {
                    this.closeOrderChart();
                    return;
                }
                if (event.key === "Tab") {
                    const elements = this.$refs.orderChartDialog?.querySelectorAll('button:not([disabled]), input:not([disabled]), [tabindex="0"]');
                    if (elements?.length) {
                        const first = elements[0], last = elements[elements.length - 1];
                        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
                        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
                    }
                }
                return;
            }
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
