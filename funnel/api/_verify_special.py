"""专项目标：省设置向下继承、市设置省不可见、目标卷积、取消权限。"""
from __future__ import annotations

import tempfile
from pathlib import Path

from api import db as funnel_db
from api import special
from api.factors import SEED_DEFS


def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    funnel_db.DB_PATH = tmp / "funnel.db"
    funnel_db.init_schema()
    funnel_db.seed_factor_defs(SEED_DEFS)

    pk = "2026-07"
    prov = "浙江//"
    hz = "浙江/杭州市/"
    xihu = "浙江/杭州市/西湖区"
    nb = "浙江/宁波市/"

    # 省设置跑动合计
    d = special.add_pin(prov, pk, "a2t_visit", target=1000)
    assert any(i["factor_id"] == "a2t_visit" for i in d["items"]), d
    print("✅ 省设置专项")

    # 杭州自动可见，来源=省设置；不能取消
    hz_d = special.build_special(hz, pk)
    hit = next(i for i in hz_d["items"] if i["factor_id"] == "a2t_visit")
    assert hit["source_label"] == "省设置", hit
    assert hit["can_remove"] is False, hit
    print("✅ 地市继承省专项且不可删")

    xihu_d = special.build_special(xihu, pk)
    assert any(i["factor_id"] == "a2t_visit" for i in xihu_d["items"])
    print("✅ 区县继承省专项")

    # 杭州填目标
    special.set_target(hz, pk, "a2t_visit", 200)
    special.set_target(nb, pk, "a2t_visit", 150)
    # 宁波也该看见（继承）
    assert any(i["factor_id"] == "a2t_visit" for i in special.build_special(nb, pk)["items"])

    # 省看卷积（依赖 geo_tree；无生产库时 children 可能空）
    prov_d = special.build_special(prov, pk)
    visit = next(i for i in prov_d["items"] if i["factor_id"] == "a2t_visit")
    assert visit["target"] == 1000, visit
    if visit.get("rollup") and visit["rollup"].get("n_set"):
        assert visit["rollup"]["sum"] == 350, visit["rollup"]
        print("✅ 省级下级目标卷积", visit["rollup"]["sum"])
    else:
        print("⚠️ 无生产库地区树，跳过卷积断言", visit.get("rollup"))

    # 市自建专项：省看不见
    special.add_pin(hz, pk, "a2t_visit_dahua", target=80)
    assert any(i["factor_id"] == "a2t_visit_dahua"
               for i in special.build_special(hz, pk)["items"])
    assert any(i["factor_id"] == "a2t_visit_dahua"
               for i in special.build_special(xihu, pk)["items"])
    assert not any(i["factor_id"] == "a2t_visit_dahua"
                   for i in special.build_special(prov, pk)["items"])
    print("✅ 市设置专项：区县可见、省不可见")

    # 杭州可取消本市专项；不可取消省专项
    special.remove_pin(hz, pk, "a2t_visit_dahua")
    assert not any(i["factor_id"] == "a2t_visit_dahua"
                   for i in special.build_special(hz, pk)["items"])
    try:
        special.remove_pin(hz, pk, "a2t_visit")
        raise AssertionError("should not remove province pin")
    except ValueError:
        print("✅ 地市不能取消省设置")

    # 比率因子可设置但不卷积
    special.add_pin(prov, pk, "a2t_meet", target=50)
    meet = next(i for i in special.build_special(prov, pk)["items"]
                if i["factor_id"] == "a2t_meet")
    assert meet["additive"] is False
    assert meet["rollup"] is None
    print("✅ 比率因子无卷积")

    # factor_targets 同步
    st = funnel_db.load_latest_state(hz, pk) or {}
    ft = st.get("factor_targets") or {}
    assert any("a2t/visit" in k and v == 200 for k, v in ft.items()), ft
    print("✅ 同步 factor_targets")

    print("\n全部校验通过")


if __name__ == "__main__":
    main()
