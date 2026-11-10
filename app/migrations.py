"""SQLite 既有数据的幂等结构迁移。"""

from decimal import Decimal

from app.dec import fmt


def migrate_paint_primer_text(engine):
    """将 Dec2 的整数分值转换为原显示文本，不改历史判定和关联。"""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as connection:
        # 显式开启事务，使 SQLite 的 ALTER TABLE 和数据换算一起回滚。
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            columns = connection.exec_driver_sql("PRAGMA table_info(travel_paint_param)").all()
            types = {row[1]: row[2].upper() for row in columns}
            if types.get("primer_thickness_ratio") != "INTEGER":
                connection.commit()
                return
            rows = connection.exec_driver_sql(
                "SELECT id, primer_thickness_ratio FROM travel_paint_param").all()
            values = []
            for row_id, value in rows:
                if value is not None and not isinstance(value, int):
                    raise ValueError("旧底漆厚度包含非整数存储值，迁移已取消")
                values.append((None if value is None else fmt(Decimal(value) / 100), row_id))
            connection.exec_driver_sql(
                "ALTER TABLE travel_paint_param RENAME COLUMN primer_thickness_ratio "
                "TO _primer_thickness_cents")
            connection.exec_driver_sql(
                "ALTER TABLE travel_paint_param ADD COLUMN primer_thickness_ratio VARCHAR(255)")
            if values:
                connection.exec_driver_sql(
                    "UPDATE travel_paint_param SET primer_thickness_ratio = ? WHERE id = ?", values)
            connection.exec_driver_sql("ALTER TABLE travel_paint_param DROP COLUMN _primer_thickness_cents")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
