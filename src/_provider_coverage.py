"""授权服务商覆盖指标，供全省/地市/区县/代理商全景共用。

口径：
  - 授权签约：provider_contract.管理标签 = '授权服务商'
  - 授权激活：授权签约中 provider_contract.是否激活 = 'Y'
  - 区域测算：district_base.服务商体量
  - 代理商测算：dealer_sandbox.下游服务商总数（未填写则渗透率为空）
"""
from __future__ import annotations

import sqlite3


AUTH_LABEL = '授权服务商'


def _ratio(numerator: int, denominator: int | float | None) -> float | None:
    if denominator is None or denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def authorized_provider_metrics(
    conn: sqlite3.Connection,
    *,
    city: str | None = None,
    district: str | None = None,
    dealer: str | None = None,
) -> dict:
    """返回一个实体的授权签约、授权激活和两项比率。"""
    where = ["管理标签 = ?", "客户编码 IS NOT NULL", "客户编码 != ''"]
    params: list = [AUTH_LABEL]
    if dealer:
        # 代理商页面的地市仅筛选代理商；选中后仍看该代理商全省数据。
        where.append("上级分销商名称 = ?")
        params.append(dealer)
    else:
        if city:
            where.append("客户城市 = ?")
            params.append(city)
        if district:
            where.append("客户区县 = ?")
            params.append(district)

    row = conn.execute(
        f"""
        SELECT COUNT(DISTINCT 客户编码) AS 授权签约,
               COUNT(DISTINCT CASE WHEN 是否激活='Y' THEN 客户编码 END) AS 授权激活
          FROM provider_contract
         WHERE {' AND '.join(where)}
        """,
        params,
    ).fetchone()
    signed = int(row[0] or 0)
    activated = int(row[1] or 0)

    if dealer:
        estimate_row = conn.execute(
            "SELECT 下游服务商总数 FROM dealer_sandbox WHERE 客户名称=? LIMIT 1",
            (dealer,),
        ).fetchone()
        estimate = float(estimate_row[0]) if estimate_row and estimate_row[0] not in (None, '') else None
        source = 'dealer_sandbox.下游服务商总数'
    else:
        estimate_where, estimate_params = [], []
        if city:
            estimate_where.append("城市 = ?")
            estimate_params.append(city)
        if district:
            estimate_where.append("区县 = ?")
            estimate_params.append(district)
        suffix = f" WHERE {' AND '.join(estimate_where)}" if estimate_where else ''
        estimate_row = conn.execute(
            f"SELECT SUM(服务商体量) FROM district_base{suffix}", estimate_params
        ).fetchone()
        estimate = float(estimate_row[0]) if estimate_row and estimate_row[0] is not None else None
        source = 'district_base.服务商体量'

    return {
        '测算服务商数': int(estimate) if estimate is not None else None,
        '授权签约数': signed,
        '授权渗透率': _ratio(signed, estimate),
        '授权激活数': activated,
        '授权激活率': _ratio(activated, signed),
        '测算来源': source,
    }


def authorized_provider_by_city(conn: sqlite3.Connection) -> list[dict]:
    """全省页面的地市横向对比数据。"""
    estimates = {
        row[0]: int(row[1] or 0)
        for row in conn.execute(
            "SELECT 城市, SUM(服务商体量) FROM district_base GROUP BY 城市"
        )
        if row[0]
    }
    actual = {
        row[0]: (int(row[1] or 0), int(row[2] or 0))
        for row in conn.execute(
            """
            SELECT 客户城市,
                   COUNT(DISTINCT 客户编码),
                   COUNT(DISTINCT CASE WHEN 是否激活='Y' THEN 客户编码 END)
              FROM provider_contract
             WHERE 管理标签=? AND 客户城市 IS NOT NULL AND 客户城市!=''
             GROUP BY 客户城市
            """,
            (AUTH_LABEL,),
        )
    }
    rows = []
    for city in sorted(set(estimates) | set(actual)):
        signed, activated = actual.get(city, (0, 0))
        estimate = estimates.get(city)
        rows.append({
            '城市': city,
            '测算服务商数': estimate,
            '授权签约数': signed,
            '授权渗透率': _ratio(signed, estimate),
            '授权激活数': activated,
            '授权激活率': _ratio(activated, signed),
        })
    return rows


def authorized_provider_by_district(conn: sqlite3.Connection, city: str) -> list[dict]:
    """地市页面的区县下钻数据；保留有签约但尚未维护测算的区县。"""
    estimates = {
        row[0]: int(row[1] or 0)
        for row in conn.execute(
            "SELECT 区县, SUM(服务商体量) FROM district_base WHERE 城市=? GROUP BY 区县",
            (city,),
        )
        if row[0]
    }
    actual = {
        row[0]: (int(row[1] or 0), int(row[2] or 0))
        for row in conn.execute(
            """
            SELECT 客户区县,
                   COUNT(DISTINCT 客户编码),
                   COUNT(DISTINCT CASE WHEN 是否激活='Y' THEN 客户编码 END)
              FROM provider_contract
             WHERE 管理标签=? AND 客户城市=?
               AND 客户区县 IS NOT NULL AND 客户区县!=''
             GROUP BY 客户区县
            """,
            (AUTH_LABEL, city),
        )
    }
    rows = []
    for district in sorted(set(estimates) | set(actual)):
        signed, activated = actual.get(district, (0, 0))
        estimate = estimates.get(district)
        rows.append({
            '区县': district,
            '测算服务商数': estimate,
            '授权签约数': signed,
            '授权渗透率': _ratio(signed, estimate),
            '授权激活数': activated,
            '授权激活率': _ratio(activated, signed),
        })
    rows.sort(key=lambda row: (-row['授权签约数'], row['区县']))
    return rows
