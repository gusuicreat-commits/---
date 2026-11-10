"""一个工作表汇总检验内容，保留纯内容并以实线分隔检验类型。"""
import logging

from openpyxl import Workbook
from openpyxl.styles import Border, Side


SECTIONS = [('合同信息', 'contract'), ('卷筒参数', 'drum'), ('大车传动参数', 'travel'),
            ('控制柜参数', 'control_cabinet'), ('大车油漆参数', 'travel_paint')]


def build(ws, session, ht_code):
    from app.export.registry import GENERATORS

    ws['A1'] = '检验汇总'
    row, results = 3, []
    for name, key in SECTIONS:
        if key != 'contract':
            for cells in ws.iter_rows(min_row=row - 1, max_row=row - 1, max_col=6):
                for cell in cells:
                    cell.border = Border(bottom=Side(style='thin', color='000000'))
        ws.cell(row, 1, name)
        # 各类别只作内存缓冲，不输出额外 sheet，不复制生成器的视觉样式。
        buffer = Workbook()
        error = None
        try:
            with session.begin_nested():
                GENERATORS[key](buffer.active, session, ht_code)
            for cells in buffer.active:
                for cell in cells:
                    dest = ws.cell(row + cell.row, cell.column, cell.value)
                    dest.number_format = cell.number_format
                    dest.data_type = cell.data_type
        except Exception as exc:
            error = f'{type(exc).__name__}: {exc}'
            logging.exception('汇总分区「%s」生成失败', name)
            for cells in ws.iter_rows(min_row=row + 1, max_row=row + 2, max_col=6):
                for cell in cells:
                    cell.value = None
            ws.cell(row + 1, 1, '生成失败')
            reason = ws.cell(row + 2, 1, '原因：' + error)
            reason.data_type = 's'
        finally:
            buffer.close()
        results.append({'name': name, 'ok': error is None, 'error': error})
        row += 4
    return results
