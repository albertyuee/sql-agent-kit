#!/usr/bin/env python3
"""初始化 sql-agent-kit 的示例数据库。

表结构在这里定义一次，同时渲染成 SQLite 库和 MySQL 导入脚本，
避免两处定义随字段演进而漂移。

    python scripts/init_db.py --dialect sqlite --out data/local.db
    python scripts/init_db.py --dialect mysql  --out scripts/init_db.sql

表与列的定义与 data/schema_annotations.yaml、data/tables.yaml 保持一致。
"""
from __future__ import annotations

import argparse
import random
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any


# --------------------------------------------------------------------------
# 结构定义（唯一事实来源）
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Column:
    name: str
    type: str                       # 规范类型，见 SQLITE_TYPES / MYSQL_TYPES
    length: int | None = None       # varchar 的长度；int/date 等忽略
    nullable: bool = True
    pk: bool = False
    autoincrement: bool = True
    unique: bool = False
    default: Any = None
    fk: str | None = None           # "表名.列名"
    comment: str = ""


@dataclass(frozen=True)
class Table:
    name: str
    columns: list[Column]
    comment: str = ""


def _id() -> Column:
    return Column("id", "bigint", nullable=False, pk=True, comment="主键")


def _created_at(comment: str = "创建时间") -> Column:
    return Column("created_at", "datetime", nullable=False, comment=comment)


SCHEMA: list[Table] = [
    Table("users", [
        _id(),
        Column("name", "varchar", 50, nullable=False, comment="用户姓名"),
        Column("email", "varchar", 120, nullable=False, unique=True, comment="用户邮箱地址"),
        Column("phone", "varchar", 20, comment="用户手机号码"),
        _created_at("账号注册时间"),
    ], comment="用户基本信息表"),

    Table("categories", [
        _id(),
        Column("name", "varchar", 50, nullable=False, comment="分类名称"),
        Column("parent_id", "bigint", fk="categories.id", comment="父级分类ID，为空表示顶级分类"),
        _created_at("分类创建时间"),
    ], comment="商品分类表，支持树形层级结构"),

    Table("products", [
        _id(),
        Column("name", "varchar", 120, nullable=False, comment="商品名称"),
        Column("category_id", "bigint", nullable=False, fk="categories.id", comment="所属商品分类ID"),
        Column("price", "decimal", nullable=False, comment="商品销售价格"),
        Column("stock", "int", nullable=False, default=0, comment="商品当前库存数量"),
        _created_at("商品创建时间"),
    ], comment="商品信息表"),

    Table("orders", [
        _id(),
        Column("user_id", "bigint", nullable=False, fk="users.id", comment="下单用户ID"),
        Column("status", "varchar", 20, nullable=False, comment="订单状态"),
        Column("total_amount", "decimal", nullable=False, comment="订单总金额"),
        _created_at("订单创建时间"),
        Column("updated_at", "datetime", nullable=False, comment="订单最后更新时间"),
    ], comment="订单主表，记录用户订单基础信息"),

    Table("order_items", [
        _id(),
        Column("order_id", "bigint", nullable=False, fk="orders.id", comment="关联的订单ID"),
        Column("product_id", "bigint", nullable=False, fk="products.id", comment="关联的商品ID"),
        Column("quantity", "int", nullable=False, comment="购买商品数量"),
        Column("unit_price", "decimal", nullable=False, comment="购买时的商品单价"),
    ], comment="订单明细表，记录订单中包含的商品信息"),

    Table("ad_campaigns", [
        _id(),
        Column("channel", "varchar", 30, nullable=False, comment="广告投放渠道"),
        Column("campaign", "varchar", 100, nullable=False, comment="广告活动名称"),
        Column("start_date", "date", nullable=False, comment="投放开始日期"),
        Column("end_date", "date", nullable=False, comment="投放结束日期"),
        Column("budget", "decimal", nullable=False, comment="广告预算金额"),
        Column("spend", "decimal", nullable=False, comment="广告实际花费金额"),
        Column("impressions", "bigint", nullable=False, comment="广告曝光量"),
        Column("clicks", "int", nullable=False, comment="广告点击量"),
        Column("orders", "int", nullable=False, comment="广告带来的订单转化数"),
        Column("revenue", "decimal", nullable=False, comment="广告带来的收入金额"),
        _created_at("记录创建时间"),
    ], comment="广告投放活动效果统计表"),

    Table("user_behavior", [
        _id(),
        Column("user_id", "bigint", nullable=False, fk="users.id", comment="操作用户ID"),
        Column("session_id", "varchar", 64, nullable=False, comment="用户会话ID"),
        Column("action", "varchar", 30, nullable=False, comment="用户行为类型"),
        Column("product_id", "bigint", fk="products.id", comment="操作关联的商品ID，无关联商品时为空"),
        Column("page", "varchar", 120, comment="访问的页面路径"),
        Column("duration_s", "int", comment="页面停留时长（秒）"),
        Column("device", "varchar", 20, comment="访问设备类型"),
        _created_at("行为发生时间"),
    ], comment="用户行为日志表，记录用户在端上的操作轨迹"),

    Table("product_reviews", [
        _id(),
        Column("product_id", "bigint", nullable=False, fk="products.id", comment="评价的商品ID"),
        Column("user_id", "bigint", nullable=False, fk="users.id", comment="评价的用户ID"),
        Column("order_id", "bigint", fk="orders.id", comment="评价关联的订单ID"),
        Column("rating", "int", nullable=False, comment="评分等级（1-5星）"),
        Column("tag", "varchar", 30, comment="评价标签"),
        Column("content", "text", comment="评价具体内容，为空表示未填写文字评价"),
        _created_at("评价创建时间"),
    ], comment="商品评价表"),

    Table("inventory_log", [
        _id(),
        Column("product_id", "bigint", nullable=False, fk="products.id", comment="变更的商品ID"),
        Column("change_type", "varchar", 20, nullable=False, comment="库存变更类型"),
        Column("quantity", "int", nullable=False, comment="库存变更数量，正数增加负数减少"),
        Column("stock_after", "int", nullable=False, comment="变更后的库存数量"),
        Column("remark", "varchar", 200, comment="库存变更备注说明"),
        _created_at("库存变更时间"),
    ], comment="商品库存变更流水表"),
]


