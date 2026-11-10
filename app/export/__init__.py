"""独立查询并生成各工作表。"""

from decimal import Decimal

from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from app.dec import fmt


def finish(ws) -> None:
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="EEEEEE")
    for column in ws.columns:
        width = 12
        for cell in column:
            if isinstance(cell.value, Decimal):
                cell.number_format = "0.00"
            # 文本始终作为文字保存，避免客户名称或铭牌被解释成公式。
            if isinstance(cell.value, str):
                cell.data_type = "s"
            text = fmt(cell.value) if isinstance(cell.value, Decimal) else str(cell.value or "")
            width = max(width, sum(2 if ord(ch) > 127 else 1 for ch in text) + 2)
        ws.column_dimensions[get_column_letter(column[0].column)].width = width


def qualified_text(value) -> str:
    return "待校验" if value is None else "合格" if value == 1 else "不合格"
