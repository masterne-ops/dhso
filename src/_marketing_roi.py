"""市场推广费用专题：九类 Excel 的校验、快照导入与 ROI 查询。

设计原则：
1. 城市/区县/代理商/服务商是同一批费用的不同观察层级，分别保存，禁止跨层级相加。
2. 每个导出按文件名日期保存快照；同一日期、同一来源再次导入时整体替换，避免重复累计。
3. “产出投入比”=关联 SO / 费用，不等同于利润 ROI；页面必须使用清晰口径名称。
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import BinaryIO, Iterable

import pandas as pd


SOURCE_CITY = "city"
SOURCE_DISTRICT = "district"
SOURCE_PROVINCE = "province"
SOURCE_DEALER = "dealer"
SOURCE_PROVIDER = "provider"
SOURCE_MEETING = "meeting"
SOURCE_ATTENDEE = "attendee"
SOURCE_SUMMARY = "summary"
SOURCE_ADSP_WRITEOFF = "adsp_writeoff"

SOURCE_LABELS = {
    SOURCE_PROVINCE: "省区维度费用",
    SOURCE_CITY: "城市维度费用",
    SOURCE_DISTRICT: "区县维度费用",
    SOURCE_DEALER: "一级客户维度费用",
    SOURCE_PROVIDER: "服务商门头费用",
    SOURCE_MEETING: "会议汇总",
    SOURCE_ATTENDEE: "会议参会明细",
    SOURCE_SUMMARY: "市场推广费用总览",
    SOURCE_ADSP_WRITEOFF: "ADSP核销跟进",
}


REGION_COLUMNS = [
    "snapshot_date", "source_type", "row_key", "legion", "province", "city", "district",
    "total_invest", "material_invest", "gift_invest", "terminal_invest",
    "storefront_target", "storefront_count", "storefront_invest",
    "provider_storefront_count", "provider_storefront_invest", "provider_storefront_output",
    "zero_output_provider_count", "meeting_target", "meeting_count", "meeting_invest",
    "meeting_output_30d", "attendee_count", "attendee_company_count",
    "signed_rate", "activated_rate", "new_signed_count", "new_activated_count",
    "meeting_special_count", "night_units", "scene_units", "wireless_meeting_count",
    "wireless_units", "coupon_200_issued", "coupon_200_redeemed",
    "coupon_50_issued", "coupon_50_redeemed", "coupon_5_issued", "coupon_5_redeemed",
    "exclusive_store_invest", "security_center_invest", "vehicle_ad_invest",
    "outdoor_ad_invest", "other_ad_invest", "source_row",
]

DEALER_COLUMNS = [
    "snapshot_date", "row_key", "legion", "province", "business_type", "dealer_code",
    "dealer_name", "owner", "channel_type", "signed_target_wan", "performance_ytd",
    "orders_ytd", "fee_rate", "total_invest", "material_invest", "gift_invest",
    "terminal_invest", "storefront_count", "storefront_invest", "exclusive_store_count",
    "exclusive_store_invest", "security_center_count", "security_center_invest",
    "circle_meeting_count", "circle_meeting_invest", "city_meeting_count",
    "city_meeting_invest", "vehicle_ad_invest", "outdoor_ad_invest", "other_ad_invest",
    "source_row",
]

PROVIDER_COLUMNS = [
    "snapshot_date", "row_key", "legion", "province", "city", "provider_code",
    "provider_name", "dealer_name", "storefront_invest", "active_flag",
    "annual_redpack_output", "source_row",
]

MEETING_COLUMNS = [
    "snapshot_date", "row_key", "adsp_id", "meeting_source", "activity_name",
    "cloud_meeting_id", "business_type", "meeting_category", "expense_composition",
    "province", "city", "district", "activity_start", "host_dealer",
    "registration_companies", "signin_accounts", "signin_companies", "unconvertible_companies",
    "v5", "v4", "v3", "v2", "v1", "v0", "unsigned_count", "meeting_expense",
    "post_redpack_output", "evaluation_count", "signed_rate", "activated_rate",
    "new_signed_count", "new_activated_count", "coupon_200_issued", "coupon_200_redeemed",
    "coupon_200_issued_amount", "coupon_200_redeemed_amount", "coupon_50_issued",
    "coupon_50_redeemed", "coupon_50_issued_amount", "coupon_50_redeemed_amount",
    "coupon_5_issued", "coupon_5_redeemed", "coupon_5_issued_amount",
    "coupon_5_redeemed_amount", "night_units", "scene_units", "wireless_units", "source_row",
]

ATTENDEE_COLUMNS = [
    "snapshot_date", "row_key", "adsp_id", "cloud_meeting_id", "meeting_category",
    "expense_composition", "activity_name", "activity_start", "host_dealer",
    "signin_company", "customer_code", "customer_name", "participant_name", "account",
    "user_type", "channel_type_fixed", "star_realtime", "active_realtime",
    "post_redpack_output", "registration_time", "signin_time", "evaluation_time",
    "evaluation_content", "coupon_200_code", "coupon_200_amount", "coupon_200_status",
    "coupon_50_code", "coupon_50_amount", "coupon_50_status", "coupon_5_code",
    "coupon_5_amount", "coupon_5_status", "night_units", "scene_units", "wireless_units",
    "source_row",
]

SUMMARY_COLUMNS = [
    "snapshot_date", "row_key", "business_type", "total_invest", "material_invest",
    "gift_invest", "terminal_invest", "storefront_count", "storefront_invest",
    "exclusive_store_count", "exclusive_store_invest", "security_center_count",
    "security_center_invest", "circle_meeting_count", "circle_meeting_invest",
    "city_meeting_count", "city_meeting_invest", "vehicle_ad_count",
    "vehicle_ad_invest", "outdoor_ad_count", "outdoor_ad_invest", "other_ad_count",
    "other_ad_invest", "source_row",
]

ADSP_WRITEOFF_COLUMNS = [
    "snapshot_date", "row_key", "legion", "province", "creation_month", "adsp_id",
    "creator_name", "creator_department", "budget_amount", "closed_flag",
    "expense_reimbursement", "corporate_payment", "total_paid", "source_row",
]

NUMERIC_FIELDS = {
    *REGION_COLUMNS[7:-1],
    *DEALER_COLUMNS[9:-1],
    "storefront_invest", "annual_redpack_output",
    *MEETING_COLUMNS[14:47],
    "post_redpack_output", "coupon_200_amount", "coupon_50_amount", "coupon_5_amount",
    "night_units", "scene_units", "wireless_units",
    *SUMMARY_COLUMNS[3:-1],
    "budget_amount", "expense_reimbursement", "corporate_payment", "total_paid",
}


DDL = """
CREATE TABLE IF NOT EXISTS marketing_import_batch (
    batch_id TEXT PRIMARY KEY,
    snapshot_date TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_file TEXT NOT NULL,
    file_sha256 TEXT NOT NULL UNIQUE,
    imported_at TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    status TEXT NOT NULL,
    warnings_json TEXT
);
CREATE TABLE IF NOT EXISTS marketing_region_snapshot (
    snapshot_date TEXT NOT NULL, source_type TEXT NOT NULL, row_key TEXT NOT NULL,
    legion TEXT, province TEXT, city TEXT, district TEXT,
    total_invest REAL, material_invest REAL, gift_invest REAL, terminal_invest REAL,
    storefront_target REAL, storefront_count REAL, storefront_invest REAL,
    provider_storefront_count REAL, provider_storefront_invest REAL, provider_storefront_output REAL,
    zero_output_provider_count REAL, meeting_target REAL, meeting_count REAL, meeting_invest REAL,
    meeting_output_30d REAL, attendee_count REAL, attendee_company_count REAL,
    signed_rate REAL, activated_rate REAL, new_signed_count REAL, new_activated_count REAL,
    meeting_special_count REAL, night_units REAL, scene_units REAL, wireless_meeting_count REAL,
    wireless_units REAL, coupon_200_issued REAL, coupon_200_redeemed REAL,
    coupon_50_issued REAL, coupon_50_redeemed REAL, coupon_5_issued REAL, coupon_5_redeemed REAL,
    exclusive_store_invest REAL, security_center_invest REAL, vehicle_ad_invest REAL,
    outdoor_ad_invest REAL, other_ad_invest REAL, source_row INTEGER,
    PRIMARY KEY (snapshot_date, source_type, row_key)
);
CREATE TABLE IF NOT EXISTS marketing_dealer_snapshot (
    snapshot_date TEXT NOT NULL, row_key TEXT NOT NULL, legion TEXT, province TEXT,
    business_type TEXT, dealer_code TEXT, dealer_name TEXT, owner TEXT, channel_type TEXT,
    signed_target_wan REAL, performance_ytd REAL, orders_ytd REAL, fee_rate REAL,
    total_invest REAL, material_invest REAL, gift_invest REAL, terminal_invest REAL,
    storefront_count REAL, storefront_invest REAL, exclusive_store_count REAL,
    exclusive_store_invest REAL, security_center_count REAL, security_center_invest REAL,
    circle_meeting_count REAL, circle_meeting_invest REAL, city_meeting_count REAL,
    city_meeting_invest REAL, vehicle_ad_invest REAL, outdoor_ad_invest REAL,
    other_ad_invest REAL, source_row INTEGER,
    PRIMARY KEY (snapshot_date, row_key)
);
CREATE TABLE IF NOT EXISTS marketing_provider_snapshot (
    snapshot_date TEXT NOT NULL, row_key TEXT NOT NULL, legion TEXT, province TEXT, city TEXT,
    provider_code TEXT, provider_name TEXT, dealer_name TEXT, storefront_invest REAL,
    active_flag TEXT, annual_redpack_output REAL, source_row INTEGER,
    PRIMARY KEY (snapshot_date, row_key)
);
CREATE TABLE IF NOT EXISTS marketing_meeting_snapshot (
    snapshot_date TEXT NOT NULL, row_key TEXT NOT NULL, adsp_id TEXT, meeting_source TEXT,
    activity_name TEXT, cloud_meeting_id TEXT, business_type TEXT, meeting_category TEXT,
    expense_composition TEXT, province TEXT, city TEXT, district TEXT, activity_start TEXT,
    host_dealer TEXT, registration_companies REAL, signin_accounts REAL, signin_companies REAL,
    unconvertible_companies REAL, v5 REAL, v4 REAL, v3 REAL, v2 REAL, v1 REAL, v0 REAL,
    unsigned_count REAL, meeting_expense REAL, post_redpack_output REAL, evaluation_count REAL,
    signed_rate REAL, activated_rate REAL, new_signed_count REAL, new_activated_count REAL,
    coupon_200_issued REAL, coupon_200_redeemed REAL, coupon_200_issued_amount REAL,
    coupon_200_redeemed_amount REAL, coupon_50_issued REAL, coupon_50_redeemed REAL,
    coupon_50_issued_amount REAL, coupon_50_redeemed_amount REAL, coupon_5_issued REAL,
    coupon_5_redeemed REAL, coupon_5_issued_amount REAL, coupon_5_redeemed_amount REAL,
    night_units REAL, scene_units REAL, wireless_units REAL, source_row INTEGER,
    PRIMARY KEY (snapshot_date, row_key)
);
CREATE TABLE IF NOT EXISTS marketing_meeting_attendee (
    snapshot_date TEXT NOT NULL, row_key TEXT NOT NULL, adsp_id TEXT, cloud_meeting_id TEXT,
    meeting_category TEXT, expense_composition TEXT, activity_name TEXT, activity_start TEXT,
    host_dealer TEXT, signin_company TEXT, customer_code TEXT, customer_name TEXT,
    participant_name TEXT, account TEXT, user_type TEXT, channel_type_fixed TEXT,
    star_realtime TEXT, active_realtime TEXT, post_redpack_output REAL,
    registration_time TEXT, signin_time TEXT, evaluation_time TEXT, evaluation_content TEXT,
    coupon_200_code TEXT, coupon_200_amount REAL, coupon_200_status TEXT,
    coupon_50_code TEXT, coupon_50_amount REAL, coupon_50_status TEXT,
    coupon_5_code TEXT, coupon_5_amount REAL, coupon_5_status TEXT,
    night_units REAL, scene_units REAL, wireless_units REAL, source_row INTEGER,
    PRIMARY KEY (snapshot_date, row_key)
);
CREATE TABLE IF NOT EXISTS marketing_summary_snapshot (
    snapshot_date TEXT NOT NULL, row_key TEXT NOT NULL, business_type TEXT,
    total_invest REAL, material_invest REAL, gift_invest REAL, terminal_invest REAL,
    storefront_count REAL, storefront_invest REAL, exclusive_store_count REAL,
    exclusive_store_invest REAL, security_center_count REAL, security_center_invest REAL,
    circle_meeting_count REAL, circle_meeting_invest REAL, city_meeting_count REAL,
    city_meeting_invest REAL, vehicle_ad_count REAL, vehicle_ad_invest REAL,
    outdoor_ad_count REAL, outdoor_ad_invest REAL, other_ad_count REAL,
    other_ad_invest REAL, source_row INTEGER,
    PRIMARY KEY (snapshot_date, row_key)
);
CREATE TABLE IF NOT EXISTS marketing_adsp_writeoff_snapshot (
    snapshot_date TEXT NOT NULL, row_key TEXT NOT NULL, legion TEXT, province TEXT,
    creation_month TEXT, adsp_id TEXT, creator_name TEXT, creator_department TEXT,
    budget_amount REAL, closed_flag TEXT, expense_reimbursement REAL,
    corporate_payment REAL, total_paid REAL, source_row INTEGER,
    PRIMARY KEY (snapshot_date, row_key)
);
CREATE INDEX IF NOT EXISTS idx_mrs_geo ON marketing_region_snapshot(snapshot_date, source_type, city, district);
CREATE INDEX IF NOT EXISTS idx_mds_dealer ON marketing_dealer_snapshot(snapshot_date, dealer_code);
CREATE INDEX IF NOT EXISTS idx_mps_geo ON marketing_provider_snapshot(snapshot_date, city, dealer_name);
CREATE INDEX IF NOT EXISTS idx_mms_geo ON marketing_meeting_snapshot(snapshot_date, city, district);
CREATE INDEX IF NOT EXISTS idx_mma_customer ON marketing_meeting_attendee(snapshot_date, customer_code, adsp_id);
CREATE INDEX IF NOT EXISTS idx_maw_adsp ON marketing_adsp_writeoff_snapshot(snapshot_date, adsp_id);
"""


@dataclass
class ParsedMarketingFile:
    name: str
    source_type: str
    snapshot_date: str
    sha256: str
    dataframe: pd.DataFrame
    warnings: list[str]

    @property
    def row_count(self) -> int:
        return len(self.dataframe)


def _clean_text(value) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip().replace("（", "(").replace("）", ")")
    return None if not text or text.lower() == "nan" else text


def _number(value) -> float | None:
    if value is None or value == "" or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, str) and value.startswith("#"):
        return None
    try:
        return float(str(value).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None


def _datetime_text(value) -> str | None:
    if value is None or value == "" or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, (int, float)):
        parsed = pd.Timestamp("1899-12-30") + pd.to_timedelta(float(value), unit="D")
    else:
        parsed = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(parsed) else parsed.strftime("%Y-%m-%d %H:%M:%S")


def _file_bytes(uploaded_file) -> tuple[str, bytes]:
    if isinstance(uploaded_file, (str, Path)):
        path = Path(uploaded_file)
        return path.name, path.read_bytes()
    name = getattr(uploaded_file, "name", "uploaded.xlsx")
    if hasattr(uploaded_file, "getvalue"):
        return name, uploaded_file.getvalue()
    if hasattr(uploaded_file, "seek"):
        uploaded_file.seek(0)
    data = uploaded_file.read()
    return name, data


def _snapshot_date(name: str) -> str:
    matches = re.findall(r"(?<!\d)(20\d{6})(?!\d)", name)
    if not matches:
        raise ValueError(f"文件名缺少 YYYYMMDD 数据快照日期：{name}")
    return datetime.strptime(matches[-1], "%Y%m%d").date().isoformat()


def _source_type(title: str, name: str) -> str:
    probe = f"{title} {name}"
    if "区县维度" in probe:
        return SOURCE_DISTRICT
    if "城市维度" in probe:
        return SOURCE_CITY
    if "省区维度" in probe:
        return SOURCE_PROVINCE
    if "一级客户维度" in probe:
        return SOURCE_DEALER
    if "服务商门头维度" in probe or "服务商维度" in probe:
        return SOURCE_PROVIDER
    if "ADSP核销跟进" in probe:
        return SOURCE_ADSP_WRITEOFF
    if "市场推广费用_总览" in probe or "市场推广分析_汇总" in probe:
        return SOURCE_SUMMARY
    if "参会客户明细" in probe:
        return SOURCE_ATTENDEE
    if "会议分析" in probe:
        return SOURCE_MEETING
    raise ValueError(f"无法识别文件类型：{name}")


def _header_keys(raw: pd.DataFrame, top_row: int, sub_row: int) -> list[str | None]:
    result: list[str | None] = []
    seen: dict[str, int] = {}
    active_top: str | None = None
    for col in range(raw.shape[1]):
        top = _clean_text(raw.iat[top_row, col])
        sub = _clean_text(raw.iat[sub_row, col])
        if top:
            active_top = top
        if top and not sub:
            key = top
        elif active_top and sub:
            key = f"{active_top}|{sub}"
        else:
            key = None
        if key:
            seen[key] = seen.get(key, 0) + 1
            if seen[key] > 1:
                key = f"{key}#{seen[key]}"
        result.append(key)
    return result


def _value(row: pd.Series, keys: list[str | None], key: str):
    try:
        return row.iloc[keys.index(key)]
    except ValueError:
        return None


def _record(row: pd.Series, keys: list[str | None], mapping: dict[str, str], source_row: int) -> dict:
    record: dict = {target: _value(row, keys, source) for target, source in mapping.items()}
    record["source_row"] = source_row
    for key in list(record):
        if key in NUMERIC_FIELDS:
            record[key] = _number(record[key])
        elif key.endswith("_time") or key == "activity_start":
            record[key] = _datetime_text(record[key])
        else:
            record[key] = _clean_text(record[key])
    return record


REGION_MAP = {
    "legion": "军团", "province": "省份", "city": "城市", "district": "区县",
    "total_invest": "总投入金额", "material_invest": "领料投入金额",
    "gift_invest": "积分商城礼品投入金额", "terminal_invest": "终端推广投入金额",
    "storefront_target": "门头|门头目标", "storefront_count": "门头|已营建数量",
    "storefront_invest": "门头|金额", "provider_storefront_count": "门头|服务商营建数量",
    "provider_storefront_invest": "门头|服务商营建金额",
    "provider_storefront_output": "门头|年度安装红包",
    "zero_output_provider_count": "门头|0产值服务商数量",
    "meeting_target": "推广会(申请标准是圈子会议+地市推广会)|推广会目标",
    "meeting_count": "推广会(申请标准是圈子会议+地市推广会)|已开展数量",
    "meeting_invest": "推广会(申请标准是圈子会议+地市推广会)|金额",
    "meeting_output_30d": "推广会(申请标准是圈子会议+地市推广会)|会后30天签到公司业绩产出(万)",
    "attendee_count": "推广会(申请标准是圈子会议+地市推广会)|签到人数",
    "attendee_company_count": "推广会(申请标准是圈子会议+地市推广会)|签到公司数",
    "signed_rate": "推广会(申请标准是圈子会议+地市推广会)|签到公司签约率",
    "activated_rate": "推广会(申请标准是圈子会议+地市推广会)|签到公司激活率",
    "new_signed_count": "推广会(申请标准是圈子会议+地市推广会)|会后新签客户数\n(含会议当天)",
    "new_activated_count": "推广会(申请标准是圈子会议+地市推广会)|会后新激活客户数\n(含会议当天)",
    "meeting_special_count": "推广会(申请标准是圈子会议+地市推广会)|夜视王+场景化专项会议场次",
    "night_units": "推广会(申请标准是圈子会议+地市推广会)|夜视王产品上线台数",
    "scene_units": "推广会(申请标准是圈子会议+地市推广会)|场景化产品上线台数",
    "wireless_meeting_count": "推广会(申请标准是圈子会议+地市推广会)|无线专项会议场次",
    "wireless_units": "推广会(申请标准是圈子会议+地市推广会)|无线产品上线台数",
    "coupon_200_issued": "200元签到券|会议卡券发放数量",
    "coupon_200_redeemed": "200元签到券|会议卡券已解锁数量",
    "coupon_50_issued": "50或100元签到券|会议卡券发放数量",
    "coupon_50_redeemed": "50或100元签到券|会议卡券已解锁数量",
    "coupon_5_issued": "无线产品5元签到券|会议卡券发放数量",
    "coupon_5_redeemed": "无线产品5元签到券|会议卡券已解锁数量",
    "exclusive_store_invest": "专卖店|金额", "security_center_invest": "安防服务中心|金额",
    "vehicle_ad_invest": "车体广告|金额", "outdoor_ad_invest": "户外广告|金额",
    "other_ad_invest": "其他广告|金额",
}

DISTRICT_REGION_MAP = {
    **REGION_MAP,
    "provider_storefront_count": "门头|服务商营建数量",
    "provider_storefront_invest": "门头|金额#2",
    "provider_storefront_output": "门头|年度服务商安装红包金额",
}

PROVINCE_REGION_MAP = {
    **REGION_MAP,
    "meeting_target": "推广会(圈子会议&地市推广会)|推广会目标",
    "meeting_count": "推广会(圈子会议&地市推广会)|已开展数量",
    "meeting_invest": "推广会(圈子会议&地市推广会)|金额",
    "meeting_output_30d": "推广会(圈子会议&地市推广会)|会后30天签到公司业绩产出",
    "attendee_count": "推广会(圈子会议&地市推广会)|签到人数",
    "attendee_company_count": "推广会(圈子会议&地市推广会)|签到公司数",
    "signed_rate": "推广会(圈子会议&地市推广会)|签到公司签约率",
    "activated_rate": "推广会(圈子会议&地市推广会)|签到公司激活率",
    "new_signed_count": "推广会(圈子会议&地市推广会)|会后新签客户数\n(含会议当天)",
    "new_activated_count": "推广会(圈子会议&地市推广会)|会后新激活客户数\n(含会议当天)",
    "meeting_special_count": "推广会(圈子会议&地市推广会)|夜视王+场景化专项会议场次",
    "night_units": "推广会(圈子会议&地市推广会)|夜视王产品上线台数",
    "scene_units": "推广会(圈子会议&地市推广会)|场景化产品上线台数",
    "wireless_meeting_count": "推广会(圈子会议&地市推广会)|无线专项会议场次",
    "wireless_units": "推广会(圈子会议&地市推广会)|无线产品上线台数",
}

DEALER_MAP = {
    "legion": "军团", "province": "省份", "business_type": "业务类型",
    "dealer_code": "一级客户编码", "dealer_name": "所属一级名称", "owner": "客户所有者",
    "channel_type": "渠道客户类型", "signed_target_wan": "签约金额(万)",
    "performance_ytd": "累计业绩(计任务)", "orders_ytd": "累计订单", "fee_rate": "费率",
    "total_invest": "总投入金额", "material_invest": "领料投入金额",
    "gift_invest": "积分商城礼品投入金额", "terminal_invest": "终端推广投入金额",
    "storefront_count": "门头|数量", "storefront_invest": "门头|金额",
    "exclusive_store_count": "专卖店|数量", "exclusive_store_invest": "专卖店|金额",
    "security_center_count": "安防服务中心|数量", "security_center_invest": "安防服务中心|金额",
    "circle_meeting_count": "圈子会议|数量", "circle_meeting_invest": "圈子会议|金额",
    "city_meeting_count": "地市推广会|数量", "city_meeting_invest": "地市推广会|金额",
    "vehicle_ad_invest": "车体广告|金额", "outdoor_ad_invest": "户外广告|金额",
    "other_ad_invest": "其他广告|金额",
}

PROVIDER_MAP = {
    "legion": "军团", "province": "省份", "city": "地市", "provider_code": "投入客户编码",
    "provider_name": "投入客户名称", "dealer_name": "所属一级名称",
    "storefront_invest": "投入金额(元)", "active_flag": "是否激活",
    "annual_redpack_output": "当年安装红包上线金额(元)",
}

MEETING_MAP = {
    "adsp_id": "ADSPID", "meeting_source": "会议来源", "activity_name": "活动名称",
    "cloud_meeting_id": "云商会议沙龙ID", "business_type": "业务类型",
    "meeting_category": "会议类别", "expense_composition": "费用组成", "province": "省份",
    "city": "地市", "district": "区县", "activity_start": "活动开始时间",
    "host_dealer": "主办方(认证名称)", "registration_companies": "报名公司数",
    "signin_accounts": "签到账号数", "signin_companies": "签到公司数",
    "unconvertible_companies": "签到公司数\n(无法转化服务商)",
    "v5": "实时参会客户星级分布(服务商)|V5", "v4": "实时参会客户星级分布(服务商)|V4",
    "v3": "实时参会客户星级分布(服务商)|V3", "v2": "实时参会客户星级分布(服务商)|V2",
    "v1": "实时参会客户星级分布(服务商)|V1", "v0": "实时参会客户星级分布(服务商)|V0",
    "unsigned_count": "实时参会客户星级分布(服务商)|未签约数",
    "meeting_expense": "会议申请费用", "post_redpack_output": "参会后安装红包金额",
    "evaluation_count": "会议评价数量", "signed_rate": "签到公司签约率",
    "activated_rate": "签到公司激活率", "new_signed_count": "会后新签客户数\n(含会议当天)",
    "new_activated_count": "会后新激活客户数(含会议当天)",
    "coupon_200_issued": "200元签到券|会议卡券发放数量",
    "coupon_200_redeemed": "200元签到券|会议卡券已解锁数量",
    "coupon_200_issued_amount": "200元签到券|会议卡券发放金额",
    "coupon_200_redeemed_amount": "200元签到券|会议卡券已解锁金额",
    "coupon_50_issued": "50或100元签到券|会议卡券发放数量",
    "coupon_50_redeemed": "50或100元签到券|会议卡券已解锁数量",
    "coupon_50_issued_amount": "50或100元签到券|会议卡券发放金额",
    "coupon_50_redeemed_amount": "50或100元签到券|会议卡券已解锁金额",
    "coupon_5_issued": "无线产品5元签到券|会议卡券发放数量",
    "coupon_5_redeemed": "无线产品5元签到券|会议卡券已解锁数量",
    "coupon_5_issued_amount": "无线产品5元签到券|会议卡券发放金额",
    "coupon_5_redeemed_amount": "无线产品5元签到券|会议卡券已解锁金额",
    "night_units": "夜视王产品上线台数", "scene_units": "场景化产品上线台数",
    "wireless_units": "无线产品上线台数",
}

ATTENDEE_MAP = {
    "adsp_id": "ADSPID", "cloud_meeting_id": "云商会议ID", "meeting_category": "会议类别",
    "expense_composition": "费用组成", "activity_name": "活动名称", "activity_start": "活动开始时间",
    "host_dealer": "主办方(认证名称)", "signin_company": "签到时填写的公司名称",
    "customer_code": "参会客户编码(实时)", "customer_name": "参会客户名称(实时)",
    "participant_name": "参与人姓名", "account": "账号", "user_type": "用户类型",
    "channel_type_fixed": "渠道客户类型(固化)", "star_realtime": "星级(实时)",
    "active_realtime": "是否激活(实时)", "post_redpack_output": "参会后安装红包金额",
    "registration_time": "报名时间", "signin_time": "签到时间", "evaluation_time": "评价时间",
    "evaluation_content": "评价内容", "coupon_200_code": "200元签到券|卡券编码",
    "coupon_200_amount": "200元签到券|卡券发放面额", "coupon_200_status": "200元签到券|卡券使用状态",
    "coupon_50_code": "50或100元签到券|卡券编码", "coupon_50_amount": "50或100元签到券|卡券发放面额",
    "coupon_50_status": "50或100元签到券|卡券使用状态", "coupon_5_code": "无线产品5元签到券|卡券编码",
    "coupon_5_amount": "无线产品5元签到券|卡券发放面额", "coupon_5_status": "无线产品5元签到券|卡券使用状态",
    "night_units": "夜视王产品上线台数", "scene_units": "场景化产品上线台数",
    "wireless_units": "无线产品上线台数",
}

SUMMARY_MAP = {
    "business_type": "业务类型", "total_invest": "总投入金额",
    "material_invest": "领料投入金额", "gift_invest": "积分商城礼品投入金额",
    "terminal_invest": "终端推广投入金额", "storefront_count": "门头|数量",
    "storefront_invest": "门头|金额", "exclusive_store_count": "专卖店|数量",
    "exclusive_store_invest": "专卖店|金额",
    "security_center_count": "安防服务中心|数量",
    "security_center_invest": "安防服务中心|金额",
    "circle_meeting_count": "圈子会议|数量", "circle_meeting_invest": "圈子会议|金额",
    "city_meeting_count": "地市推广会|数量", "city_meeting_invest": "地市推广会|金额",
    "vehicle_ad_count": "车体广告|数量", "vehicle_ad_invest": "车体广告|金额",
    "outdoor_ad_count": "户外广告|数量", "outdoor_ad_invest": "户外广告|金额",
    "other_ad_count": "其他广告|数量", "other_ad_invest": "其他广告|金额",
}

ADSP_WRITEOFF_MAP = {
    "legion": "军团", "province": "省份", "creation_month": "创建月份",
    "adsp_id": "ADSP单号", "creator_name": "创建人姓名",
    "creator_department": "创建人部门", "budget_amount": "预算申请金额",
    "closed_flag": "是否结项", "expense_reimbursement": "每刻核报销金额",
    "corporate_payment": "对公支付金额", "total_paid": "付款总金额",
}


def parse_marketing_file(uploaded_file) -> ParsedMarketingFile:
    name, content = _file_bytes(uploaded_file)
    raw = pd.read_excel(io.BytesIO(content), header=None, dtype=object)
    title = " ".join(filter(None, (_clean_text(v) for v in raw.iloc[0].tolist())))
    source_type = _source_type(title, name)
    snapshot = _snapshot_date(name)
    sha256 = hashlib.sha256(content).hexdigest()
    warnings: list[str] = []

    if source_type == SOURCE_PROVIDER:
        keys = [_clean_text(v) for v in raw.iloc[1].tolist()]
        start_row, mapping = 3, PROVIDER_MAP
    elif source_type == SOURCE_ADSP_WRITEOFF:
        keys = [_clean_text(v) for v in raw.iloc[1].tolist()]
        start_row, mapping = 3, ADSP_WRITEOFF_MAP
    elif source_type == SOURCE_ATTENDEE:
        keys = _header_keys(raw, 1, 2)
        start_row, mapping = 3, ATTENDEE_MAP
    elif source_type == SOURCE_SUMMARY:
        keys = _header_keys(raw, 1, 2)
        start_row, mapping = 3, SUMMARY_MAP
    else:
        keys = _header_keys(raw, 1, 2)
        start_row = 4
        mapping = {
            SOURCE_PROVINCE: PROVINCE_REGION_MAP,
            SOURCE_CITY: REGION_MAP,
            SOURCE_DISTRICT: DISTRICT_REGION_MAP,
            SOURCE_DEALER: DEALER_MAP,
            SOURCE_MEETING: MEETING_MAP,
        }[source_type]

    records = []
    for index in range(start_row, len(raw)):
        row = raw.iloc[index]
        record = _record(row, keys, mapping, source_row=index + 1)
        if source_type in (SOURCE_PROVINCE, SOURCE_CITY, SOURCE_DISTRICT):
            if source_type == SOURCE_PROVINCE:
                if not record.get("province"):
                    continue
                natural = record["province"]
            elif not record.get("city") or (source_type == SOURCE_DISTRICT and not record.get("district")):
                continue
            record["source_type"] = source_type
            if source_type != SOURCE_PROVINCE:
                natural = f"{record.get('city','')}|{record.get('district','')}"
        elif source_type == SOURCE_DEALER:
            if not record.get("dealer_code"):
                continue
            natural = f"{record.get('dealer_code')}|{record.get('business_type')}|{record.get('channel_type')}|{index + 1}"
        elif source_type == SOURCE_PROVIDER:
            if not record.get("provider_code"):
                continue
            natural = record["provider_code"]
        elif source_type == SOURCE_MEETING:
            if not record.get("adsp_id"):
                continue
            natural = record["adsp_id"]
        elif source_type == SOURCE_SUMMARY:
            if not record.get("business_type"):
                continue
            natural = record["business_type"]
        elif source_type == SOURCE_ADSP_WRITEOFF:
            if not record.get("adsp_id"):
                continue
            natural = record["adsp_id"]
        else:
            if not record.get("adsp_id"):
                continue
            natural = f"{record.get('adsp_id')}|{record.get('customer_code') or ''}|{record.get('account') or record.get('participant_name') or f'row-{index + 1}'}"
        record["snapshot_date"] = snapshot
        record["row_key"] = hashlib.sha1(natural.encode("utf-8")).hexdigest()
        records.append(record)

    frame = pd.DataFrame(records)
    expected = {
        SOURCE_PROVINCE: REGION_COLUMNS, SOURCE_CITY: REGION_COLUMNS, SOURCE_DISTRICT: REGION_COLUMNS,
        SOURCE_DEALER: DEALER_COLUMNS, SOURCE_PROVIDER: PROVIDER_COLUMNS,
        SOURCE_MEETING: MEETING_COLUMNS, SOURCE_ATTENDEE: ATTENDEE_COLUMNS,
        SOURCE_SUMMARY: SUMMARY_COLUMNS, SOURCE_ADSP_WRITEOFF: ADSP_WRITEOFF_COLUMNS,
    }[source_type]
    for column in expected:
        if column not in frame.columns:
            frame[column] = None
    frame = frame[expected]

    if frame.empty:
        raise ValueError(f"未读取到有效数据行：{name}")
    duplicated = int(frame["row_key"].duplicated().sum())
    if duplicated:
        warnings.append(f"文件内有 {duplicated} 个重复业务主键，保留最后一行")
        frame = frame.drop_duplicates("row_key", keep="last").reset_index(drop=True)
    if source_type == SOURCE_ATTENDEE:
        coded = frame[frame["customer_code"].notna()]
        old_pk_dups = int(coded.duplicated(["adsp_id", "customer_code"]).sum())
        if old_pk_dups:
            warnings.append(f"同一会议同一公司存在 {old_pk_dups} 条多人参会记录，已全部保留")
    if source_type == SOURCE_DEALER:
        repeated = int(frame["dealer_code"].duplicated().sum())
        if repeated:
            warnings.append(f"同一代理商有 {repeated} 条业务/渠道拆分行，展示时按代理商汇总")
    return ParsedMarketingFile(name, source_type, snapshot, sha256, frame, warnings)


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)


def _insert_frame(conn: sqlite3.Connection, table: str, frame: pd.DataFrame) -> None:
    columns = list(frame.columns)
    quoted = ", ".join(f'"{c}"' for c in columns)
    placeholders = ", ".join("?" for _ in columns)
    sql = f"INSERT OR REPLACE INTO {table} ({quoted}) VALUES ({placeholders})"
    rows = []
    for values in frame.itertuples(index=False, name=None):
        rows.append(tuple(None if pd.isna(v) else v for v in values))
    conn.executemany(sql, rows)


def import_marketing_files(files: Iterable, db_path: str | Path, force: bool = False) -> list[dict]:
    """校验并原子导入一组文件；返回逐文件结果。"""
    parsed = [parse_marketing_file(item) for item in files]
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    table_map = {
        SOURCE_PROVINCE: "marketing_region_snapshot",
        SOURCE_CITY: "marketing_region_snapshot", SOURCE_DISTRICT: "marketing_region_snapshot",
        SOURCE_DEALER: "marketing_dealer_snapshot", SOURCE_PROVIDER: "marketing_provider_snapshot",
        SOURCE_MEETING: "marketing_meeting_snapshot", SOURCE_ATTENDEE: "marketing_meeting_attendee",
        SOURCE_SUMMARY: "marketing_summary_snapshot",
        SOURCE_ADSP_WRITEOFF: "marketing_adsp_writeoff_snapshot",
    }
    results: list[dict] = []
    try:
        _ensure_schema(conn)
        conn.execute("BEGIN")
        for item in parsed:
            seen = conn.execute(
                "SELECT batch_id,status FROM marketing_import_batch WHERE file_sha256=?",
                (item.sha256,),
            ).fetchone()
            if seen and seen[1] == "success" and not force:
                results.append({"file": item.name, "source_type": item.source_type, "status": "skipped", "rows": item.row_count, "warnings": item.warnings})
                continue
            table = table_map[item.source_type]
            if item.source_type in (SOURCE_PROVINCE, SOURCE_CITY, SOURCE_DISTRICT):
                conn.execute(f"DELETE FROM {table} WHERE snapshot_date=? AND source_type=?", (item.snapshot_date, item.source_type))
            else:
                conn.execute(f"DELETE FROM {table} WHERE snapshot_date=?", (item.snapshot_date,))
            _insert_frame(conn, table, item.dataframe)
            conn.execute(
                """UPDATE marketing_import_batch SET status='superseded'
                   WHERE snapshot_date=? AND source_type=? AND status='success' AND file_sha256<>?""",
                (item.snapshot_date, item.source_type, item.sha256),
            )
            if seen:
                conn.execute("DELETE FROM marketing_import_batch WHERE file_sha256=?", (item.sha256,))
            conn.execute(
                """INSERT INTO marketing_import_batch
                   (batch_id,snapshot_date,source_type,source_file,file_sha256,imported_at,row_count,status,warnings_json)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (str(uuid.uuid4()), item.snapshot_date, item.source_type, item.name, item.sha256,
                 datetime.now().isoformat(timespec="seconds"), item.row_count, "success",
                 json.dumps(item.warnings, ensure_ascii=False)),
            )
            results.append({"file": item.name, "source_type": item.source_type, "status": "imported", "rows": item.row_count, "warnings": item.warnings})
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return results


