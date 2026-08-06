"""会议纪要文件解析

支持格式：
  - .docx (Word) → 用 python-docx 抽取段落 + 表格
  - .xlsx (Excel) → 用 openpyxl/pandas 把每个 sheet 转 markdown 表格
  - 直接粘贴的文本 → 原样返回

输出：统一的 markdown 风格文本（含段落 + 表格），喂给 LLM 抽取数据声明
"""

import io
from typing import List


def parse_docx(file_bytes: bytes) -> str:
    """解析 Word 文档，按顺序拼接段落 + 表格"""
    try:
        from docx import Document
    except ImportError:
        raise RuntimeError("python-docx 未安装。pip install python-docx")

    doc = Document(io.BytesIO(file_bytes))

    blocks: List[str] = []
    # 按文档顺序遍历段落 + 表格（document.element.body 含两者）
    for child in doc.element.body.iter():
        tag = child.tag.split('}')[-1] if '}' in child.tag else child.tag
        if tag == 'p':
            # 段落
            text_parts = []
            for r in child.iter():
                if r.tag.endswith('}t') and r.text:
                    text_parts.append(r.text)
            text = ''.join(text_parts).strip()
            if text:
                blocks.append(text)
        elif tag == 'tbl':
            # 表格 — 转 markdown
            rows = []
            for row in child.findall('.//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}tr'):
                cells = []
                for cell in row.findall('.//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}tc'):
                    cell_text_parts = []
                    for r in cell.iter():
                        if r.tag.endswith('}t') and r.text:
                            cell_text_parts.append(r.text)
                    cells.append(''.join(cell_text_parts).strip())
                if cells:
                    rows.append(cells)
            if rows:
                blocks.append(_rows_to_markdown(rows))

    # 去重相邻重复（python-docx 有时把表格内容也算到段落里）
    out = []
    last = None
    for b in blocks:
        if b != last:
            out.append(b)
            last = b
    return '\n\n'.join(out)


def parse_xlsx(file_bytes: bytes) -> str:
    """解析 Excel，每个 sheet 转 markdown 表格"""
    import pandas as pd

    blocks: List[str] = []
    excel = pd.ExcelFile(io.BytesIO(file_bytes))
    for sheet_name in excel.sheet_names:
        df = excel.parse(sheet_name)
        if df.empty:
            continue
        blocks.append(f"## Sheet: {sheet_name}")

        # DataFrame → list of rows
        header = [str(c) for c in df.columns]
        rows = [header]
        for _, r in df.iterrows():
            rows.append([_fmt_cell(v) for v in r.values])
        blocks.append(_rows_to_markdown(rows))
    return '\n\n'.join(blocks)


def _fmt_cell(v) -> str:
    """单元格值格式化"""
    if v is None:
        return ''
    s = str(v)
    if s == 'nan' or s == 'NaT':
        return ''
    # 数字保留原始格式
    return s.strip()


def _rows_to_markdown(rows: List[List[str]]) -> str:
    """二维表 → markdown 表格"""
    if not rows:
        return ''
    # 头
    header = rows[0]
    md_lines = ['| ' + ' | '.join(header) + ' |']
    md_lines.append('| ' + ' | '.join(['---'] * len(header)) + ' |')
    for r in rows[1:]:
        # 补齐列数
        if len(r) < len(header):
            r = r + [''] * (len(header) - len(r))
        elif len(r) > len(header):
            r = r[:len(header)]
        md_lines.append('| ' + ' | '.join(r) + ' |')
    return '\n'.join(md_lines)


def parse_uploaded(uploaded_file) -> str:
    """streamlit UploadedFile → 文本"""
    name = (uploaded_file.name or '').lower()
    file_bytes = uploaded_file.getvalue()

    if name.endswith('.docx'):
        return parse_docx(file_bytes)
    if name.endswith('.xlsx') or name.endswith('.xlsm'):
        return parse_xlsx(file_bytes)
    if name.endswith('.txt') or name.endswith('.md'):
        # 兜底：纯文本
        try:
            return file_bytes.decode('utf-8')
        except UnicodeDecodeError:
            return file_bytes.decode('gbk', errors='ignore')

    raise ValueError(f"不支持的文件格式：{name}（仅支持 .docx / .xlsx）")
