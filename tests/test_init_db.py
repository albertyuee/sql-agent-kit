"""scripts/init_db.py 的测试。

用标准库 unittest，不引入额外依赖。运行：
    python -m unittest discover -s tests -v
"""
import contextlib
import importlib.util
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
TABLES_YAML = REPO_ROOT / "data" / "tables.yaml"


def load_init_db():
    """从路径加载 scripts/init_db.py —— scripts 不是包，只能显式加载。"""
    spec = importlib.util.spec_from_file_location(
        "init_db", SCRIPTS_DIR / "init_db.py"
    )
    module = importlib.util.module_from_spec(spec)
    # 必须先注册进 sys.modules：模块用了 `from __future__ import annotations`，
    # dataclasses 解析字符串注解时要去 sys.modules 里找这个模块。
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def whitelisted_tables() -> set[str]:
    """仓库白名单是唯一事实来源，测试不该自己再抄一份表名。"""
    with open(TABLES_YAML, encoding="utf-8") as f:
        return set(yaml.safe_load(f)["allowed_tables"])


class BuildSQLiteTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "local.db"

    def test_creates_every_whitelisted_table(self):
        init_db = load_init_db()

        init_db.build_sqlite(self.db_path)

        conn = sqlite3.connect(self.db_path)
        self.addCleanup(conn.close)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        actual = {r[0] for r in rows}

        self.assertEqual(
            whitelisted_tables() - actual,
            set(),
            "白名单里的表没有被建出来",
        )