def load_snapshot_tables(db_path: str | Path, snapshot_date: str) -> dict[str, pd.DataFrame]:
    conn = sqlite3.connect(str(db_path))
    try:
        _ensure_schema(conn)
        return {
            "region": pd.read_sql("SELECT * FROM marketing_region_snapshot WHERE snapshot_date=?", conn, params=(snapshot_date,)),
            "dealer": pd.read_sql("SELECT * FROM marketing_dealer_snapshot WHERE snapshot_date=?", conn, params=(snapshot_date,)),
            "provider": pd.read_sql("SELECT * FROM marketing_provider_snapshot WHERE snapshot_date=?", conn, params=(snapshot_date,)),
            "meeting": pd.read_sql("SELECT * FROM marketing_meeting_snapshot WHERE snapshot_date=?", conn, params=(snapshot_date,)),
            "attendee": pd.read_sql("SELECT * FROM marketing_meeting_attendee WHERE snapshot_date=?", conn, params=(snapshot_date,)),
            "summary": pd.read_sql("SELECT * FROM marketing_summary_snapshot WHERE snapshot_date=?", conn, params=(snapshot_date,)),
            "adsp_writeoff": pd.read_sql("SELECT * FROM marketing_adsp_writeoff_snapshot WHERE snapshot_date=?", conn, params=(snapshot_date,)),
            "batches": pd.read_sql("SELECT * FROM marketing_import_batch WHERE snapshot_date=? AND status='success' ORDER BY imported_at DESC", conn, params=(snapshot_date,)),
        }
    finally:
        conn.close()