# --------------------------------------------------------------------------
# 方言渲染
# --------------------------------------------------------------------------

# 规范类型 → 各方言实际类型。varchar/decimal 需要长度参数，单独处理。
SQLITE_TYPES = {
    "bigint": "INTEGER",
    "int": "INTEGER",
    "varchar": "TEXT",
    "text": "TEXT",
    "decimal": "NUMERIC",
    "datetime": "TEXT",
    "date": "TEXT",
}


def sqlite_type(col: Column) -> str:
    return SQLITE_TYPES[col.type]


def render_sqlite(table: Table) -> str:
    """渲染一张表的 SQLite CREATE TABLE 语句。"""
    lines = []
    for col in table.columns:
        parts = [f'"{col.name}"', sqlite_type(col)]
        if col.pk:
            # NOT NULL 不能省：SQLite 的 INTEGER PRIMARY KEY 实际不可空，
            # 但不写 NOT NULL 时 PRAGMA 会报 notnull=0，
            # 渲染进 prompt 就成了「id (INTEGER, 可空)」，误导模型
            parts.append("NOT NULL PRIMARY KEY AUTOINCREMENT")
        elif not col.nullable:
            parts.append("NOT NULL")
        if col.unique:
            parts.append("UNIQUE")
        if col.default is not None:
            parts.append(f"DEFAULT {col.default}")
        lines.append("    " + " ".join(parts))

    for col in table.columns:
        if col.fk:
            ref_table, ref_col = col.fk.split(".")
            lines.append(
                f'    FOREIGN KEY ("{col.name}") REFERENCES "{ref_table}"("{ref_col}")'
            )

    body = ",\n".join(lines)
    return f'CREATE TABLE "{table.name}" (\n{body}\n);'


MYSQL_TYPES = {
    "bigint": "BIGINT",
    "int": "INT",
    "varchar": "VARCHAR({length})",
    "text": "TEXT",
    "decimal": "DECIMAL(12,2)",
    "datetime": "DATETIME",
    "date": "DATE",
}


def mysql_type(col: Column) -> str:
    return MYSQL_TYPES[col.type].format(length=col.length or 255)


def render_mysql_table(table: Table) -> str:
    """渲染一张表的 MySQL CREATE TABLE 语句。"""
    lines = []
    for col in table.columns:
        parts = [f"  `{col.name}`", mysql_type(col)]
        if col.pk:
            parts.append("NOT NULL AUTO_INCREMENT")
        else:
            parts.append("NOT NULL" if not col.nullable else "NULL")
        if col.default is not None:
            parts.append(f"DEFAULT {col.default}")
        if col.comment:
            parts.append(f"COMMENT '{col.comment}'")
        lines.append(" ".join(parts))

    lines.append(f"  PRIMARY KEY (`{_pk_name(table)}`)")

    for col in table.columns:
        if col.unique:
            lines.append(f"  UNIQUE KEY `uk_{table.name}_{col.name}` (`{col.name}`)")
        if col.fk:
            lines.append(f"  KEY `idx_{table.name}_{col.name}` (`{col.name}`)")

    for col in table.columns:
        if col.fk:
            ref_table, ref_col = col.fk.split(".")
            lines.append(
                f"  CONSTRAINT `fk_{table.name}_{col.name}` "
                f"FOREIGN KEY (`{col.name}`) REFERENCES `{ref_table}` (`{ref_col}`)"
            )

    body = ",\n".join(lines)
    charset = "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
    comment = f" COMMENT='{table.comment}'" if table.comment else ""
    return (
        f"DROP TABLE IF EXISTS `{table.name}`;\n"
        f"CREATE TABLE `{table.name}` (\n{body}\n) {charset}{comment};"
    )