class SeedDataTests(unittest.TestCase):
    """种子数据要能直接撑起分析类图表，不能只是几张空表。"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls._tmp.name) / "local.db"
        load_init_db().build_sqlite(cls.db_path)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def query(self, sql, params=()):
        conn = sqlite3.connect(self.db_path)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    # --- 每一张表都要有数据 ---------------------------------------------

    def test_every_table_has_rows(self):
        empty = []
        for table in sorted(whitelisted_tables()):
            (count,) = self.query(f'SELECT COUNT(*) FROM "{table}"')[0]
            if count == 0:
                empty.append(table)

        self.assertEqual(empty, [], "这些表建出来了但没有种子数据")

    # --- 引用完整性 ------------------------------------------------------

    def test_order_items_reference_existing_orders(self):
        orphans = self.query(
            "SELECT COUNT(*) FROM order_items oi "
            "LEFT JOIN orders o ON o.id = oi.order_id "
            "WHERE o.id IS NULL"
        )[0][0]

        self.assertEqual(orphans, 0, "order_items 里有指向不存在订单的明细")

    def test_order_items_reference_existing_products(self):
        orphans = self.query(
            "SELECT COUNT(*) FROM order_items oi "
            "LEFT JOIN products p ON p.id = oi.product_id "
            "WHERE p.id IS NULL"
        )[0][0]

        self.assertEqual(orphans, 0, "order_items 里有指向不存在商品的明细")

    def test_orders_reference_existing_users(self):
        orphans = self.query(
            "SELECT COUNT(*) FROM orders o "
            "LEFT JOIN users u ON u.id = o.user_id "
            "WHERE u.id IS NULL"
        )[0][0]

        self.assertEqual(orphans, 0, "orders 里有指向不存在用户的订单")

    def test_products_reference_existing_categories(self):
        orphans = self.query(
            "SELECT COUNT(*) FROM products p "
            "LEFT JOIN categories c ON c.id = p.category_id "
            "WHERE c.id IS NULL"
        )[0][0]

        self.assertEqual(orphans, 0, "products 里有指向不存在分类的商品")

    def test_user_behavior_and_reviews_reference_real_entities(self):
        bad_behavior = self.query(
            "SELECT COUNT(*) FROM user_behavior b "
            "LEFT JOIN users u ON u.id = b.user_id "
            "LEFT JOIN products p ON p.id = b.product_id "
            "WHERE u.id IS NULL OR (b.product_id IS NOT NULL AND p.id IS NULL)"
        )[0][0]
        bad_reviews = self.query(
            "SELECT COUNT(*) FROM product_reviews r "
            "LEFT JOIN products p ON p.id = r.product_id "
            "LEFT JOIN users u ON u.id = r.user_id "
            "WHERE p.id IS NULL OR u.id IS NULL"
        )[0][0]

        self.assertEqual(bad_behavior, 0, "user_behavior 有悬空引用")
        self.assertEqual(bad_reviews, 0, "product_reviews 有悬空引用")

    # --- 数据形状要能出图 ------------------------------------------------

    def test_orders_span_twelve_distinct_months(self):
        months = self.query(
            "SELECT DISTINCT strftime('%Y-%m', created_at) FROM orders"
        )

        self.assertEqual(len(months), 12, "订单没有覆盖 12 个月，趋势图会缺数据点")

    def test_purchase_funnel_narrows_monotonically(self):
        counts = dict(
            self.query(
                "SELECT action, COUNT(*) FROM user_behavior "
                "WHERE action IN ('view_product','add_cart','purchase') "
                "GROUP BY action"
            )
        )

        self.assertIn("view_product", counts)
        self.assertIn("add_cart", counts)
        self.assertIn("purchase", counts)
        self.assertGreater(counts["view_product"], counts["add_cart"], "漏斗第二步没有收窄")
        self.assertGreater(counts["add_cart"], counts["purchase"], "漏斗第三步没有收窄")

    def test_categories_are_not_evenly_split(self):
        """各分类销量要有明显差异，否则排名和对比图没有意义。"""
        counts = [
            c for (c,) in self.query(
                "SELECT COUNT(*) FROM order_items oi "
                "JOIN products p ON p.id = oi.product_id "
                "GROUP BY p.category_id"
            )
        ]

        self.assertGreater(len(counts), 1)
        self.assertGreater(max(counts), min(counts) * 2, "各分类销量过于均匀")

    def test_inventory_log_ends_at_product_stock(self):
        """每件商品最后一条库存流水的 stock_after 要等于商品当前库存。"""
        mismatched = self.query(
            "SELECT COUNT(*) FROM products p "
            "JOIN ("
            "  SELECT product_id, stock_after FROM inventory_log il "
            "  WHERE created_at = ("
            "    SELECT MAX(created_at) FROM inventory_log "
            "    WHERE product_id = il.product_id"
            "  )"
            ") last ON last.product_id = p.id "
            "WHERE last.stock_after != p.stock"
        )[0][0]

        self.assertEqual(mismatched, 0, "库存流水与商品当前库存对不上")

    def test_reviews_are_mostly_positive_with_some_low_scores(self):
        ratings = dict(self.query("SELECT rating, COUNT(*) FROM product_reviews GROUP BY rating"))
        total = sum(ratings.values())

        self.assertEqual(set(ratings) - {1, 2, 3, 4, 5}, set(), "评分超出 1-5 星")
        self.assertGreater((ratings[4] + ratings[5]) / total, 0.6, "好评占比过低，不像真实分布")
        self.assertLess((ratings[1] + ratings[2]) / total, 0.25, "差评占比过高，不像真实分布")

    def test_ad_campaign_metrics_are_internally_consistent(self):
        bad = self.query(
            "SELECT COUNT(*) FROM ad_campaigns "
            "WHERE clicks > impressions "
            "   OR orders > clicks "
            "   OR spend > budget "
            "   OR start_date > end_date"
        )[0][0]

        self.assertEqual(bad, 0, "广告数据自相矛盾（点击>曝光 / 转化>点击 等）")

    # --- 可复现 ----------------------------------------------------------

    def test_two_builds_produce_identical_data(self):
        second = Path(self._tmp.name) / "second.db"
        load_init_db().build_sqlite(second)

        def digest(path):
            conn = sqlite3.connect(path)
            try:
                out = []
                for table in sorted(whitelisted_tables()):
                    rows = conn.execute(
                        f'SELECT * FROM "{table}" ORDER BY id'
                    ).fetchall()
                    out.append(repr(rows))
                return "\n".join(out)
            finally:
                conn.close()

        self.assertEqual(digest(self.db_path), digest(second), "两次生成的数据不一致")


class SchemaLoaderIntegrationTests(unittest.TestCase):
    """真正的验收点：仓库自己的 SchemaLoader 能从生成的库里看到全部白名单表。"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls._tmp.name) / "local.db"
        load_init_db().build_sqlite(cls.db_path)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_schema_loader_sees_all_whitelisted_tables_with_columns(self):
        sys.path.insert(0, str(REPO_ROOT))
        try:
            from sql_agent.schema.loader import SchemaLoader
            from sqlalchemy import create_engine
        finally:
            sys.path.pop(0)

        engine = create_engine(f"sqlite:///{self.db_path}")
        loaded = SchemaLoader(engine, sorted(whitelisted_tables())).load()

        self.assertEqual(
            whitelisted_tables() - set(loaded),
            set(),
            "SchemaLoader 看不到部分白名单表",
        )
        for name, meta in loaded.items():
            self.assertTrue(meta["columns"], f"{name} 没有解析出任何列")

    def test_schema_loader_resolves_foreign_keys(self):
        sys.path.insert(0, str(REPO_ROOT))
        try:
            from sql_agent.schema.loader import SchemaLoader
            from sqlalchemy import create_engine
        finally:
            sys.path.pop(0)

        engine = create_engine(f"sqlite:///{self.db_path}")
        loaded = SchemaLoader(engine, sorted(whitelisted_tables())).load()

        order_items_fks = loaded["order_items"]["foreign_keys"]
        referenced = {fk["references"] for fk in order_items_fks}

        self.assertEqual(
            referenced,
            {"orders.id", "products.id"},
            "order_items 的外键没有正确解析出来",
        )