def available_snapshots(db_path: str | Path) -> list[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        _ensure_schema(conn)
        rows = conn.execute("SELECT DISTINCT snapshot_date FROM marketing_import_batch WHERE status='success' ORDER BY snapshot_date DESC").fetchall()
        return [row[0] for row in rows]
    finally:
        conn.close()


def query_region_so(db_path: str | Path, snapshot_date: str) -> pd.DataFrame:
    """查询当年截至快照日的全量感知 SO，按城市/区县汇总。"""
    end = date.fromisoformat(snapshot_date)
    start = date(end.year, 1, 1)
    conn = sqlite3.connect(str(db_path))
    try:
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='view' AND name='product_flow_v'").fetchone()
        if not exists:
            return pd.DataFrame(columns=["city", "district", "so_ytd"])
        return pd.read_sql(
            """SELECT 上线城市 AS city, 上线区县_全 AS district,
                      COALESCE(SUM(最新分销价),0) AS so_ytd
               FROM product_flow_v
               WHERE date(上线时间) BETWEEN ? AND ? AND 上线城市 IS NOT NULL
               GROUP BY 上线城市, 上线区县_全""",
            conn, params=(start.isoformat(), end.isoformat()),
        )
    finally:
        conn.close()


def validate_reconciliation(parsed_files: Iterable[ParsedMarketingFile]) -> list[dict]:
    """跨文件勾稽检查；金额允许 1 元浮点误差。"""
    by_type = {item.source_type: item for item in parsed_files}
    checks: list[dict] = []

    def total(source: str, field: str) -> float:
        frame = by_type[source].dataframe
        return float(pd.to_numeric(frame[field], errors="coerce").fillna(0).sum())

    def summary_total(field: str) -> float:
        frame = by_type[SOURCE_SUMMARY].dataframe
        total_rows = frame[frame["business_type"] == "合计"]
        source = total_rows if not total_rows.empty else frame
        return float(pd.to_numeric(source[field], errors="coerce").fillna(0).sum())

    def add(label: str, left: float, right: float, tolerance: float = 1.0):
        checks.append({"检查项": label, "来源A": left, "来源B": right, "差异": left - right, "结果": "通过" if abs(left - right) <= tolerance else "需核对"})

    if SOURCE_CITY in by_type and SOURCE_DISTRICT in by_type:
        add("城市与区县总投入", total(SOURCE_CITY, "total_invest"), total(SOURCE_DISTRICT, "total_invest"))
        add("城市与区县推广会费用", total(SOURCE_CITY, "meeting_invest"), total(SOURCE_DISTRICT, "meeting_invest"))
    if SOURCE_PROVINCE in by_type and SOURCE_CITY in by_type:
        add("省区与城市总投入", total(SOURCE_PROVINCE, "total_invest"), total(SOURCE_CITY, "total_invest"))
        add("省区与城市推广会费用", total(SOURCE_PROVINCE, "meeting_invest"), total(SOURCE_CITY, "meeting_invest"))
    if SOURCE_CITY in by_type and SOURCE_DEALER in by_type:
        add("城市总投入与代理商已分摊费用", total(SOURCE_CITY, "total_invest"), total(SOURCE_DEALER, "total_invest"))
    if SOURCE_CITY in by_type and SOURCE_PROVIDER in by_type:
        add("城市服务商门头与服务商明细", total(SOURCE_CITY, "provider_storefront_invest"), total(SOURCE_PROVIDER, "storefront_invest"))
    if SOURCE_CITY in by_type and SOURCE_MEETING in by_type:
        add("城市推广会费用与会议明细", total(SOURCE_CITY, "meeting_invest"), total(SOURCE_MEETING, "meeting_expense"))
        add("城市会后30天产出与会议明细", total(SOURCE_CITY, "meeting_output_30d"), total(SOURCE_MEETING, "post_redpack_output"))
    if SOURCE_SUMMARY in by_type and SOURCE_ADSP_WRITEOFF in by_type:
        add("总览终端推广投入与ADSP预算", summary_total("terminal_invest"), total(SOURCE_ADSP_WRITEOFF, "budget_amount"))
    if SOURCE_SUMMARY in by_type and SOURCE_DEALER in by_type:
        add("总览总投入与代理商已分摊费用", summary_total("total_invest"), total(SOURCE_DEALER, "total_invest"))
    return checks