def _pk_name(table: Table) -> str:
    for col in table.columns:
        if col.pk:
            return col.name
    raise ValueError(f"{table.name} 没有主键")


def _sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    escaped = str(value).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def render_mysql_insert(table: Table, rows: list[tuple], batch_size: int = 200) -> str:
    """渲染成批量 INSERT，减少导入时的往返。"""
    if not rows:
        return ""
    names = ", ".join(f"`{c.name}`" for c in table.columns)
    statements = []
    for start in range(0, len(rows), batch_size):
        batch = rows[start:start + batch_size]
        values = ",\n  ".join(
            "(" + ", ".join(_sql_literal(v) for v in row) + ")" for row in batch
        )
        statements.append(
            f"INSERT INTO `{table.name}` ({names}) VALUES\n  {values};"
        )
    return "\n".join(statements)


def render_mysql_sql(now: datetime | None = None) -> str:
    """渲染完整的 MySQL 导入脚本。"""
    now = now or datetime.combine(date.today(), time.min)
    rows_by_table = generate_seed(now)

    # 建表按依赖顺序（被引用的表在前）。每张表自带 DROP，
    # 加上 FOREIGN_KEY_CHECKS=0，导入顺序就无所谓了。
    parts = [
        "-- sql-agent-kit 示例数据库（MySQL）",
        f"-- 由 scripts/init_db.py 生成，共 {len(SCHEMA)} 张表",
        f"-- 数据基准日：{now:%Y-%m-%d}（订单覆盖此前 12 个月）",
        "-- 导入：mysql -u root -p your_database < scripts/init_db.sql",
        "",
        "SET NAMES utf8mb4;",
        "SET FOREIGN_KEY_CHECKS = 0;",
        "",
    ]

    for table in SCHEMA:
        parts.append(render_mysql_table(table))
        parts.append("")

    for table in SCHEMA:
        insert = render_mysql_insert(table, rows_by_table.get(table.name, []))
        if insert:
            parts.append(insert)
            parts.append("")

    parts.append("SET FOREIGN_KEY_CHECKS = 1;")
    parts.append("")
    return "\n".join(parts)


def build_mysql_sql(path: str | Path, now: datetime | None = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_mysql_sql(now), encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# 种子数据
# --------------------------------------------------------------------------

SEED = 20240501                     # 固定种子：同一天生成的数据完全可复现

N_USERS = 200
N_PRODUCTS = 60
N_CATEGORIES = 8
N_CAMPAIGNS = 12
N_REVIEWS = 1500
ORDERS_PER_MONTH = 250              # 再乘以月度权重，12 个月合计约 3000 单
MONTHS = 12

# 月度权重：给 618 / 双11 / 双12 抬一点，春节月压一点，
# 让趋势图不是一条直线
SEASONAL_WEIGHT = {
    1: 0.85, 2: 0.70, 3: 0.95, 4: 0.95, 5: 1.08, 6: 1.20,
    7: 1.00, 8: 1.00, 9: 1.05, 10: 1.10, 11: 1.35, 12: 1.25,
}

SURNAMES = list("赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜")
GIVEN_NAMES = [
    "伟", "芳", "娜", "敏", "静", "丽", "强", "磊", "洋", "艳",
    "勇", "军", "杰", "娟", "涛", "明", "超", "秀英", "霞", "平",
    "刚", "桂英", "文", "辉", "玲", "岩", "鹏", "宇", "晨", "悦",
]

# 分类：(id, 名称, 父级 id)。1/2/7/8 是顶级，3/4 挂在 1 下，5/6 挂在 2 下
CATEGORIES = [
    (1, "手机数码", None),
    (2, "电脑办公", None),
    (3, "智能穿戴", 1),
    (4, "手机配件", 1),
    (5, "笔记本", 2),
    (6, "外设配件", 2),
    (7, "影音娱乐", None),
    (8, "家用电器", None),
]

# 各分类的「品牌 -> 该品牌型号」素材。必须按品牌分组，
# 否则随机拼品牌和型号会造出「惠普 非凡 S3」这种不存在的商品。
# 分类体量刻意不均，排名/对比图才有信息量。
PRODUCT_POOLS = {
    1: {
        "星辰": ["Phone 12", "Phone 13 Pro", "X9"],
        "极光": ["Note 20", "K60"],
        "磐石": ["Mate 8"],
    },
    2: {
        "联想": ["ThinkBook 14", "小新 Pro"],
        "戴尔": ["XPS 13", "灵越 15"],
        "华硕": ["无畏 16", "灵耀 15"],
    },
    3: {
        "步频": ["智能手环 5", "运动手表 S2"],
        "悦跑": ["儿童手表 K3"],
        "光环": ["体脂秤 Mini"],
    },
    4: {
        "闪充": ["65W 充电器", "磁吸充电宝"],
        "磐石": ["Type-C 数据线", "钢化膜", "手机壳"],
    },
    5: {
        "联想": ["小新 Air 14", "拯救者 Y7000"],
        "惠普": ["战 66", "星 15"],
        "宏碁": ["非凡 S3", "暗影骑士 5"],
    },
    6: {
        "罗技": ["机械键盘 K8", "无线鼠标 M3"],
        "雷蛇": ["电竞耳机 H7"],
        "达尔优": ["4K 显示器 27", "USB 扩展坞"],
    },
    7: {
        "声阔": ["降噪耳机 A40", "蓝牙音箱 X3"],
        "漫步者": ["回音壁 B2"],
        "索尼": ["直播麦克风 M1"],
    },
    8: {
        "美的": ["空气炸锅 5L", "破壁机"],
        "九阳": ["电饭煲 4L"],
        "苏泊尔": ["扫地机器人 T30", "加湿器 3L"],
    },
}

CATEGORY_PRICE_BAND = {
    1: (1299, 6999), 2: (2499, 8999), 3: (199, 1499), 4: (19, 299),
    5: (2999, 9999), 6: (49, 899), 7: (199, 2499), 8: (199, 2999),
}

# 分类体量权重：手机数码最大，家用电器最小
CATEGORY_WEIGHTS = {1: 22, 2: 16, 3: 9, 4: 14, 5: 8, 6: 10, 7: 11, 8: 7}

ACTION_PAGE = {
    "view_home": "/",
    "view_product": "/product/{pid}",
    "add_cart": "/cart",
    "purchase": "/checkout/success",
}

REVIEW_TAGS = {
    5: ["质量好", "性价比高", "物流快", "做工精细"],
    4: ["性价比高", "物流快", "外观好看"],
    3: ["中规中矩", "包装一般"],
    2: ["描述不符", "发货慢"],
    1: ["描述不符", "质量差", "客服态度差"],
}

REVIEW_CONTENT = {
    5: "东西很好，和描述一致，下次还会回购。",
    4: "整体不错，性价比可以，就是物流稍慢。",
    3: "一般般吧，能用的水平，没什么惊喜。",
    2: "和图片有差距，做工也一般，有点失望。",
    1: "质量太差了，用了两天就出问题，不推荐。",
}


def _month_starts(now: datetime, count: int) -> list[datetime]:
    """返回最近 count 个月的月初时间，由旧到新。"""
    months = []
    year, month = now.year, now.month
    for _ in range(count):
        months.append(datetime(year, month, 1))
        month -= 1
        if month == 0:
            month, year = 12, year - 1
    return list(reversed(months))


def _add_months(moment: datetime, months: int) -> datetime:
    month_index = moment.month - 1 + months
    year = moment.year + month_index // 12
    month = month_index % 12 + 1
    return moment.replace(year=year, month=month)


def _random_moment(rng: random.Random, start: datetime, end: datetime) -> datetime:
    """在 [start, end) 内取一个随机时刻。"""
    span = max(int((end - start).total_seconds()), 1)
    return start + timedelta(seconds=rng.randrange(span))


def _random_moment_in_month(rng: random.Random, month_start: datetime, now: datetime) -> datetime:
    next_month = _add_months(month_start, 1)
    end = min(next_month, now)
    return _random_moment(rng, month_start, end)


def _weighted_order_count(rng: random.Random, month_start: datetime, now: datetime) -> int:
    """当月订单量 = 基准量 × 季节权重，并带一点抖动。"""
    weight = SEASONAL_WEIGHT[month_start.month]
    jitter = rng.uniform(0.9, 1.1)
    return max(int(ORDERS_PER_MONTH * weight * jitter), 1)


# --- 各表生成 --------------------------------------------------------------

def _gen_users(rng: random.Random, now: datetime) -> list[tuple]:
    rows = []
    used_emails: set[str] = set()
    for uid in range(1, N_USERS + 1):
        name = rng.choice(SURNAMES) + rng.choice(GIVEN_NAMES)
        email = f"user{uid:04d}@example.com"
        while email in used_emails:
            email = f"user{uid:04d}{rng.randrange(100)}@example.com"
        used_emails.add(email)
        phone = "1" + "".join(str(rng.randrange(10)) for _ in range(10))
        created = _random_moment(rng, now - timedelta(days=730), now - timedelta(days=30))
        rows.append((uid, name, email, phone, _fmt_dt(created)))
    return rows


def _gen_categories(rng: random.Random, now: datetime) -> list[tuple]:
    base = now - timedelta(days=760)
    return [
        (cid, name, parent, _fmt_dt(base + timedelta(days=rng.randrange(30))))
        for cid, name, parent in CATEGORIES
    ]


def _category_plan() -> list[int]:
    """按分类权重把 N_PRODUCTS 个商品分配到各分类。

    用确定性的分配而不是逐个随机抽 —— 60 次抽样噪声太大，
    抽出来各分类商品数会差不多，分类排名图就没意义了。
    """
    total = sum(CATEGORY_WEIGHTS.values())
    counts = {
        cid: int(round(N_PRODUCTS * weight / total))
        for cid, weight in CATEGORY_WEIGHTS.items()
    }
    # 四舍五入的零头补给权重最大的分类
    biggest = max(CATEGORY_WEIGHTS, key=lambda cid: CATEGORY_WEIGHTS[cid])
    counts[biggest] += N_PRODUCTS - sum(counts.values())

    plan = [cid for cid, n in counts.items() for _ in range(n)]
    return plan


def _gen_products(rng: random.Random, now: datetime) -> tuple[list[list], dict[int, float]]:
    """返回 (商品行, 各商品的畅销度权重)。

    畅销度是长尾的：少数爆款贡献大部分销量，排名图才有真实的梯度。
    """
    plan = _category_plan()
    rng.shuffle(plan)                      # 打散，避免商品 id 按分类扎堆

    rows = []
    popularity: dict[int, float] = {}
    used_names: set[str] = set()
    for pid, category_id in enumerate(plan, start=1):
        brand = rng.choice(list(PRODUCT_POOLS[category_id]))
        model = rng.choice(PRODUCT_POOLS[category_id][brand])
        name = f"{brand} {model}"
        if name in used_names:                       # 撞名就加后缀，保证名称唯一
            name = f"{name} {rng.choice(['标准版', '高配版', '青春版', 'Pro'])}"
        used_names.add(name)

        low, high = CATEGORY_PRICE_BAND[category_id]
        price = round(rng.uniform(low, high), 2)
        created = _random_moment(rng, now - timedelta(days=550), now - timedelta(days=60))
        rows.append([pid, name, category_id, price, 0, _fmt_dt(created)])

        # 帕累托分布：多数商品权重在 1 附近，少数爆款能到几十
        popularity[pid] = rng.paretovariate(1.2)

    return rows, popularity


def _gen_inventory_log(rng: random.Random, products: list[list], now: datetime) -> list[tuple]:
    """生成库存流水，并回填商品当前库存。

    先造流水再定库存（而不是反过来），这样「最后一条流水的 stock_after
    等于商品当前 stock」天然成立，不用事后对齐。
    """
    rows = []
    log_id = 1
    for product in products:
        pid = product[0]
        stock = rng.randrange(80, 400)
        moment = now - timedelta(days=rng.randrange(300, 540))

        for _ in range(rng.randrange(20, 60)):
            moment = min(moment + timedelta(hours=rng.randrange(24, 240)), now)
            if moment >= now:
                break
            change_type = rng.choices(
                ["sale", "restock", "return"], weights=[70, 25, 5], k=1
            )[0]
            if change_type == "sale":
                quantity = -rng.randrange(1, 6)
            elif change_type == "restock":
                quantity = rng.randrange(30, 120)
            else:
                quantity = rng.randrange(1, 4)

            if stock + quantity < 0:                 # 库存不能为负
                quantity = -stock
            if quantity == 0:
                continue
            stock += quantity

            remark = {
                "sale": "订单出库",
                "restock": "供应商补货",
                "return": "售后退货入库",
            }[change_type]
            rows.append((log_id, pid, change_type, quantity, stock, remark, _fmt_dt(moment)))
            log_id += 1

        product[4] = stock                           # 回填 products.stock

    return rows


def _gen_orders(
    rng: random.Random,
    now: datetime,
    product_price: dict[int, float],
    popularity: dict[int, float],
) -> list[tuple]:
    """订单主表 + 明细。金额由明细汇总，保证两表对得上。"""
    orders, items = [], []
    order_id, item_id = 1, 1

    product_ids = list(range(1, N_PRODUCTS + 1))
    product_weights = [popularity[pid] for pid in product_ids]

    for month_start in _month_starts(now, MONTHS):
        for _ in range(_weighted_order_count(rng, month_start, now)):
            created = _random_moment_in_month(rng, month_start, now)
            user_id = rng.randrange(1, N_USERS + 1)
            age_days = (now - created).days
            status = _pick_status(rng, age_days)

            lines = []
            for _ in range(rng.randrange(1, 6)):
                # 按畅销度加权，而不是均匀抽 —— 真实销量是长尾的
                product_id = rng.choices(product_ids, weights=product_weights, k=1)[0]
                if any(line[0] == product_id for line in lines):
                    continue                          # 同一订单不重复记同一商品
                quantity = rng.choices([1, 2, 3, 4], weights=[62, 24, 10, 4], k=1)[0]
                unit_price = round(rng.uniform(0.85, 1.0) * product_price[product_id], 2)
                lines.append((product_id, quantity, unit_price))

            for product_id, quantity, unit_price in lines:
                items.append((item_id, order_id, product_id, quantity, unit_price))
                item_id += 1

            total = round(sum(quantity * unit_price for _, quantity, unit_price in lines), 2)
            updated = min(created + timedelta(hours=rng.randrange(1, 240)), now)
            orders.append(
                (order_id, user_id, status, total, _fmt_dt(created), _fmt_dt(updated))
            )
            order_id += 1

    return orders, items


def _pick_status(rng: random.Random, age_days: int) -> str:
    """越久远的订单越可能已经走完流程。"""
    if age_days > 20:
        return rng.choices(
            ["completed", "cancelled", "shipped", "paid"],
            weights=[88, 7, 3, 2], k=1,
        )[0]
    if age_days > 5:
        return rng.choices(
            ["completed", "shipped", "paid", "cancelled"],
            weights=[50, 33, 12, 5], k=1,
        )[0]
    return rng.choices(
        ["pending", "paid", "shipped", "cancelled"],
        weights=[38, 37, 20, 5], k=1,
    )[0]


def _gen_user_behavior(rng: random.Random, now: datetime, n_orders: int) -> list[tuple]:
    """按漏斗生成行为日志。

    三类行为的数量显式递减，且 purchase 数量与订单数一致 ——
    这样漏斗图不会出现「下单比加购还多」这种反直觉结果。
    """
    # 倍数直接决定行为日志的体积（占生成文件的大头），够图表用即可
    counts = {
        "view_home": int(n_orders * 0.7),
        "view_product": int(n_orders * 2.6),
        "add_cart": int(n_orders * 1.5),
        "purchase": n_orders,
    }
    devices = ["mobile", "pc", "tablet"]

    rows = []
    bid = 1
    moments = sorted(
        _random_moment(rng, now - timedelta(days=365), now) for _ in range(sum(counts.values()))
    )
    cursor = 0
    for action, count in counts.items():
        for _ in range(count):
            user_id = rng.randrange(1, N_USERS + 1)
            product_id = None if action == "view_home" else rng.randrange(1, N_PRODUCTS + 1)
            page = ACTION_PAGE[action].format(pid=product_id or "")
            duration = rng.randrange(3, 300)
            device = rng.choices(devices, weights=[68, 25, 7], k=1)[0]
            session_id = f"s{rng.randrange(1, 40_000):06d}"
            rows.append((
                bid, user_id, session_id, action, product_id,
                page, duration, device, _fmt_dt(moments[cursor]),
            ))
            bid += 1
            cursor += 1
    return rows


def _gen_reviews(rng: random.Random, completed_orders: list[tuple], now: datetime) -> list[tuple]:
    rows = []
    for rid, (order_id, user_id, created) in enumerate(
        rng.sample(completed_orders, min(N_REVIEWS, len(completed_orders))), start=1
    ):
        rating = rng.choices([5, 4, 3, 2, 1], weights=[45, 30, 13, 7, 5], k=1)[0]
        product_id = rng.randrange(1, N_PRODUCTS + 1)
        tag = rng.choice(REVIEW_TAGS[rating])
        content = REVIEW_CONTENT[rating] if rng.random() < 0.7 else None
        reviewed_at = created + timedelta(days=rng.randrange(1, 20))
        rows.append((
            rid, product_id, user_id, order_id, rating, tag, content,
            _fmt_dt(min(reviewed_at, now)),
        ))
    return rows


def _gen_ad_campaigns(rng: random.Random, now: datetime, total_orders: int) -> list[tuple]:
    """广告投放效果。

    从「归因订单占全站订单的比例」倒推曝光和点击，而不是反过来 ——
    否则各活动加总出来的订单会超过全站订单总量。
    ROI 也是先定目标再反推花费，避免出现几百倍的假数字。
    """
    channels = ["douyin", "xiaohongshu", "baidu", "wechat"]
    attributed = int(total_orders * 0.35)          # 广告归因订单，不该吃掉全部订单

    weights = [rng.uniform(0.5, 1.5) for _ in range(N_CAMPAIGNS)]
    weight_sum = sum(weights)

    rows = []
    for cid in range(1, N_CAMPAIGNS + 1):
        channel = channels[(cid - 1) % len(channels)]
        start = now - timedelta(days=rng.randrange(20, 330))
        end = start + timedelta(days=rng.randrange(15, 60))

        orders = max(int(attributed * weights[cid - 1] / weight_sum), 1)
        revenue = round(orders * rng.uniform(150, 900), 2)

        spend = round(revenue / rng.uniform(2.0, 8.0), 2)    # 目标 ROI 2-8 倍
        budget = round(spend / rng.uniform(0.6, 1.0), 2)     # 预算不低于花费

        clicks = max(int(orders / rng.uniform(0.02, 0.10)), 1)
        impressions = max(int(clicks / rng.uniform(0.008, 0.05)), clicks)

        rows.append((
            cid, channel, f"{channel}-{now.year}-{cid:02d}",
            _fmt_date(start), _fmt_date(end),
            budget, spend, impressions, clicks, orders, revenue,
            _fmt_dt(now - timedelta(days=rng.randrange(0, 330))),
        ))
    return rows


def _fmt_dt(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def _fmt_date(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d")


def generate_seed(now: datetime | None = None) -> dict[str, list[tuple]]:
    """生成全部种子数据。

    返回 {表名: [行元组]}，行顺序与 SCHEMA 里的列顺序一致。
    与数据库方言无关，SQLite 和 MySQL 用的是同一份数据。

    now 默认取当天零点：同一天内反复生成结果完全一致，
    同时保证趋势图始终收在当前月。
    """
    now = now or datetime.combine(date.today(), time.min)
    rng = random.Random(SEED)

    users = _gen_users(rng, now)
    categories = _gen_categories(rng, now)
    products, popularity = _gen_products(rng, now)

    product_price = {p[0]: p[3] for p in products}
    orders, items = _gen_orders(rng, now, product_price, popularity)
    inventory = _gen_inventory_log(rng, products, now)

    completed = [
        (o[0], o[1], datetime.strptime(o[4], "%Y-%m-%d %H:%M:%S"))
        for o in orders if o[2] == "completed"
    ]
    reviews = _gen_reviews(rng, completed, now)
    behavior = _gen_user_behavior(rng, now, len(orders))
    campaigns = _gen_ad_campaigns(rng, now, len(orders))

    return {
        "users": users,
        "categories": categories,
        "products": [tuple(p) for p in products],
        "orders": orders,
        "order_items": items,
        "ad_campaigns": campaigns,
        "user_behavior": behavior,
        "product_reviews": reviews,
        "inventory_log": inventory,
    }


# --------------------------------------------------------------------------
# 建库
# --------------------------------------------------------------------------

def build_sqlite(path: str | Path) -> Path:
    """在 path 处建出示例 SQLite 库，返回该路径。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 每次都重建，保证可重复运行
    if path.exists():
        path.unlink()

    rows_by_table = generate_seed()

    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        for table in SCHEMA:
            conn.execute(render_sqlite(table))
        for table in SCHEMA:
            rows = rows_by_table.get(table.name, [])
            if not rows:
                continue
            placeholders = ", ".join("?" for _ in table.columns)
            names = ", ".join(f'"{c.name}"' for c in table.columns)
            conn.executemany(
                f'INSERT INTO "{table.name}" ({names}) VALUES ({placeholders})',
                rows,
            )
        conn.commit()
    finally:
        conn.close()
    return path


DEFAULT_SQLITE_PATH = "./data/local.db"


def resolve_env_value(key: str, default: str | None = None) -> str | None:
    """从 .env 读取一个配置项。

    解析规则和 backend/routers/config.py 保持一致：跳过注释行，
    并剥掉行内注释 —— 否则 `DB_TYPE=mysql  # mysql | postgresql`
    会被整串当成类型名。
    """
    env_path = Path(".env")
    if not env_path.exists():
        return default
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key_part, _, value = line.partition("=")
        if key_part.strip() == key:
            return value.split("#")[0].strip() or default
    return default


def try_build_sqlite(path: str | Path) -> bool:
    """建库；失败时打印人话提示并返回 False。

    不把异常直接抛出去，是因为用户看到的是 PermissionError /
    FileExistsError / "unable to open database file" 这种底层报错，
    根本想不到要去改 .env 里的 DB_SQLITE_PATH。
    """
    try:
        build_sqlite(path)
    except (OSError, sqlite3.Error) as exc:
        print(f"✗ 无法创建 SQLite 示例库：{path}")
        print(f"  原因：{type(exc).__name__}: {exc}")
        print("  请检查路径 —— .env 的 DB_SQLITE_PATH（或 --out）要写成")
        print("  ./data/local.db 这样带文件名的可写路径，不能只写到目录。")
        return False
    return True


def auto_init() -> int:
    """启动脚本用的入口：只在「确实是 sqlite 且库还不存在」时才动手。

    MySQL / PostgreSQL 一律不碰 —— scripts/init_db.sql 里有 DROP TABLE，
    自动对着用户已有的库执行是破坏性的，必须由人显式发起。
    """
    db_type = (resolve_env_value("DB_TYPE", "mysql") or "mysql").lower()

    if db_type != "sqlite":
        print(f"数据库类型为 {db_type}，不自动创建示例库。")
        print("  需要示例数据？见 README「初始化示例数据库」一节。")
        print("  注意 scripts/init_db.sql 含 DROP TABLE，别对着有真实数据的库执行。")
        return 0

    db_path = Path(
        resolve_env_value("DB_SQLITE_PATH", DEFAULT_SQLITE_PATH) or DEFAULT_SQLITE_PATH
    )
    # 必须是 is_file()：目录也满足 exists()，会把「漏写文件名」当成
    # 「库已就绪」，于是这里报成功、用户到第一次查询才撞上
    # sqlalchemy 的 unable to open database file。
    if db_path.is_file():
        print(f"SQLite 示例库已就绪：{db_path}")
        return 0

    if db_path.exists():
        print(f"✗ DB_SQLITE_PATH 指向了目录，不是数据库文件：{db_path}")
        print("  请改成带文件名的路径，例如 ./data/local.db")
        return 1

    print(f"未找到 SQLite 库，正在生成示例数据库 → {db_path}")
    if not try_build_sqlite(db_path):
        return 1

    total = sum(_row_counts(db_path).values())
    print(f"已生成 {len(SCHEMA)} 张表 / {total} 行数据")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="初始化 sql-agent-kit 的示例数据库",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  python scripts/init_db.py --dialect sqlite --out data/local.db\n"
            "  python scripts/init_db.py --dialect mysql --out scripts/init_db.sql\n"
        ),
    )
    parser.add_argument(
        "--dialect", choices=["sqlite", "mysql"], required=True,
        help="sqlite 直接建库文件；mysql 生成可导入的 .sql 脚本",
    )
    parser.add_argument(
        "--out",
        help="输出路径。sqlite 默认取 .env 的 DB_SQLITE_PATH，"
             "mysql 默认写 scripts/init_db.sql",
    )
    parser.add_argument(
        "--auto", action="store_true",
        help="启动脚本模式：读 .env，仅在「是 sqlite 且库不存在」时生成，"
             "MySQL/PostgreSQL 一律不碰",
    )
    args = parser.parse_args(argv)

    if args.auto:
        return auto_init()

    if args.dialect == "sqlite":
        out = args.out or resolve_env_value("DB_SQLITE_PATH", DEFAULT_SQLITE_PATH)
        # 手动跑也给同样的友好报错，别一个入口说人话、另一个甩 traceback
        if not try_build_sqlite(out):
            return 1
        path = Path(out)
        total = sum(_row_counts(path).values())
        print(f"已生成 SQLite 库：{path}")
        print(f"  共 {len(SCHEMA)} 张表 / {total} 行数据")
        print("  把 .env 里的 DB_TYPE 设为 sqlite、DB_SQLITE_PATH 指向该文件即可")
    else:
        path = build_mysql_sql(args.out or "scripts/init_db.sql")
        print(f"已生成 MySQL 导入脚本：{path}")
        print(f"  导入：mysql -u root -p your_database < {path}")
    return 0


def _row_counts(db_path: str | Path) -> dict[str, int]:
    """直接查刚建好的库，比再生成一遍数据便宜得多。"""
    conn = sqlite3.connect(db_path)
    try:
        return {
            table.name: conn.execute(
                f'SELECT COUNT(*) FROM "{table.name}"'
            ).fetchone()[0]
            for table in SCHEMA
        }
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
