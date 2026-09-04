from supportops_core.models import Base


def test_every_business_table_and_column_has_a_description_comment() -> None:
    """防止新增模型字段时只补代码、不补数据库数据字典。"""
    missing: list[str] = []
    for table in Base.metadata.sorted_tables:
        if not table.comment:
            missing.append(table.name)
        missing.extend(
            f"{table.name}.{column.name}" for column in table.columns if not column.comment
        )

    assert missing == []
