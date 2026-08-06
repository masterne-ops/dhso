#!/usr/bin/env python3
"""市场推广费用与 SO 效率专题。"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
from _auth import require_auth  # noqa: E402
from _loaders import DB_PATH  # noqa: E402
from _marketing_roi import (  # noqa: E402
    SOURCE_LABELS,
    available_snapshots,
    import_marketing_files,
    load_snapshot_tables,
    parse_marketing_file,
    query_region_so,
    validate_reconciliation,
)


st.set_page_config(page_title="市场推广 ROI", page_icon="📣", layout="wide")
require_auth()


def money(value) -> str:
    return f"{float(value or 0) / 10000:,.1f}万"


def ratio(output, invest) -> float | None:
    return float(output) / float(invest) if pd.notna(invest) and float(invest) > 0 else None


def pct(value) -> str:
    return "—" if value is None or pd.isna(value) else f"{float(value) * 100:.2f}%"


def ratio_text(value) -> str:
    return "—" if value is None or pd.isna(value) else f"{float(value):.2f} 倍"


def sum_col(frame: pd.DataFrame, column: str) -> float:
    if frame.empty or column not in frame:
        return 0.0
    return float(pd.to_numeric(frame[column], errors="coerce").fillna(0).sum())


st.title("📣 市场推广费用与 SO 效率")
st.caption(
    "城市/区县、代理商、服务商是同一批费用的不同观察层级，系统分层展示，不跨层级重复相加。"
    "“产出投入比”= 关联 SO ÷ 费用；“费用/SO”= 市场费用 ÷ 同期区域 SO，均不等同于利润 ROI。"
)


with st.expander("📥 导入市场推广数据（支持六类 Excel 一次上传）", expanded=False):
    uploads = st.file_uploader(
        "选择城市、区县、一级客户、服务商门头、会议分析、参会明细文件",
        type=["xlsx", "xls"], accept_multiple_files=True, key="marketing_roi_upload",
    )
    if uploads:
        parsed = []
        preview_rows = []
        parse_errors = []
        for uploaded in uploads:
            try:
                item = parse_marketing_file(uploaded)
                parsed.append(item)
                preview_rows.append({
                    "文件": item.name, "识别类型": SOURCE_LABELS[item.source_type],
                    "快照日期": item.snapshot_date, "有效行数": item.row_count,
                    "提醒": "；".join(item.warnings) or "—",
                })
            except Exception as exc:
                parse_errors.append(f"{uploaded.name}：{exc}")
        if preview_rows:
            st.dataframe(pd.DataFrame(preview_rows), use_container_width=True, hide_index=True)
        for error in parse_errors:
            st.error(error)
        checks = validate_reconciliation(parsed)
        if checks:
            st.markdown("##### 跨文件勾稽")
            check_df = pd.DataFrame(checks)
            st.dataframe(
                check_df.style.format({"来源A": "{:,.2f}", "来源B": "{:,.2f}", "差异": "{:,.2f}"}),
                use_container_width=True, hide_index=True,
            )
            st.caption("“城市总投入与代理商已分摊费用”的差额表示尚未分摊到一级客户的费用，不作为导入失败条件。")
        if st.button("确认导入", type="primary", disabled=bool(parse_errors), key="marketing_roi_import"):
            try:
                results = import_marketing_files(uploads, DB_PATH)
                imported = sum(r["status"] == "imported" for r in results)
                skipped = sum(r["status"] == "skipped" for r in results)
                st.success(f"导入完成：{imported} 个文件写入，{skipped} 个相同文件自动跳过。")
                st.cache_data.clear()
                st.rerun()
            except Exception as exc:
                st.error(f"导入失败，数据库已回滚：{exc}")


snapshots = available_snapshots(DB_PATH)
if not snapshots:
    st.info("尚未导入市场推广专题数据。请在上方一次上传本期六类 Excel。")
    st.stop()

with st.sidebar:
    st.header("筛选")
    snapshot = st.selectbox("数据快照", snapshots, index=0)

tables = load_snapshot_tables(DB_PATH, snapshot)
region_all = tables["region"]
city_all = region_all[region_all["source_type"] == "city"].copy()
district_all = region_all[region_all["source_type"] == "district"].copy()
city_options = sorted(city_all["city"].dropna().unique().tolist())

with st.sidebar:
    selected_city = st.selectbox("地市", ["全省"] + city_options, index=0)
    district_options = [] if selected_city == "全省" else sorted(
        district_all.loc[district_all["city"] == selected_city, "district"].dropna().unique().tolist()
    )
    selected_district = st.selectbox("区县", ["全部区县"] + district_options, index=0)
    st.divider()
    st.caption(f"专题数据截至：{snapshot}")
    st.caption(f"已导入来源：{tables['batches']['source_type'].nunique()} / 6")


city_df = city_all if selected_city == "全省" else city_all[city_all["city"] == selected_city]
district_df = district_all.copy()
if selected_city != "全省":
    district_df = district_df[district_df["city"] == selected_city]
if selected_district != "全部区县":
    district_df = district_df[district_df["district"] == selected_district]

meeting_df = tables["meeting"].copy()
provider_df = tables["provider"].copy()
attendee_df = tables["attendee"].copy()
if selected_city != "全省":
    meeting_df = meeting_df[meeting_df["city"] == selected_city]
    provider_df = provider_df[provider_df["city"] == selected_city]
    selected_adsp = set(meeting_df["adsp_id"].dropna())
    attendee_df = attendee_df[attendee_df["adsp_id"].isin(selected_adsp)]
if selected_district != "全部区县":
    meeting_df = meeting_df[meeting_df["district"] == selected_district]
    selected_adsp = set(meeting_df["adsp_id"].dropna())
    attendee_df = attendee_df[attendee_df["adsp_id"].isin(selected_adsp)]

so_detail = query_region_so(DB_PATH, snapshot)
if selected_city != "全省" and not so_detail.empty:
    so_detail = so_detail[so_detail["city"] == selected_city]
if selected_district != "全部区县" and not so_detail.empty:
    so_detail = so_detail[so_detail["district"] == selected_district]

authoritative_region = district_df if selected_district != "全部区县" else city_df
total_invest = sum_col(authoritative_region, "total_invest")
total_so = sum_col(so_detail, "so_ytd")
meeting_invest = sum_col(meeting_df, "meeting_expense")
meeting_output = sum_col(meeting_df, "post_redpack_output")
storefront_invest = sum_col(provider_df, "storefront_invest")
storefront_output = sum_col(provider_df, "annual_redpack_output")
if selected_district != "全部区县":
    # 服务商门头明细源表没有区县字段，区县总览使用区县汇总文件中的可勾稽口径。
    storefront_invest = sum_col(district_df, "provider_storefront_invest")
    storefront_output = sum_col(district_df, "provider_storefront_output")

tabs = st.tabs(["总览", "城市 / 区县", "代理商", "推广会", "门头服务商", "数据质量"])

with tabs[0]:
    scope = selected_district if selected_district != "全部区县" else selected_city
    st.subheader(f"{scope}市场推广效率总览")
    metrics = st.columns(6)
    metrics[0].metric("市场费用", money(total_invest), help="来自城市或区县费用快照；不会叠加代理商/服务商明细")
    metrics[1].metric("同期区域 SO", money(total_so), help="product_flow_v 最新分销价，当年1月1日至快照日")
    metrics[2].metric("费用 / SO", pct(ratio(total_invest, total_so)), help="市场费用占同期区域SO的比例，不是归因ROI")
    metrics[3].metric("推广会费用", money(meeting_invest))
    metrics[4].metric("推广会产出投入比", ratio_text(ratio(meeting_output, meeting_invest)), help="会后30天安装红包关联SO ÷ 会议申请费用")
    metrics[5].metric("门头产出投入比", ratio_text(ratio(storefront_output, storefront_invest)), help="当年安装红包关联SO ÷ 服务商门头费用")

    st.markdown("##### 费用结构")
    structure = pd.DataFrame({
        "费用类型": ["领料投入", "积分商城礼品", "终端推广"],
        "金额": [sum_col(authoritative_region, "material_invest"), sum_col(authoritative_region, "gift_invest"), sum_col(authoritative_region, "terminal_invest")],
    }).set_index("费用类型")
    st.bar_chart(structure, y="金额", horizontal=True, height=260)
    st.caption("终端推广内部包含门头、推广会和广告等动作；下方各专题使用对应明细计算效率。")

    if selected_city == "全省":
        st.markdown("##### 地市效率排序")
        city_show = city_all.copy()
        if not so_detail.empty:
            city_so = so_detail.groupby("city", as_index=False)["so_ytd"].sum()
            city_show = city_show.merge(city_so, on="city", how="left")
        else:
            city_show["so_ytd"] = 0
        city_show["费用_SO"] = city_show.apply(lambda row: ratio(row["total_invest"], row["so_ytd"]), axis=1)
        city_show["推广会产出投入比"] = city_show.apply(lambda row: ratio(row["meeting_output_30d"], row["meeting_invest"]), axis=1)
        city_show["门头产出投入比"] = city_show.apply(lambda row: ratio(row["provider_storefront_output"], row["provider_storefront_invest"]), axis=1)
        show = city_show[["city", "total_invest", "so_ytd", "费用_SO", "meeting_invest", "推广会产出投入比", "provider_storefront_invest", "门头产出投入比"]].copy()
        show.columns = ["地市", "市场费用", "同期SO", "费用/SO", "推广会费用", "推广会产出投入比", "服务商门头费用", "门头产出投入比"]
        st.dataframe(
            show.sort_values("市场费用", ascending=False).style.format({
                "市场费用": "{:,.0f}", "同期SO": "{:,.0f}", "费用/SO": "{:.2%}",
                "推广会费用": "{:,.0f}", "推广会产出投入比": "{:.2f}",
                "服务商门头费用": "{:,.0f}", "门头产出投入比": "{:.2f}",
            }, na_rep="—"), use_container_width=True, hide_index=True,
        )

with tabs[1]:
    st.subheader("城市总表与区县下钻")
    regional = city_all if selected_city == "全省" else district_df
    group_name = "city" if selected_city == "全省" else "district"
    if regional.empty:
        st.info("当前筛选无区域数据。")
    else:
        regional = regional.copy()
        so_group = pd.DataFrame()
        if not so_detail.empty:
            so_group = so_detail.groupby(group_name, as_index=False)["so_ytd"].sum()
            regional = regional.merge(so_group, on=group_name, how="left")
        else:
            regional["so_ytd"] = 0
        regional["费用/SO"] = regional.apply(lambda row: ratio(row["total_invest"], row["so_ytd"]), axis=1)
        regional["推广会产出投入比"] = regional.apply(lambda row: ratio(row["meeting_output_30d"], row["meeting_invest"]), axis=1)
        regional["门头产出投入比"] = regional.apply(lambda row: ratio(row["provider_storefront_output"], row["provider_storefront_invest"]), axis=1)
        regional["门头激活率"] = regional.apply(lambda row: ratio(row["provider_storefront_count"] - row["zero_output_provider_count"], row["provider_storefront_count"]), axis=1)
        cols = [group_name, "total_invest", "so_ytd", "费用/SO", "meeting_count", "meeting_invest", "meeting_output_30d", "推广会产出投入比", "provider_storefront_count", "provider_storefront_invest", "provider_storefront_output", "门头产出投入比"]
        show = regional[cols].rename(columns={
            group_name: "区域", "total_invest": "市场费用", "so_ytd": "同期SO",
            "meeting_count": "会议场次", "meeting_invest": "会议费用", "meeting_output_30d": "会后30天关联SO",
            "provider_storefront_count": "门头服务商", "provider_storefront_invest": "门头费用",
            "provider_storefront_output": "门头服务商当年关联SO",
        })
        st.dataframe(
            show.sort_values("市场费用", ascending=False).style.format({
                "市场费用": "{:,.0f}", "同期SO": "{:,.0f}", "费用/SO": "{:.2%}",
                "会议费用": "{:,.0f}", "会后30天关联SO": "{:,.0f}", "推广会产出投入比": "{:.2f}",
                "门头费用": "{:,.0f}", "门头服务商当年关联SO": "{:,.0f}", "门头产出投入比": "{:.2f}",
            }, na_rep="—"), use_container_width=True, hide_index=True, height=520,
        )

with tabs[2]:
    st.subheader("代理商费用与经营效率")
    dealer = tables["dealer"].copy()
    dealer_names = None
    if selected_city != "全省":
        dealer_names = set(meeting_df["host_dealer"].dropna())
        if selected_district == "全部区县":
            dealer_names |= set(provider_df["dealer_name"].dropna())
        dealer = dealer[dealer["dealer_name"].isin(dealer_names)]
        st.caption("地市筛选通过该地市门头服务商所属代理商及推广会主办方识别；区县筛选只能使用推广会主办方，因为一级客户和服务商门头源表没有区县字段。")
    if dealer.empty:
        st.info("当前筛选无代理商费用数据。")
    else:
        dealer_agg = dealer.groupby(["dealer_code", "dealer_name"], dropna=False, as_index=False).agg(
            费用=("total_invest", "sum"), 累计业绩=("performance_ytd", "sum"),
            门头费用=("storefront_invest", "sum"), 会议费用=("circle_meeting_invest", "sum"),
        )
        dealer_agg["费用/累计业绩"] = dealer_agg.apply(lambda row: ratio(row["费用"], row["累计业绩"]), axis=1)
        meeting_dealer = meeting_df.groupby("host_dealer", as_index=False).agg(
            会议明细费用=("meeting_expense", "sum"), 会后30天关联SO=("post_redpack_output", "sum"), 会议场次=("adsp_id", "nunique")
        ).rename(columns={"host_dealer": "dealer_name"})
        storefront_dealer = provider_df.groupby("dealer_name", as_index=False).agg(
            服务商门头费用=("storefront_invest", "sum"), 门头服务商关联SO=("annual_redpack_output", "sum"), 门头服务商=("provider_code", "nunique")
        )
        dealer_agg = dealer_agg.merge(meeting_dealer, on="dealer_name", how="left").merge(storefront_dealer, on="dealer_name", how="left")
        dealer_agg["推广会产出投入比"] = dealer_agg.apply(lambda row: ratio(row["会后30天关联SO"], row["会议明细费用"]), axis=1)
        dealer_agg["门头产出投入比"] = dealer_agg.apply(lambda row: ratio(row["门头服务商关联SO"], row["服务商门头费用"]), axis=1)
        show = dealer_agg.rename(columns={"dealer_name": "代理商", "dealer_code": "代理商编码"})
        st.dataframe(
            show.sort_values("费用", ascending=False).style.format({
                "费用": "{:,.0f}", "累计业绩": "{:,.0f}", "费用/累计业绩": "{:.2%}",
                "门头费用": "{:,.0f}", "会议费用": "{:,.0f}", "会议明细费用": "{:,.0f}",
                "会后30天关联SO": "{:,.0f}", "推广会产出投入比": "{:.2f}",
                "服务商门头费用": "{:,.0f}", "门头服务商关联SO": "{:,.0f}", "门头产出投入比": "{:.2f}",
            }, na_rep="—"), use_container_width=True, hide_index=True, height=560,
        )
        st.caption("代理商“费用/累计业绩”用于看费用强度；推广会和门头分别使用可关联的安装红包 SO 计算产出投入比。")

with tabs[3]:
    st.subheader("推广会效率")
    if meeting_df.empty:
        st.info("当前筛选无推广会数据。")
    else:
        meeting_df = meeting_df.copy()
        meeting_df["产出投入比"] = meeting_df.apply(lambda row: ratio(row["post_redpack_output"], row["meeting_expense"]), axis=1)
        meeting_df["单家签到费用"] = meeting_df.apply(lambda row: ratio(row["meeting_expense"], row["signin_companies"]), axis=1)
        show = meeting_df[["activity_start", "city", "district", "activity_name", "host_dealer", "meeting_category", "meeting_expense", "signin_accounts", "signin_companies", "new_signed_count", "new_activated_count", "post_redpack_output", "产出投入比", "单家签到费用"]].copy()
        show.columns = ["时间", "地市", "区县", "活动", "主办代理商", "会议类别", "费用", "签到人数", "签到公司", "会后新签", "会后新激活", "会后30天关联SO", "产出投入比", "单家签到费用"]
        st.dataframe(
            show.sort_values("费用", ascending=False).style.format({"费用": "{:,.0f}", "会后30天关联SO": "{:,.0f}", "产出投入比": "{:.2f}", "单家签到费用": "{:,.0f}"}, na_rep="—"),
            use_container_width=True, hide_index=True, height=560,
        )
        st.caption(f"当前共 {meeting_df['adsp_id'].nunique():,} 场会议、{attendee_df['row_key'].nunique():,} 条参会人明细。多人来自同一公司时不会被误删。")

with tabs[4]:
    st.subheader("门头服务商效率")
    if selected_district != "全部区县":
        st.warning("服务商门头明细源表没有区县字段；上方区县总览使用区县汇总口径，本表仍显示所选地市的服务商明细。")
    if provider_df.empty:
        st.info("当前筛选无服务商门头数据。")
    else:
        provider_df = provider_df.copy()
        provider_df["产出投入比"] = provider_df.apply(lambda row: ratio(row["annual_redpack_output"], row["storefront_invest"]), axis=1)
        provider_df["激活"] = provider_df["active_flag"].astype(str).str.upper().map({"Y": "是", "N": "否"}).fillna("未知")
        show = provider_df[["city", "provider_name", "dealer_name", "storefront_invest", "激活", "annual_redpack_output", "产出投入比"]].copy()
        show.columns = ["地市", "服务商", "所属代理商", "门头费用", "是否激活", "当年安装红包关联SO", "产出投入比"]
        st.dataframe(
            show.sort_values(["产出投入比", "门头费用"], ascending=[True, False]).style.format({"门头费用": "{:,.0f}", "当年安装红包关联SO": "{:,.0f}", "产出投入比": "{:.2f}"}, na_rep="—"),
            use_container_width=True, hide_index=True, height=560,
        )

with tabs[5]:
    st.subheader("导入批次与数据质量")
    batches = tables["batches"].copy()
    if not batches.empty:
        batches["来源"] = batches["source_type"].map(SOURCE_LABELS)
        st.dataframe(
            batches[["来源", "source_file", "row_count", "imported_at", "warnings_json"]].rename(columns={"source_file": "文件", "row_count": "行数", "imported_at": "导入时间", "warnings_json": "提醒"}),
            use_container_width=True, hide_index=True,
        )
    city_total = sum_col(city_all, "total_invest")
    dealer_total = sum_col(tables["dealer"], "total_invest")
    unallocated = city_total - dealer_total
    quality = pd.DataFrame([
        {"检查项": "城市费用 = 区县费用", "数值": city_total - sum_col(district_all, "total_invest"), "结论": "通过" if abs(city_total - sum_col(district_all, "total_invest")) <= 1 else "需核对"},
        {"检查项": "服务商门头汇总 = 服务商明细", "数值": sum_col(city_all, "provider_storefront_invest") - sum_col(tables["provider"], "storefront_invest"), "结论": "通过" if abs(sum_col(city_all, "provider_storefront_invest") - sum_col(tables["provider"], "storefront_invest")) <= 1 else "需核对"},
        {"检查项": "推广会汇总 = 会议明细", "数值": sum_col(city_all, "meeting_invest") - sum_col(tables["meeting"], "meeting_expense"), "结论": "通过" if abs(sum_col(city_all, "meeting_invest") - sum_col(tables["meeting"], "meeting_expense")) <= 1 else "需核对"},
        {"检查项": "尚未分摊到一级客户的费用", "数值": unallocated, "结论": "提示"},
    ])
    st.dataframe(quality.style.format({"数值": "{:,.2f}"}), use_container_width=True, hide_index=True)
    st.info("一级客户明细合计低于城市总投入的差额不会丢失：总盘以城市/区县文件为准，代理商页只展示已分摊费用。")