def mysql_create_block(dump: str, table: str) -> str:
    """从 MySQL dump 里抠出某张表的 CREATE TABLE 块。"""
    match = re.search(
        rf"CREATE TABLE `{re.escape(table)}` \((.*?)\n\) ENGINE",
        dump,
        re.DOTALL,
    )
    if not match:
        raise AssertionError(f"dump 里找不到 {table} 的 CREATE TABLE")
    return match.group(1)


def mysql_column_names(dump: str, table: str) -> list[str]:
    """从 CREATE TABLE 块里抽取列名（跳过主键/外键等约束行）。"""
    names = []
    for line in mysql_create_block(dump, table).splitlines():
        line = line.strip()
        match = re.match(r"`([^`]+)`\s+\w", line)
        if match:
            names.append(match.group(1))
    return names


class MysqlDumpTests(unittest.TestCase):
    """MySQL 导出。本机没有 MySQL 实例，所以这里验证的是 dump 的结构正确性，
    以及它和 SQLite 路径出自同一份定义。"""

    @classmethod
    def setUpClass(cls):
        cls.init_db = load_init_db()
        cls.dump = cls.init_db.render_mysql_sql()

    def test_creates_every_whitelisted_table(self):
        missing = [
            t for t in sorted(whitelisted_tables())
            if f"CREATE TABLE `{t}`" not in self.dump
        ]

        self.assertEqual(missing, [], "dump 里缺少这些表的建表语句")

    def test_is_rerunnable(self):
        """每张表都要先 DROP，否则第二次导入会报表已存在。"""
        missing = [
            t for t in sorted(whitelisted_tables())
            if f"DROP TABLE IF EXISTS `{t}`" not in self.dump
        ]

        self.assertEqual(missing, [], "这些表没有 DROP TABLE IF EXISTS，脚本不可重复执行")

    def test_inserts_rows_into_every_table(self):
        missing = [
            t for t in sorted(whitelisted_tables())
            if f"INSERT INTO `{t}`" not in self.dump
        ]

        self.assertEqual(missing, [], "这些表没有 INSERT 语句，导入后是空表")

    def test_declares_foreign_keys(self):
        self.assertIn("FOREIGN KEY", self.dump, "dump 没有声明任何外键")

    def test_columns_match_the_sqlite_database(self):
        """跨方言漂移护栏：MySQL dump 的列必须和真正建出来的 SQLite 库一致。"""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db_path = Path(tmp.name) / "local.db"
        self.init_db.build_sqlite(db_path)

        conn = sqlite3.connect(db_path)
        self.addCleanup(conn.close)

        drift = {}
        for table in sorted(whitelisted_tables()):
            actual = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]
            rendered = mysql_column_names(self.dump, table)
            if actual != rendered:
                drift[table] = {"sqlite": actual, "mysql": rendered}

        self.assertEqual(drift, {}, "两种方言渲染出的列不一致")

    def test_is_pure_ascii_sql_with_utf8_preamble(self):
        """中文只应出现在注释和字符串里，且文件要声明 utf8mb4。"""
        self.assertIn("utf8mb4", self.dump, "dump 没有声明 utf8mb4，中文会乱码")


class CommandLineTests(unittest.TestCase):
    """用户实际敲的就是这两条命令，得端到端跑通。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.out = Path(self._tmp.name) / "out"

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "init_db.py"), *args],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )

    def test_sqlite_dialect_builds_a_database(self):
        db_path = self.out.with_suffix(".db")

        result = self.run_cli("--dialect", "sqlite", "--out", str(db_path))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(db_path.exists(), "命令行没有生成 SQLite 库")

    def test_mysql_dialect_writes_a_sql_file(self):
        sql_path = self.out.with_suffix(".sql")

        result = self.run_cli("--dialect", "mysql", "--out", str(sql_path))

        self.assertEqual(result.returncode, 0, result.stderr)
        content = sql_path.read_text(encoding="utf-8")
        self.assertIn("CREATE TABLE `orders`", content)


@contextlib.contextmanager
def chdir(path):
    previous = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


class AutoInitTests(unittest.TestCase):
    """启动脚本只喊一句 `init_db.py --dialect sqlite --auto`，
    由脚本自己读 .env 决定该不该动手 —— shell 和 batch 各写一遍
    解析 .env 太容易出错（CRLF、行内注释、set -e）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.init_db = load_init_db()

    def write_env(self, content: str):
        (self.root / ".env").write_text(content, encoding="utf-8")

    def auto(self):
        with chdir(self.root):
            return self.init_db.auto_init()

    def test_creates_the_sqlite_database_when_missing(self):
        self.write_env("DB_TYPE=sqlite\nDB_SQLITE_PATH=./data/local.db\n")

        code = self.auto()

        self.assertEqual(code, 0)
        self.assertTrue((self.root / "data" / "local.db").exists())

    def test_ignores_inline_comments_and_surrounding_space(self):
        self.write_env("DB_TYPE=sqlite        # mysql | postgresql | sqlite\n"
                       "  DB_SQLITE_PATH = ./data/app.db  \n")

        self.auto()

        self.assertTrue((self.root / "data" / "app.db").exists())

    def test_does_not_touch_a_non_sqlite_database(self):
        """MySQL 的脚本里有 DROP TABLE，绝不能自动往别人的库上跑。"""
        self.write_env("DB_TYPE=mysql\nDB_HOST=127.0.0.1\n")

        code = self.auto()

        self.assertEqual(code, 0)
        self.assertFalse((self.root / "data").exists(),
                         "非 sqlite 时不该生成任何东西")

    def test_is_idempotent_and_leaves_an_existing_database_alone(self):
        self.write_env("DB_TYPE=sqlite\nDB_SQLITE_PATH=./data/local.db\n")
        self.auto()
        db = self.root / "data" / "local.db"
        before = db.read_bytes()

        self.auto()

        self.assertEqual(db.read_bytes(), before, "已存在的库被覆盖了")

    def test_defaults_to_sqlite_when_db_type_is_absent(self):
        """.env 里没写 DB_TYPE 时 create_db_engine 会按 mysql 处理，
        启动脚本不该擅自建库。"""
        self.write_env("SILICONFLOW_API_KEY=sk-test\n")

        self.auto()

        self.assertFalse((self.root / "data" / "local.db").exists())


class DataRealismTests(unittest.TestCase):
    """数据要经得起看：既不能自相矛盾，也不能一眼假。"""

    @classmethod
    def setUpClass(cls):
        cls.init_db = load_init_db()
        cls._tmp = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls._tmp.name) / "local.db"
        cls.init_db.build_sqlite(cls.db_path)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def query(self, sql):
        conn = sqlite3.connect(self.db_path)
        try:
            return conn.execute(sql).fetchall()
        finally:
            conn.close()

    def test_primary_keys_are_reported_as_not_null(self):
        """SQLite 的 INTEGER PRIMARY KEY 会被 PRAGMA 报成可空，
        渲染进 prompt 就成了「id (INTEGER, 可空)」，会误导模型。"""
        nullable_pks = []
        for table in sorted(whitelisted_tables()):
            for row in self.query(f'PRAGMA table_info("{table}")'):
                _, name, _, notnull, _, pk = row
                if pk and not notnull:
                    nullable_pks.append(f"{table}.{name}")

        self.assertEqual(nullable_pks, [], "这些主键在建表语句里没写 NOT NULL")

    def test_product_names_pair_brand_with_its_own_model(self):
        """品牌和型号必须同属一家，不能随机拼出「惠普 非凡 S3」这种假货。"""
        pools = self.init_db.PRODUCT_POOLS
        mismatched = []
        for name, category_id in self.query("SELECT name, category_id FROM products"):
            brand = name.split(" ")[0]
            if brand not in pools[category_id]:
                mismatched.append(name)

        self.assertEqual(mismatched, [], "商品名里的品牌不属于它所属分类的品牌池")

    def test_product_sales_have_a_long_tail(self):
        """销量要拉开梯度。均匀分布会让排名图看起来像一条平线。"""
        units = [
            u for (u,) in self.query(
                "SELECT SUM(quantity) u FROM order_items GROUP BY product_id ORDER BY u DESC"
            )
        ]

        self.assertGreater(len(units), 5)
        median = units[len(units) // 2]
        self.assertGreater(
            units[0], median * 3,
            f"爆款只比中位商品多卖 {units[0] / median:.1f} 倍，梯度不够",
        )

    def test_ad_attributed_orders_do_not_exceed_total_orders(self):
        ad_orders = self.query("SELECT SUM(orders) FROM ad_campaigns")[0][0]
        total_orders = self.query("SELECT COUNT(*) FROM orders")[0][0]

        self.assertLessEqual(
            ad_orders, total_orders,
            f"广告带来 {ad_orders} 单，但全站只有 {total_orders} 单",
        )

    def test_ad_roi_is_in_a_believable_range(self):
        rows = self.query(
            "SELECT campaign, revenue, spend FROM ad_campaigns WHERE spend > 0"
        )

        bad = [
            (campaign, round(revenue / spend, 1))
            for campaign, revenue, spend in rows
            if not 1 <= revenue / spend <= 15
        ]

        self.assertEqual(bad, [], "这些广告活动的 ROI 不像真实投放（应在 1-15 倍之间）")


if __name__ == "__main__":
    unittest.main()
