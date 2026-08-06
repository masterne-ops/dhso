"""SMB 周报 docx 渲染 — 严格按模板 36 段顺序填充

模板:data/weekly_report_template.docx
保留:模板所有 paragraph style(字号/bold/对齐)
策略:每段替换文本,保留首 run 格式;清空其他 runs
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.shared import Pt
from docx.text.paragraph import Paragraph


TEMPLATE_PATH = Path(__file__).parent.parent / 'data' / 'weekly_report_template.docx'


def insert_lines_after(anchor, lines, size_pt=9):
    """在 anchor 段落后插入多个段落(每行一段),9pt。用于客户清单逐行列出。"""
    prev = anchor
    for text in lines:
        new_p = OxmlElement('w:p')
        prev._p.addnext(new_p)
        para = Paragraph(new_p, anchor._parent)
        run = para.add_run(text)
        run.font.size = Pt(size_pt)
        prev = para


def delete_para(para):
    """删除一个段落(用于去掉用户不要的'本周'占位行)。"""
    para._p.getparent().remove(para._p)


def _walk_paragraphs(doc):
    """模板里所有非空段落 — 按模板原始顺序(顶层 P0 + 表格嵌套段落)"""
    out = []
    for p in doc.paragraphs:
        if p.text.strip():
            out.append(p)
    for t in doc.tables:
        for row in t.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    if p.text.strip():
                        out.append(p)
                for nt in cell.tables:
                    for nr in nt.rows:
                        for nc in nr.cells:
                            for np in nc.paragraphs:
                                if np.text.strip():
                                    out.append(np)
    return out


def _replace(para, new_text: str):
    """保留首 run 格式(字号/bold/颜色),替换段落文本"""
    if not para.runs:
        para.add_run(new_text)
        return
    first = para.runs[0]
    for r in list(para.runs[1:]):
        r._element.getparent().remove(r._element)
    first.text = new_text


def _pct(v, plus_sign=False, decimals=2):
    if v is None:
        return 'xx%'
    sign = '+' if v >= 0 and plus_sign else ''
    return f"{sign}{v * 100:.{decimals}f}%"


def _fmt(v, decimals=1):
    if v is None:
        return 'xx'
    return f"{v:,.{decimals}f}"


def _join_cities(cities, fallback='无'):
    return '，'.join(cities) if cities else fallback


def render_weekly_report_docx(data: dict) -> BytesIO:
    """根据 gather_weekly_data 结果渲染周报 docx,返回 BytesIO"""
    doc = Document(str(TEMPLATE_PATH))
    paras = _walk_paragraphs(doc)

    meta = data['_meta']
    si  = data.get('ch1_si_overall', {})
    so  = data.get('ch1_so_province', {})
    p_  = data.get('ch2_provider', {})
    np_ = data.get('ch2_np') or {}
    gd  = data.get('ch2_gaode') or {}
    focus = data.get('ch3_focus', [])
    stock = data.get('ch3_stock', {})
    dealer_未达 = data.get('ch1_dealer_so_未达100', [])
    dealer_neg  = data.get('ch1_dealer_so_yoy_neg', [])

    # 安全索引访问(模板预期 36 段:0 顶 + 35 嵌套)
    P = paras  # alias
    n = len(P)

    def set(idx, text):
        if 0 <= idx < n:
            _replace(P[idx], text)

    # ─── [0] 顶层标题 + [1] 分销商布局 + [2] 分销商签约 → 保留原文 ───
    # 段 0 已是 "【省区SMB主管Q2业务运营工作指引】"

    # ─── 段 3:省区签约目标(基于 dealer_si_snapshot 实时累计) ──
    set(3, (f"省区签约目标{_fmt(si.get('全年签约总额_万'))}万(全年),"
            f"累计达成{_fmt(si.get('累计达成_万'))}万,"
            f"年度完成率{_pct(si.get('年度完成率'))}(达成÷签约金额);"
            f"按节奏阶段任务{_fmt(si.get('累计任务_万'))}万,阶段达标率{_pct(si.get('累计完成率'))};"
            f"达标 {si.get('累计达标数', 'xx')}/{si.get('代理商总数', 'xx')} 家"
            f"(新签 {si.get('新签家数', 'xx')} 家)"
            f"(数据时点 {si.get('_数据时点', '-')})"))

    # ─── 段 4:签约目标未达成城市(累计完成率 < 100% 的城市) ──
    not_done = si.get('未达标城市') or []
    set(4, (f"签约目标未达成城市({len(not_done)} 个):"
            f"{_join_cities(not_done)}(较上周增加xx市,减少xx市)"))

    # ─── 段 5:拓新目标城市 — 等 P1 拓新名单数据 ──
    set(5, "拓新目标城市:[等用户给拓新名单数据],拓新未达成城市:[等数据](较上周变化:xx)")

    # ─── 段 6:省区SO 标题(保留)── (索引 6 = "省区SO" 大类)

    # ─── 段 7:省区SO 总览(本周新增 + 周环比)──
    set(7, (f"省区SO目标{_fmt(so.get('年度目标_万'), 0)}万,"
            f"SO累计达成{_fmt(so.get('YTD达成_万'))}万,"
            f"累计完成{_pct(so.get('YTD完成率'))},"
            f"同比{_pct(so.get('YTD同比'), plus_sign=True)};"
            f"本月达成{_fmt(so.get('本月达成_万'))}万,"
            f"本月同比{_pct(so.get('本月同比'), plus_sign=True)};"
            f"本周+{_fmt(so.get('本周新增_万'))}万"
            f"(上周{_fmt(so.get('上周新增_万'))}万,周环比{_pct(so.get('周环比'), plus_sign=True)})"))

    # ─── 段 8:SO 未达 100% 城市 ──
    set(8, (f"截止上月SO进度条未达成100%的城市:"
            f"{_join_cities(data.get('ch1_so_not_达标_cities'))}"
            f"(较上周变化:xx)"))

    # ─── 段 9:SO 同比负增长城市 ──
    set(9, (f"SO累计同比负增长的城市:"
            f"{_join_cities(data.get('ch1_so_yoy_negative_cities'))}"
            f"(较上周变化:xx)"))

    # ─── 段 10:客户SO 标题(保留)──

    # ─── 段 11:未达 100% 客户 — 引导句 + 每客户一行(26年新客户标注) ──
    set(11, f"截止上月SO进度条未达成100%的客户数:{len(dealer_未达)}家,重点关注:")
    if dealer_未达:
        lines = []
        for r in dealer_未达:
            tag = "（26年新客户）" if r.get('是否新签') == 'Y' else ""
            lines.append(f"{r['客户']}({r['所在城市']}/{r['业务员']}/{_pct(r['SO完成率'])}){tag}")
        insert_lines_after(P[11], lines)

    # ─── 段 12:同比负增长客户 — 授牌统计 + 每客户一行 ──
    pai_n = sum(1 for r in dealer_neg if r.get('分销商认证') == '授牌客户')
    set(12, f"SO累计同比负增长的客户数:{len(dealer_neg)}家（其中授牌客户{pai_n}家）:")
    if dealer_neg:
        lines = []
        for r in dealer_neg:
            tag = "（授牌客户）" if r.get('分销商认证') == '授牌客户' else ""
            lines.append(f"{r['客户']}({r['所在城市']}/{r['业务员']}/{_pct(r['SO同比'], plus_sign=True)}){tag}")
        insert_lines_after(P[12], lines)

    # ─── 段 13:下周改进计划 标题(保留)──

    # ─── 段 14:二、服务商管理 标题(25 年整体签约/激活为固定历史数)──
    set(14, "二、服务商管理 【25年整体签约8306家、激活7174家(≥1000元)】")

    # ─── 段 15:26 年签约目标(provider_contract 全量)──
    set(15, (f"1、26年服务商签约目标:{p_.get('签约目标', 'xx'):,}家,"
             f"累计达成:{p_.get('签约达成', 'xx'):,}家"
             f"(其中本年新签{p_.get('本年新签', 'xx')}家)"
             f"本月新签:{p_.get('本月新签', 'xx')}家、"
             f"本周:{p_.get('本周新签', 'xx')}家"))

    # ─── 段 16:26 年激活目标(provider_contract.是否激活 = 'Y')──
    set(16, (f"26年服务商激活目标:{p_.get('激活目标', 'xx'):,}家,"
             f"累计达成:{p_.get('累计激活', 'xx'):,}家"))

    # ─── 段 17:存量激活(是否新签=N AND 是否激活=Y)──
    set(17, (f"存量激活:{p_.get('存量激活', 'xx')}家,"
             f"本月:{p_.get('本月存量激活', 'xx')}家,"
             f"本周:{p_.get('本周存量激活', 'xx')}家"))

    # ─── 段 18:新增激活(是否新签=Y AND 是否激活=Y)──
    set(18, (f"新增激活:{p_.get('新增激活', 'xx')}家,"
             f"本月:{p_.get('本月新增激活', 'xx')}家,"
             f"本周:{p_.get('本周新增激活', 'xx')}家"))

    # ─── 段 19:竞品 top 服务商进展(总览 + 转化 + 周变化) ──
    cp = data.get('ch2_competitor')
    if cp:
        delta = cp.get('较上周', 0)
        delta_str = f"+{delta}" if delta >= 0 else f"{delta}"
        set(19, (f"竞品top服务商进展:库内 {cp['总数']} 家(竞品体量 {cp['竞品体量_万']:,.0f} 万,"
                 f"覆盖 {cp['覆盖城市数']} 城);"
                 f"已开发(在售大华) {cp['已开发家数']} 家,开发率 {_pct(cp['已开发率'])},"
                 f"待开发 {cp['待开发家数']} 家。"
                 f"本月转化 {cp['本月转化家数']} 家上线({cp['本月转化单数']} 单 / "
                 f"{cp['本月转化金额_万']:,.1f} 万);"
                 f"本周转化 {cp['本周转化家数']} 家 / {cp['本周转化金额_万']:,.1f} 万"
                 f"(较上周{delta_str} 家)。"))
    else:
        set(19, f"竞品top服务商进展:库内 {data.get('ch2_competitor_top_count', 'xx')} 家")

    # ─── 段 20:NP流转 标题 ──

    # ─── 段 21:NP 累计流转(简化,去掉算不出的联系/识别占位)──
    if np_:
        set(21, f"累计流转{np_['识别有效流转']}家，签约{np_['已签约']}家,激活{np_['已激活']}家")
    else:
        set(21, "累计流转xx家，签约xx家,激活xx家")

    # ─── 段 22:NP 本周 — 删除(用户版无本周行)──
    delete_para(P[22])

    # ─── 段 23:高德潜客 标题 ──

    # ─── 段 24:高德累计(简化,去掉联系占位)──
    if gd:
        set(24, (f"累计下发{gd['累计下发']}家(去重后),识别有效客户{gd['识别有效']}家,"
                 f"签约{gd['已签约']}家,激活{gd['已激活']}家"))
    else:
        set(24, "累计下发xx家,识别有效客户xx家,签约xx家,激活xx家")

    # ─── 段 25:高德本周 — 删除(用户版无本周行)──
    delete_para(P[25])

    # ─── 段 26:下周改进计划 标题 ──

    # ─── 段 27:三、技术行销 标题 ──
    # ─── 段 28:产品专项过程管理 标题 ──

    # ─── 段 29:无线铺货管理(distribution_info + provider_contract 客户分类)──
    wd = data.get('ch3_wireless_distribution')
    if wd:
        def _delta(v):
            return f"+{v}" if v >= 0 else f"{v}"
        set(29, (f"省区无线铺货管理:"
                 f"累计铺货 {wd['累计铺货台数']:,} 台 / {wd['累计铺货家数']} 家服务商,"
                 f"累计上线率 {_pct(wd['累计上线率'])};"
                 f"本周新铺 {wd['本周铺货家数']} 家(较上周{_delta(wd['较上周'])} 家),"
                 f"本周铺货 {wd['本周铺货台数']} 台 上线 {wd['本周上线数']} 台,"
                 f"本周上线率 {_pct(wd['本周上线率'])}。"
                 f"夫妻店新增 {wd['本周夫妻店']} 家(较上周{_delta(wd['夫妻店较上周'])} 家),"
                 f"占比 {_pct(wd['本周夫妻店占比'])};"
                 f"批发类客户铺货增加 {wd['本周批发类']} 家(较上周{_delta(wd['批发类较上周'])} 家),"
                 f"占比 {_pct(wd['本周批发类占比'])}。"))
    else:
        set(29, "省区无线铺货管理:[等用户给铺货明细数据]")

    # ─── 段 30:呆滞品管理 ──
    if stock:
        set(30, (f"省区呆滞品管理:总计{stock.get('呆滞总台数', 'xx')}台,"
                 f"已消耗{stock.get('已消耗', 'xx')}台,消化率 {stock.get('消化率pct', 'xx')}%"))

    # ─── 段 31:三大专项SO达成 标题 ──

    # ─── 段 32-34:夜视王 / 无线 / 场景化(含本周新增 + 较上周 delta)──
    fmap = {f['专项']: f for f in focus}
    def _focus_line(f, suffix):
        delta = f.get('较上周', 0)
        delta_str = f"+{delta}" if delta >= 0 else f"{delta}"
        return (f"{f['专项']}产品SO目标{f['目标']:,}台,"
                f"累计达成{f['YTD达成']:,}台({_pct(f['YTD完成率'])}),"
                f"本月达成{f['本月达成']}台,"
                f"本周新增{f.get('本周新增', 'xx')}台(较上周{delta_str} 台){suffix}")
    if '夜视王' in fmap: set(32, _focus_line(fmap['夜视王'], ';'))
    if '无线'   in fmap: set(33, _focus_line(fmap['无线'],   ';'))
    if '场景化' in fmap: set(34, _focus_line(fmap['场景化'], '。'))

    # ─── 段 35:下周改进计划 (保留)──
    # ─── 段 36:四、其他工作 (保留)──

    # ─── 在文档末尾追加生成元数据(独立段落)──
    p_meta = doc.add_paragraph()
    run = p_meta.add_run(
        f"——— 数据周期 {meta['week_start']} ~ {meta['week_end']} · "
        f"360 月报快照 {meta['snapshot_period']} · "
        f"生成 {meta['generated_at']} ——"
    )
    run.italic = True
    from docx.shared import RGBColor
    run.font.color.rgb = RGBColor(0x88, 0x88, 0x88)

    # ─── 保存 ───
    buf = BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf
