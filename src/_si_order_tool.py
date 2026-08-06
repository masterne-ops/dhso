#!/usr/bin/env python3
"""智能备货配单工具数据层。

生产业务库仅以 SQLite URI ``mode=ro`` 打开。本模块不建表、不写入业务数据；
页面上的型号和数量修改只保存在当前 Streamlit 会话中。
"""
from __future__ import annotations

import math
import re
import sqlite3
from collections.abc import Iterable
from pathlib import Path

import numpy as np
import pandas as pd


BLOCKED_STATUS_PATTERN = r"已退市|停产|停售|已停售|下架"


def norm_text(value) -> str:
    if value is None or pd.isna(value):
        return ""
    return re.sub(r"\s+", "", str(value).strip())


def norm_model(value) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).upper().strip()
    text = re.sub(r"[（(].*?[）)]", "", text)
    text = re.sub(r"\s+", "", text)
    for _ in range(3):
        text = re.sub(r"-(0280|0360|0400|0600|0800|1200)B(?=-|$)", "", text)
        text = re.sub(r"-(V\d+(?:\.\d+)?|S\d+|DOA|10-KIT|KIT|BULK)$", "", text)
    return text.strip("-")


def mode_value(series: pd.Series) -> str:
    values = [str(x).strip() for x in series.dropna() if str(x).strip()]
    if not values:
        return ""
    return str(pd.Series(values).value_counts().index[0])


def connect_readonly(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path).resolve()
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    conn.execute("PRAGMA query_only=ON")
    return conn


def _month_key_minus(year: int, month: int, offset: int) -> int:
    period = pd.Period(f"{year}-{month:02d}", freq="M") - offset
    return int(period.year * 100 + period.month)


def compute_customer_tasks(
    customers: pd.DataFrame,
    flags: pd.DataFrame,
    rhythm: pd.DataFrame,
    *,
    task_through_month: int,
    target_month: int,
    snapshot_date: str | None,
) -> pd.DataFrame:
    """计算目标月累计任务、进度与缺口。

    续签客户使用生产节奏的目标月占比；新签客户把剩余年度任务按目标月到
    12月的标准节奏比例分配。快照后新签、尚未进入快照表的客户也视为新签。
    """
    out = customers.copy()
    if flags.empty:
        out["是否新签"] = None
    else:
        out = out.merge(
            flags[["客户编码", "是否新签"]].drop_duplicates("客户编码"),
            on="客户编码",
            how="left",
            validate="one_to_one",
        )
    snap = pd.Timestamp(snapshot_date) if snapshot_date else pd.NaT
    sign_date = pd.to_datetime(out["签约时间"], errors="coerce")
    signed_after_snapshot = (
        sign_date.gt(snap) if pd.notna(snap) else pd.Series(False, index=out.index)
    )
    out["客户类型"] = np.where(
        out["是否新签"].eq("Y")
        | (out["是否新签"].isna() & signed_after_snapshot),
        "新签",
        "续签",
    )
    if not 1 <= task_through_month <= 12 or not 1 <= target_month <= 12:
        raise ValueError("任务基准月和目标月必须在1—12月")
    if target_month < task_through_month:
        raise ValueError("目标备货月份不能早于生产任务基准月")
    period_ratio = float(
        pd.to_numeric(
            rhythm.loc[
                rhythm["月份"].between(task_through_month + 1, target_month), "占比"
            ],
            errors="coerce",
        ).fillna(0).sum()
    )
    remaining_ratio = float(
        pd.to_numeric(
            rhythm.loc[rhythm["月份"].gt(task_through_month), "占比"], errors="coerce"
        ).fillna(0).sum()
    )
    if target_month > task_through_month and (period_ratio <= 0 or remaining_ratio <= 0):
        raise ValueError(
            f"生产库未找到从 {task_through_month + 1} 月到 {target_month} 月"
            "的有效客户 SI 进度节奏"
        )
    remaining = (out["年度任务_万"] - out["当前累计任务_万"]).clip(lower=0)
    out["目标周期节奏占比"] = period_ratio
    new_customer_increment = (
        remaining * period_ratio / remaining_ratio
        if remaining_ratio > 0
        else pd.Series(0.0, index=out.index)
    )
    out["目标周期新增任务_万"] = np.where(
        out["客户类型"].eq("续签"),
        out["年度任务_万"] * period_ratio,
        new_customer_increment,
    )
    out["目标月底累计任务_万"] = np.minimum(
        out["年度任务_万"],
        out["当前累计任务_万"] + out["目标周期新增任务_万"],
    )
    out["当前进度"] = np.where(
        out["目标月底累计任务_万"] > 0,
        out["当前官方达成_万"] / out["目标月底累计任务_万"],
        0,
    )
    out["目标月下单缺口_万"] = (
        out["目标月底累计任务_万"] - out["当前官方达成_万"]
    ).clip(lower=0)
    return out


def infer_task_through_month(customers: pd.DataFrame, rhythm: pd.DataFrame) -> dict:
    """根据正式累计任务占年度任务的中位数，识别任务表目前做到哪个月。

    续签客户的正式累计任务应接近月度节奏累计值。使用中位数可以避开少量
    新签客户、手工调整客户对月份识别的干扰。
    """
    ratios = (
        pd.to_numeric(customers["当前累计任务_万"], errors="coerce")
        / pd.to_numeric(customers["年度任务_万"], errors="coerce").replace(0, np.nan)
    )
    ratios = ratios.replace([np.inf, -np.inf], np.nan).dropna()
    ratios = ratios[(ratios >= 0) & (ratios <= 1.2)]
    if ratios.empty:
        raise ValueError("无法从正式任务表识别当前累计任务月份")
    observed = float(ratios.median())
    rhythm_sorted = rhythm.sort_values("月份").copy()
    rhythm_sorted["占比"] = pd.to_numeric(rhythm_sorted["占比"], errors="coerce").fillna(0)
    rhythm_sorted["累计占比"] = rhythm_sorted["占比"].cumsum()
    if rhythm_sorted.empty or rhythm_sorted["累计占比"].max() <= 0:
        raise ValueError("客户SI月度节奏为空")
    rhythm_sorted["差异"] = (rhythm_sorted["累计占比"] - observed).abs()
    best = rhythm_sorted.sort_values(["差异", "月份"]).iloc[0]
    through_month = int(best["月份"])
    available = list(range(through_month + 1, 13))
    if not available:
        available = [12]
    return {
        "task_through_month": through_month,
        "observed_task_ratio": observed,
        "matched_cumulative_ratio": float(best["累计占比"]),
        "ratio_difference": float(best["差异"]),
        "available_target_months": available,
        "default_target_month": available[0],
    }


def _build_quote_maps(quotes: pd.DataFrame) -> tuple[dict, dict]:
    q = quotes.copy()
    q["型号"] = q["型号"].fillna("").astype(str).str.strip()
    q["物料号"] = q["物料号"].fillna("").astype(str).str.strip()
    q["规范型号"] = q["型号"].map(norm_model)
    q["状态排序"] = (
        q["产品状态"]
        .fillna("")
        .map({"主推": 0, "在售": 1, "正常": 1, "新品": 1, "即将退市": 4, "已退市": 9})
        .fillna(2)
    )
    q["价格_元"] = pd.to_numeric(q["价格_元"], errors="coerce")
    q = q[q["价格_元"].between(0.01, 899999.99)].sort_values(
        ["状态排序", "价格_元"], ascending=[True, True]
    )
    exact: dict[str, dict] = {}
    normalized: dict[str, dict] = {}
    for _, row in q.iterrows():
        record = row.to_dict()
        for key in [row["型号"], row["物料号"]]:
            key = str(key).upper().strip()
            if key and key not in exact:
                exact[key] = record
        key = row["规范型号"]
        if key and key not in normalized:
            normalized[key] = record
    return exact, normalized


def assign_prices(catalog: pd.DataFrame, quotes: pd.DataFrame) -> pd.DataFrame:
    """按报价精确/规范匹配，失败后回退流向价和 SI 加权价。"""
    out = catalog.copy()
    exact, normalized = _build_quote_maps(quotes)
    price_rows: list[dict] = []
    for _, row in out.iterrows():
        internal = str(row.get("内部型号") or "").strip()
        external = str(row.get("外部型号") or "").strip()
        quote = None
        source = ""
        for key, label in [
            (internal.upper(), "报价单-内部型号精确"),
            (external.upper(), "报价单-外部型号精确"),
        ]:
            if key and key in exact:
                quote, source = exact[key], label
                break
        if quote is None:
            for key, label in [
                (norm_model(internal), "报价单-内部型号规范"),
                (norm_model(external), "报价单-外部型号规范"),
            ]:
                if key and key in normalized:
                    quote, source = normalized[key], label
                    break
        price = float(quote["价格_元"]) if quote is not None else 0
        status = str(quote.get("产品状态") or "") if quote is not None else ""
        quote_model = str(quote.get("型号") or "") if quote is not None else ""
        if not 0 < price < 900000:
            flow_price = float(row.get("流向价_元") or 0)
            if 0 < flow_price < 900000:
                price, source = flow_price, "产品流向-最新分销价回退"
        if not 0 < price < 900000:
            si_qty = float(row.get("SI_YTD数量") or 0)
            si_amount_yuan = float(row.get("SI_YTD金额_万") or 0) * 10000
            if si_qty > 0 and 0 < si_amount_yuan / si_qty < 900000:
                price, source = si_amount_yuan / si_qty, "SI期间加权单价回退"
        price_rows.append(
            {
                "最新分销价_元": price if 0 < price < 900000 else np.nan,
                "价格来源": source or "无可验证价格",
                "报价单型号": quote_model,
                "产品状态": status,
            }
        )
    return pd.concat(
        [out.reset_index(drop=True), pd.DataFrame(price_rows)], axis=1
    )


def _prepare_model_metrics(si: pd.DataFrame, so: pd.DataFrame, catalog: pd.DataFrame) -> pd.DataFrame:
    keys = ["客户编码", "内部型号"]
    model = si.merge(
        so,
        on=keys,
        how="outer",
        suffixes=("_SI", "_SO"),
        validate="one_to_one",
    )
    for col in [
        "SI_YTD数量",
        "SI_YTD金额_万",
        "SI_近3月数量",
        "SO_YTD数量",
        "SO_30数量",
        "SO_60数量",
        "SO_90数量",
    ]:
        model[col] = pd.to_numeric(model.get(col, 0), errors="coerce").fillna(0)
    model["客户名称"] = (
        model.get("客户名称_SI", pd.Series("", index=model.index))
        .fillna("")
        .where(
            model.get("客户名称_SI", pd.Series("", index=model.index)).fillna("").ne(""),
            model.get("客户名称_SO", pd.Series("", index=model.index)).fillna(""),
        )
    )
    model["外部型号"] = (
        model.get("外部型号_SI", pd.Series("", index=model.index))
        .fillna("")
        .where(
            model.get("外部型号_SI", pd.Series("", index=model.index)).fillna("").ne(""),
            model.get("外部型号_SO", pd.Series("", index=model.index)).fillna(""),
        )
    )
    model["标准产品四级"] = (
        model.get("标准产品四级_SO", pd.Series("", index=model.index))
        .fillna("")
        .where(
            model.get("标准产品四级_SO", pd.Series("", index=model.index)).fillna("").ne(""),
            model.get("标准产品四级_SI", pd.Series("", index=model.index)).fillna(""),
        )
    )
    price_cols = [
        "内部型号",
        "最新分销价_元",
        "价格来源",
        "产品状态",
        "报价单型号",
        "目录外部型号",
        "目录产品四级",
    ]
    model = model.merge(
        catalog[[c for c in price_cols if c in catalog.columns]],
        on="内部型号",
        how="left",
        validate="many_to_one",
    )
    model["外部型号"] = model["外部型号"].where(
        model["外部型号"].fillna("").ne(""), model["目录外部型号"].fillna("")
    )
    model["标准产品四级"] = model["标准产品四级"].where(
        model["标准产品四级"].fillna("").ne(""), model["目录产品四级"].fillna("")
    )
    model["近期月均SO"] = pd.concat(
        [
            model["SO_30数量"],
            model["SO_60数量"] / 2,
            model["SO_90数量"] / 3,
        ],
        axis=1,
    ).max(axis=1)
    model["SI_SO净流入数量"] = model["SI_YTD数量"] - model["SO_YTD数量"]
    model["基础补货需求数量"] = (
        model["近期月均SO"] * 1.2 - model["SI_SO净流入数量"].clip(lower=0)
    ).clip(lower=0)
    model["需求货值权重"] = (
        model["基础补货需求数量"]
        + model["近期月均SO"] * 0.35
        + model["SI_近3月数量"].clip(lower=0) / 3
    ) * model["最新分销价_元"].fillna(0)
    soon = model["产品状态"].fillna("").astype(str).str.contains("即将退市")
    blocked = model["产品状态"].fillna("").astype(str).str.contains(
        BLOCKED_STATUS_PATTERN, regex=True
    )
    model.loc[soon, "需求货值权重"] *= 0.2
    model.loc[blocked, "需求货值权重"] = 0
    return model


def _product_catalog(si: pd.DataFrame, so: pd.DataFrame, quotes: pd.DataFrame) -> pd.DataFrame:
    si_cat = (
        si.groupby("内部型号", as_index=False)
        .agg(
            目录外部型号=("外部型号", mode_value),
            目录产品四级=("标准产品四级", mode_value),
            SI_YTD数量=("SI_YTD数量", "sum"),
            SI_YTD金额_万=("SI_YTD金额_万", "sum"),
        )
    )
    so_cat = (
        so.groupby("内部型号", as_index=False)
        .agg(
            SO外部型号=("外部型号", mode_value),
            SO产品四级=("标准产品四级", mode_value),
            流向价_元=("流向价_元", "median"),
        )
    )
    cat = si_cat.merge(so_cat, on="内部型号", how="outer", validate="one_to_one")
    for col in ["SI_YTD数量", "SI_YTD金额_万", "流向价_元"]:
        cat[col] = pd.to_numeric(cat[col], errors="coerce").fillna(0)
    cat["目录外部型号"] = cat["目录外部型号"].fillna("").where(
        cat["目录外部型号"].fillna("").ne(""), cat["SO外部型号"].fillna("")
    )
    cat["目录产品四级"] = cat["目录产品四级"].fillna("").where(
        cat["目录产品四级"].fillna("").ne(""), cat["SO产品四级"].fillna("")
    )
    price_input = cat.rename(
        columns={"目录外部型号": "外部型号", "目录产品四级": "标准产品四级"}
    )
    priced = assign_prices(price_input, quotes).rename(
        columns={"外部型号": "目录外部型号", "标准产品四级": "目录产品四级"}
    )
    return priced.sort_values(
        ["目录产品四级", "内部型号"], na_position="last"
    ).reset_index(drop=True)


def load_tool_dataset(
    db_path: str | Path,
    *,
    year: int | None = None,
    target_month: int | None = None,
) -> dict:
    conn = connect_readonly(db_path)
    try:
        available_years = [
            int(r[0])
            for r in conn.execute(
                "SELECT DISTINCT 年度 FROM kpi_rhythm "
                "WHERE 指标 LIKE '客户SI进度条%' AND 类型='续签客户' "
                "ORDER BY 年度"
            ).fetchall()
            if r[0] is not None
        ]
        if not available_years:
            raise ValueError("生产库没有客户 SI 月度节奏")
        if year is None:
            current_year = pd.Timestamp.now(tz="Asia/Shanghai").year
            eligible_years = [y for y in available_years if y <= current_year]
            year = max(eligible_years or available_years)
        if year not in available_years:
            raise ValueError(f"生产库没有 {year} 年客户 SI 月度节奏")
        so_asof = conn.execute(
            "SELECT MAX(上线日期) FROM product_flow_v "
            "WHERE 上线日期 IS NOT NULL AND 产品序列号!='***'"
        ).fetchone()[0]
        quote_date = conn.execute("SELECT MAX(报价日期) FROM dahua_quotation").fetchone()[0]
        si_latest_key, si_imported = conn.execute(
            "SELECT MAX(数据年份*100+月份), MAX(导入时间) FROM dealer_purchase"
        ).fetchone()
        snapshot_date = conn.execute(
            "SELECT MAX(数据时点) FROM dealer_si_snapshot"
        ).fetchone()[0]
        customers = pd.read_sql(
            """
            SELECT 签约时间, TRIM(客户编码) AS 客户编码, TRIM(客户名称) AS 客户名称,
                   客户所在城市 AS 城市, 客户所在区县 AS 区县, 客户所有者,
                   CAST(COALESCE(签约金额,0) AS REAL) AS 年度任务_万,
                   CAST(COALESCE(NULLIF(TRIM(CAST(累计任务 AS TEXT)),''),'0') AS REAL)
                       AS 当前累计任务_万,
                   CAST(COALESCE("累计业绩达成（计任务）",0) AS REAL)
                       AS 当前官方达成_万
              FROM signed_customer_monthly
             WHERE TRIM(COALESCE(客户编码,''))!=''
            """,
            conn,
        )
        flags = pd.read_sql(
            """
            SELECT TRIM(客户编码) AS 客户编码, 是否新签
              FROM dealer_si_snapshot
             WHERE 数据时点=(SELECT MAX(数据时点) FROM dealer_si_snapshot)
            """,
            conn,
        )
        rhythm = pd.read_sql(
            """
            SELECT 月份, 占比
              FROM kpi_rhythm
             WHERE 年度=? AND 指标 LIKE '客户SI进度条%' AND 类型='续签客户'
             ORDER BY 月份
            """,
            conn,
            params=(year,),
        )
        planning = infer_task_through_month(customers, rhythm)
        if target_month is None:
            target_month = int(planning["default_target_month"])
        if target_month not in planning["available_target_months"]:
            raise ValueError(
                f"{target_month}月不在可规划月份"
                f"{planning['available_target_months']}中；正式任务已做到"
                f"{planning['task_through_month']}月"
            )
        customers = compute_customer_tasks(
            customers,
            flags,
            rhythm,
            task_through_month=int(planning["task_through_month"]),
            target_month=target_month,
            snapshot_date=snapshot_date,
        )
        customer_names = customers[["客户编码", "客户名称"]].copy()
        customer_names["客户规范名"] = customer_names["客户名称"].map(norm_text)
        if customer_names["客户规范名"].duplicated().any():
            raise ValueError("签约客户规范名存在重复，无法安全匹配 SI")

        recent_key = _month_key_minus(
            int(si_latest_key // 100), int(si_latest_key % 100), 2
        )
        si = pd.read_sql(
            """
            SELECT REPLACE(TRIM(下单客户),' ','') AS 客户规范名,
                   TRIM(内部型号) AS 内部型号,
                   MAX(TRIM(COALESCE(外部型号,''))) AS 外部型号,
                   MAX(TRIM(COALESCE(产品四级,''))) AS 标准产品四级,
                   SUM(COALESCE(实发数量,0)) AS SI_YTD数量,
                   SUM(COALESCE(实销万,0)) AS SI_YTD金额_万,
                   SUM(CASE WHEN 数据年份*100+月份>=? THEN COALESCE(实发数量,0) ELSE 0 END)
                       AS SI_近3月数量
              FROM dealer_purchase
             WHERE 数据年份=?
               AND TRIM(COALESCE(行业一级,''))='纯分销'
               AND LOWER(REPLACE(TRIM(COALESCE(外部型号,'')),' ',''))
                   !='discount'
               AND TRIM(COALESCE(内部型号,'')) NOT IN ('','(空白)','空白')
             GROUP BY 1,2
            """,
            conn,
            params=(recent_key, year),
        )
        si = si.merge(
            customer_names,
            on="客户规范名",
            how="inner",
            validate="many_to_one",
        )
        si = si[
            [
                "客户编码",
                "客户名称",
                "内部型号",
                "外部型号",
                "标准产品四级",
                "SI_YTD数量",
                "SI_YTD金额_万",
                "SI_近3月数量",
            ]
        ]

        values_sql = ",".join(["(?,?)"] * len(customers))
        target_params: list[str] = []
        for _, row in customers.iterrows():
            target_params.extend([str(row["客户编码"]), norm_text(row["客户名称"])])
        so = pd.read_sql(
            f"""
            WITH target(客户编码,客户规范名) AS (VALUES {values_sql}),
            base AS (
                SELECT COALESCE(t1.客户编码,t2.客户编码) AS 客户编码,
                       TRIM(p.内部型号) AS 内部型号,
                       TRIM(COALESCE(p.外部型号,'')) AS 外部型号,
                       TRIM(COALESCE(p.国内产品线四级,'')) AS 标准产品四级,
                       p.上线日期, p.产品序列号,
                       CASE WHEN p.最新分销价 BETWEEN 0.01 AND 899999.99
                            THEN p.最新分销价 END AS 流向价_元
                  FROM product_flow_v p
                  LEFT JOIN target t1 ON TRIM(p.出库客户编码)=t1.客户编码
                  LEFT JOIN target t2
                    ON t1.客户编码 IS NULL
                   AND REPLACE(TRIM(p.出库客户名称),' ','')=t2.客户规范名
                 WHERE p.上线日期 BETWEEN ? AND ?
                   AND p.产品序列号!='***'
                   AND TRIM(COALESCE(p.内部型号,''))!=''
                   AND COALESCE(t1.客户编码,t2.客户编码) IS NOT NULL
            )
            SELECT 客户编码, 内部型号,
                   MAX(外部型号) AS 外部型号,
                   MAX(标准产品四级) AS 标准产品四级,
                   COUNT(DISTINCT 产品序列号) AS SO_YTD数量,
                   COUNT(DISTINCT CASE WHEN 上线日期>=date(?,'-29 day')
                                       THEN 产品序列号 END) AS SO_30数量,
                   COUNT(DISTINCT CASE WHEN 上线日期>=date(?,'-59 day')
                                       THEN 产品序列号 END) AS SO_60数量,
                   COUNT(DISTINCT CASE WHEN 上线日期>=date(?,'-89 day')
                                       THEN 产品序列号 END) AS SO_90数量,
                   MAX(上线日期) AS 最近SO日期,
                   AVG(流向价_元) AS 流向价_元
              FROM base
             GROUP BY 客户编码,内部型号
            """,
            conn,
            params=(*target_params, f"{year}-01-01", so_asof, so_asof, so_asof, so_asof),
        )
        name_map = customers.set_index("客户编码")["客户名称"].to_dict()
        so["客户名称"] = so["客户编码"].map(name_map)

        quotes = pd.read_sql(
            """
            SELECT 型号,物料号,价格_元,产品状态,报价日期
              FROM dahua_quotation
             WHERE 报价日期=(SELECT MAX(报价日期) FROM dahua_quotation)
               AND 价格_元 BETWEEN 0.01 AND 899999.99
            """,
            conn,
        )
        catalog = _product_catalog(si, so, quotes)
        models = _prepare_model_metrics(si, so, catalog)
        si_codes = set(si["客户编码"].astype(str))
        customers["SI覆盖"] = np.where(
            customers["客户编码"].astype(str).isin(si_codes),
            "SI已匹配",
            "SI无有效型号记录",
        )
        customers = customers.sort_values(
            ["城市", "目标月下单缺口_万", "客户名称"],
            ascending=[True, False, True],
        ).reset_index(drop=True)
        return {
            "customers": customers,
            "models": models,
            "catalog": catalog,
            "meta": {
                "year": year,
                "target_month": target_month,
                **planning,
                "so_asof": so_asof,
                "si_latest_month": (
                    f"{int(si_latest_key // 100):04d}-{int(si_latest_key % 100):02d}"
                    if si_latest_key
                    else None
                ),
                "si_imported_at": si_imported,
                "quotation_date": quote_date,
                "task_snapshot_date": snapshot_date,
                "customer_count": int(len(customers)),
                "model_rows": int(len(models)),
                "catalog_rows": int(len(catalog)),
                "price_coverage": float(models["最新分销价_元"].notna().mean())
                if len(models)
                else 0,
            },
        }
    finally:
        conn.close()


def allocation_reason(row: pd.Series) -> str:
    status = str(row.get("产品状态") or "")
    if re.search(BLOCKED_STATUS_PATTERN, status):
        return "停售/退市/下架型号，自动配单已拦截"
    if "即将退市" in status:
        return "退市过渡型号，仅少量配置并需人工确认"
    if float(row.get("近期月均SO") or 0) <= 0:
        return "无近期SO，仅作手工配额补位"
    if float(row.get("SI_SO净流入数量") or 0) <= 0:
        return "SO已超过年内SI净流入，优先补充"
    if float(row.get("基础补货需求数量") or 0) > 0:
        return "近期SO较快，按SI/SO结构补充"
    return "按近期SI/SO结构分配"


def _allocate_subset(subset: pd.DataFrame, target_wan: float) -> pd.DataFrame:
    if subset.empty or target_wan <= 0:
        return subset.head(0).assign(建议下单数量=pd.Series(dtype=int))
    out = subset.copy()
    target_yuan = float(target_wan) * 10000
    weights = pd.to_numeric(out["需求货值权重"], errors="coerce").fillna(0).clip(lower=0)
    if weights.sum() <= 0:
        weights = (
            pd.to_numeric(out["近期月均SO"], errors="coerce").fillna(0).clip(lower=0)
            * out["最新分销价_元"]
        )
    if weights.sum() <= 0:
        weights = pd.Series(1.0, index=out.index)
    desired = target_yuan * weights / weights.sum()
    out["建议下单数量"] = np.floor(desired / out["最新分销价_元"]).astype(int)
    allocated = float((out["建议下单数量"] * out["最新分销价_元"]).sum())
    remainder = target_yuan - allocated
    min_price = float(out["最新分销价_元"].min())
    guard = 0
    while remainder >= min_price - 1e-9 and guard < 20000:
        eligible = out[out["最新分销价_元"].le(remainder + 1e-9)]
        if eligible.empty:
            break
        pick = (
            eligible.assign(
                单元优先级=weights.loc[eligible.index] / eligible["最新分销价_元"]
            )
            .sort_values(["单元优先级", "近期月均SO"], ascending=False)
            .index[0]
        )
        out.loc[pick, "建议下单数量"] += 1
        remainder -= float(out.loc[pick, "最新分销价_元"])
        guard += 1
    if remainder > 0:
        pick = (
            out.assign(加一后绝对差=(out["最新分销价_元"] - remainder).abs())
            .sort_values(["加一后绝对差", "需求货值权重"], ascending=[True, False])
            .index[0]
        )
        if abs(float(out.loc[pick, "最新分销价_元"]) - remainder) < remainder:
            out.loc[pick, "建议下单数量"] += 1
    out["建议下单金额_万"] = (
        out["建议下单数量"] * out["最新分销价_元"] / 10000
    )
    out["建议依据"] = out.apply(allocation_reason, axis=1)
    return out[out["建议下单数量"].gt(0)].sort_values(
        "建议下单金额_万", ascending=False
    )


def auto_allocate(
    customer_models: pd.DataFrame,
    target_wan: float,
    *,
    max_models: int = 18,
) -> pd.DataFrame:
    """从客户近期SI/SO型号中智能配单，并把金额贴近任务缺口。"""
    if target_wan <= 0 or customer_models.empty:
        return customer_models.head(0).copy()
    status = customer_models["产品状态"].fillna("").astype(str)
    subset = customer_models[
        customer_models["最新分销价_元"].notna()
        & customer_models["最新分销价_元"].gt(0)
        & ~status.str.contains(BLOCKED_STATUS_PATTERN, regex=True)
        & (
            customer_models["SO_90数量"].gt(0)
            | customer_models["SI_近3月数量"].gt(0)
            | customer_models["基础补货需求数量"].gt(0)
        )
    ].copy()
    subset = subset.sort_values(
        ["需求货值权重", "SO_90数量", "SI_近3月数量"],
        ascending=False,
    ).head(max_models)
    return _allocate_subset(subset, target_wan)


def enrich_order_rows(
    rows: Iterable[dict],
    customer_models: pd.DataFrame,
    catalog: pd.DataFrame,
) -> pd.DataFrame:
    """对会话中的型号/数量重新带出价格、SI、SO和金额。"""
    quantities: dict[str, int] = {}
    for row in rows:
        model = str(row.get("内部型号") or "").strip()
        if not model:
            continue
        try:
            qty = max(0, int(round(float(row.get("建议下单数量") or 0))))
        except (TypeError, ValueError):
            qty = 0
        quantities[model] = quantities.get(model, 0) + qty
    if not quantities:
        return pd.DataFrame(
            columns=[
                "标准产品四级",
                "内部型号",
                "外部型号",
                "SI_YTD数量",
                "SI_近3月数量",
                "SO_YTD数量",
                "SO_90数量",
                "近期月均SO",
                "最新分销价_元",
                "建议下单数量",
                "建议下单金额_万",
                "价格来源",
                "产品状态",
                "建议依据",
                "最近SO日期",
                "需求货值权重",
            ]
        )
    cm = customer_models.drop_duplicates("内部型号").set_index("内部型号")
    cat = catalog.drop_duplicates("内部型号").set_index("内部型号")
    records: list[dict] = []
    for model, qty in quantities.items():
        if model in cm.index:
            src = cm.loc[model].to_dict()
        elif model in cat.index:
            c = cat.loc[model].to_dict()
            src = {
                "标准产品四级": c.get("目录产品四级", ""),
                "外部型号": c.get("目录外部型号", ""),
                "SI_YTD数量": 0,
                "SI_近3月数量": 0,
                "SO_YTD数量": 0,
                "SO_90数量": 0,
                "近期月均SO": 0,
                "最新分销价_元": c.get("最新分销价_元"),
                "价格来源": c.get("价格来源", ""),
                "产品状态": c.get("产品状态", ""),
                "最近SO日期": None,
                "需求货值权重": 1,
                "SI_SO净流入数量": 0,
                "基础补货需求数量": 0,
            }
        else:
            continue
        raw_price = src.get("最新分销价_元")
        price = float(raw_price) if pd.notna(raw_price) else 0.0
        src["最新分销价_元"] = price
        src.update(
            {
                "内部型号": model,
                "建议下单数量": qty,
                "建议下单金额_万": qty * price / 10000,
            }
        )
        src["建议依据"] = allocation_reason(pd.Series(src))
        records.append(src)
    out = pd.DataFrame(records)
    return out.sort_values(
        ["建议下单金额_万", "内部型号"], ascending=[False, True]
    ).reset_index(drop=True)


def reallocate_existing(order: pd.DataFrame, target_wan: float) -> pd.DataFrame:
    """只使用当前订单中的型号，重新分配整数数量以对齐缺口。"""
    if order.empty:
        return order
    status = order["产品状态"].fillna("").astype(str)
    usable = order[
        order["最新分销价_元"].notna()
        & order["最新分销价_元"].gt(0)
        & ~status.str.contains(BLOCKED_STATUS_PATTERN, regex=True)
    ].copy()
    return _allocate_subset(usable, target_wan)


def allocation_status(order: pd.DataFrame, target_wan: float) -> dict:
    amount = (
        float(order["建议下单金额_万"].sum())
        if not order.empty and "建议下单金额_万" in order
        else 0.0
    )
    diff = amount - float(target_wan)
    max_unit_wan = (
        float(order["最新分销价_元"].max()) / 10000
        if not order.empty and order["最新分销价_元"].notna().any()
        else 0.05
    )
    tolerance = max(0.05, max_unit_wan)
    return {
        "target_wan": float(target_wan),
        "amount_wan": amount,
        "difference_wan": diff,
        "remaining_wan": max(float(target_wan) - amount, 0),
        "coverage": amount / float(target_wan) if target_wan > 0 else 1.0,
        "aligned": abs(diff) <= tolerance,
        "tolerance_wan": tolerance,
        "unit_count": int(order["建议下单数量"].sum()) if not order.empty else 0,
        "model_count": int((order["建议下单数量"] > 0).sum()) if not order.empty else 0,
    }


def validate_dataset(dataset: dict) -> list[dict]:
    customers = dataset["customers"]
    models = dataset["models"]
    checks = [
        {
            "检查": "客户编码唯一",
            "通过": not customers["客户编码"].duplicated().any(),
            "说明": f"{len(customers)}家",
        },
        {
            "检查": "任务缺口非负",
            "通过": bool(customers["目标月下单缺口_万"].ge(0).all()),
            "说明": f"最小值={customers['目标月下单缺口_万'].min():.2f}万",
        },
        {
            "检查": "客户型号键唯一",
            "通过": not models.duplicated(["客户编码", "内部型号"]).any(),
            "说明": f"{len(models)}行",
        },
        {
            "检查": "价格覆盖",
            "通过": bool(models["最新分销价_元"].notna().mean() >= 0.80),
            "说明": f"{models['最新分销价_元'].notna().mean():.1%}",
        },
    ]
    return checks
