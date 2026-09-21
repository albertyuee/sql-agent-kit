from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
import os


class SchemaLoader:
    """
    从数据库读取真实的表结构（DDL 信息）
    只加载白名单中的表，避免把整个数据库暴露给模型
    """

    def __init__(self, engine: Engine, allowed_tables: list[str]):
        self._engine = engine
        self._allowed_tables = set(allowed_tables)

    def load(self) -> dict[str, dict]:
        """
        加载白名单内所有表的结构
        返回格式:
        {
          "orders": {
            "columns": [{"name": "id", "type": "BIGINT", "nullable": False}, ...]
          },
          ...
        }
        """
        inspector = inspect(self._engine)
        result = {}

        # 获取数据库中实际存在的表
        existing_tables = set(inspector.get_table_names())
        tables_to_load = self._allowed_tables & existing_tables

        for table_name in tables_to_load:
            columns = []
            for col in inspector.get_columns(table_name):
                columns.append({
                    "name": col["name"],
                    "type": str(col["type"]),
                    "nullable": col.get("nullable", True),
                    "default": str(col.get("default", "")),
                })

            # 尝试获取外键信息，辅助模型理解表关系
            foreign_keys = []
            for fk in inspector.get_foreign_keys(table_name):
                # constrained_columns / referred_columns 都是列表，
                # 直接插值会渲染成 "['id']" 这种畸形文本进到 prompt 里
                foreign_keys.append({
                    "column": ", ".join(fk["constrained_columns"]),
                    "references": (
                        f"{fk['referred_table']}."
                        f"{', '.join(fk['referred_columns'])}"
                    ),
                })

            result[table_name] = {
                "columns": columns,
                "foreign_keys": foreign_keys,
            }

        return result
