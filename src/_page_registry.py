"""页面注册表：app.py / 智能搜索 / 侧边栏导航的单一数据源

每条记录包含：
  file:        相对 src/ 的页面文件路径
  title:       友好标题（侧边栏 + 搜索结果展示）
  icon:        emoji
  url:         URL 路径（短路径，纯 ASCII）
  group:       分组名
  description: 一句话描述（搜索匹配 + 工具提示）
  keywords:    检索关键词列表
  role:        可访问角色：None=所有用户 / 'admin'=仅 admin
               （产品经理 product_manager 在 None 页里再按 app_user_scope 的 page 白名单收窄，见 app.py）
"""
from __future__ import annotations

PAGES = [
    # ───── 🔍 智能搜索（默认首页）─────
    {
        'file': 'pages/00·🔍 智能搜索.py',
        'title': '智能搜索',
        'icon': '🔍',
        'url': 'search',
        'group': '智能搜索',
        'description': '输入你的需求（如「想看代理商业绩排名」），AI 自动推荐最贴近的功能页面',
        'keywords': ['搜索', '推荐', '导航', '帮助', 'AI', 'search', 'help', 'find'],
        'default': True,
    },

    # ───── 🎯 客户与服务商 ─────
    {
        'file': 'pages/02·🧭 服务商智能分类.py',
        'title': '服务商智能分类（核心）',
        'icon': '🧭',
        'url': 'dispatch',
        'group': '客户与服务商',
        'description': 'RFM 5 类 MECE 智能分群 + 拜访匹配矩阵 + 4 类派单清单（救援 / 漏跑 / 培育 / 空跑）',
        'keywords': ['RFM', '分群', '分级', '分类', '派单', '分配',
                     '高价值', '核心活跃', '流失风险', '培育', '已流失', '未采购',
                     '客户线索', '客户分类', '跑动指派'],
    },
    {
        'file': 'pages/05·🔭 服务商全景.py',
        'title': '服务商画像（雷达图）',
        'icon': '🔭',
        'url': 'provider',
        'group': '客户与服务商',
        'description': '单个服务商的 6 维雷达：量/额/产品多样性/忠诚率/活跃月/红包密度 + 标签管理',
        'keywords': ['画像', '雷达图', '单户', '透视', '某个服务商', '客户详情',
                     '打标', '标签', '马甲', '伞形', '低效签约', '无采购意向', '竞品Top'],
    },
    {
        'file': 'pages/22·📇 服务商全名册.py',
        'title': '服务商全名册',
        'icon': '📇',
        'url': 'provider-roster',
        'group': '客户与服务商',
        'description': '全省所有服务商一览表 + 标签 + 多维过滤；点客户名跳到画像页打标',
        'keywords': ['名册', '全部服务商', '所有客户', '名单', '清单', '总表', '标签过滤',
                     '低效服务商', '无意向', '竞品 Top'],
    },
    {
        'file': 'pages/14·🤝 服务商行为分析.py',
        'title': '服务商行为分析',
        'icon': '🤝',
        'url': 'provider-behavior',
        'group': '客户与服务商',
        'description': '基于红包数据识别流失服务商、混合采购异常',
        'keywords': ['流失', '混合采购', '行为', '异常', '掉单', '跑路'],
    },
    {
        'file': 'pages/21·⚔️ 竞品开拓.py',
        'title': '竞品开拓（特殊服务商）',
        'icon': '⚔️',
        'url': 'competitor',
        'group': '客户与服务商',
        'description': '海康/宇视核心服务商重点开拓 — 把竞品份额转化为大华',
        'keywords': ['竞品', '海康', '开拓', '战略', '抢市场', '转化', '宇视',
                     '特殊服务商'],
    },
    {
        'file': 'pages/16·🎁 红包排行榜.py',
        'title': '红包 ROI 分析',
        'icon': '🎁',
        'url': 'redpack-roi',
        'group': '营销动作 ROI',
        'description': '安装红包扫码 ROI 排名:服务商 / 业务员 / 区域多维度分析',
        'keywords': ['红包', 'ROI', '中奖', '扫码', '奖金', '激励', '排名', '排行'],
    },

    # ───── 🚀 行动执行 ─────
    {
        'file': 'pages/06·👣 业务员跑动评估.py',
        'title': '业务员跑动评估',
        'icon': '👣',
        'url': 'visit-eval',
        'group': '行动执行',
        'description': '业务员跑动 RFM-Impact 两步评估：选择合理性 + 结果有效性',
        'keywords': ['跑动', '业务员评估', '业务员表现', '跑得好不好', '打卡',
                     '拜访', '救援', '维护', '漏跑'],
    },
    {
        'file': 'pages/07·📋 跑动任务管理.py',
        'title': '跑动任务管理',
        'icon': '📋',
        'url': 'tasks',
        'group': '行动执行',
        'description': '管理员按月分配任务 → 业务员确认 → 月底核验完成度',
        'keywords': ['任务', '本月跑哪些', '今天该跑', '该跑哪些客户', '清单',
                     '分配', '派活', '核验', '完成度', '本月任务'],
    },
    {
        'file': 'pages/40·🛒 代理商进货指导.py',
        'title': '代理商进货指导',
        'icon': '🛒',
        'url': 'restock-advisor',
        'group': '产品经理工作',
        'description': '代理商×内部型号 进销存矩阵:滞销型号预警 + 动销补货预警(序列号级现存净额)',
        'keywords': ['进货', '补货', '滞销', '动销', '库存', '型号', '进货指导', '备货',
                     '压货', '周转', 'restock', '进销存'],
        'role': 'admin',
    },
    {
        'file': 'pages/45·🧮 智能备货配单.py',
        'title': '智能备货配单',
        'icon': '🧮',
        'url': 'smart-restock',
        'group': '产品经理工作',
        'description': '自动识别最新任务进度与下一备货周期；支持增删改型号、自动带分销价和整数数量对齐',
        'keywords': ['SI', '备货', '下单', '配单', '任务缺口', '进度条', '型号',
                     '分销价', '自动带价', '增删改', 'restock', 'order'],
    },
    {
        'file': 'pages/39·🚀 GTM管理.py',
        'title': 'GTM 管理',
        'icon': '🚀',
        'url': 'gtm',
        'group': '产品经理工作',
        'description': '月度产品攻坚:夜视王/无线/场景化 GTM 名单 → 跑动+动作跟踪 → 90天SO成效 → 分销经理考核',
        'keywords': ['GTM', '产品攻坚', '产单', '夜视王', '无线', '场景化', '攻坚名单',
                     '市场营销', '动作跟踪', 'go to market', '成效', '考核'],
        'role': 'admin',
    },

    # ───── 📊 经营看板 ─────
    {
        'file': 'pages/11·📊 SO环比分析.py',
        'title': 'SO 环比分析',
        'icon': '📊',
        'url': 'so-mom',
        'group': '经营看板',
        'description': '任意两个月对比，按城市/区县/产品系列/代理商/服务商五维下钻',
        'keywords': ['环比', '同比', 'SO', '走势', '增长率', '掉量',
                     '完成不好', '完成情况', '区县完成', '月度对比'],
    },
    {
        'file': 'pages/12·📈 产品流向分析.py',
        'title': '产品流向同环比',
        'icon': '📈',
        'url': 'product-flow',
        'group': '经营看板',
        'description': '同比 2025 vs 2026，按区县/代理商/服务商透视产品流向',
        'keywords': ['流向', '产品', '同比', '走势', '出货'],
    },
    {
        'file': 'pages/01·🗺️ 全省全景.py',
        'title': '全省全景',
        'icon': '🗺️',
        'url': 'province-panorama',
        'group': '经营看板',
        'description': '浙江全省经营总盘 + 11 地市横向对比：SO / 服务商 / 红包 / 跑动',
        'keywords': ['全省', '全景', '总盘', '11地市', '地市对比', '省区', '总览', '看板', '排行'],
    },
    {
        'file': 'pages/18·🌆 地市全景.py',
        'title': '地市全景',
        'icon': '🌆',
        'url': 'city-panorama',
        'group': '经营看板',
        'description': '一页看完整地市：SO 增长 / 服务商质量 / 红包投放 / 销售跑动',
        'keywords': ['地市', '全景', '城市', '一览', '总览', '看板'],
    },
    {
        'file': 'pages/19·🏘 区县全景.py',
        'title': '区县全景',
        'icon': '🏘',
        'url': 'district-panorama',
        'group': '经营看板',
        'description': '一页看完整区县：SO 增长 / 服务商质量 / 红包投放 / 销售跑动',
        'keywords': ['区县', '全景', '区域', '滨江', '萧山', '余杭', '看板'],
    },
    {
        'file': 'pages/20·🏢 代理商全景.py',
        'title': '代理商全景',
        'icon': '🏢',
        'url': 'dealer-panorama',
        'group': '经营看板',
        'description': '一页看完整代理商旗下:SO 增长 / 服务商质量 / 红包投放 / 销售跑动',
        'keywords': ['代理商', '一级', '全景', '上级', '常业', '万仞', '看板'],
    },
    {
        'file': 'pages/34·⏳ 待激活跟进.py',
        'title': '待激活跟进',
        'icon': '⏳',
        'url': 'pending-activation',
        'group': '经营看板',
        'description': '本月待激活客户跟进：按 月/城市/代理商/业务员 看待激活客户，跟踪达标(V2) + 跑动 + 备注',
        'keywords': ['待激活', '激活', '跟进', '本月', '看板', 'V2', '达标', '标签'],
    },
    {
        'file': 'pages/36·🎁 转化红包.py',
        'title': '转化红包管理',
        'icon': '🎁',
        'url': 'conversion-redpack',
        'group': '经营看板',
        'description': '大华业务员转化红包：配额/发放/解锁跟踪，业务员效果排行 + 待跟进券',
        'keywords': ['转化红包', '红包', '抵用券', '配额', '解锁', '激活', '业务员', '卡券'],
        'role': 'admin',
    },

    # ───── 🎯 省区总监 ─────
    {
        'file': 'pages/43·🎯 省区总监重点工作台.py',
        'title': '省区总监重点工作台',
        'icon': '🎯',
        'url': 'director-board',
        'group': '省区总监',
        'description': '两大专项(NP流转/无线产品)任务设置(全省目标按基线拆地市)+ 结果检查(进度条),按省/地市/代理商/分销经理筛选',
        'keywords': ['总监', '省区总监', '专项', 'NP', '无线', '任务', '目标拆分', '进度',
                     '覆盖率', '签约率', '铺货率', '结果检查', 'director'],
        'role': 'admin',
    },
    # (原 37·📶 无线渠道明细 页已整合进 43·🎯 省区总监重点工作台 的无线专项,页面移除)
    # (GTM 攻坚以「专项」下拉子项形式整合进 43·🎯 省区总监重点工作台;独立页仍在产品经理工作组)

    # ───── ⚖️ 产品经理工作 ─────
    {
        'file': 'pages/42·🧑‍💼 产品经理工作台.py',
        'title': '产品经理工作台',
        'icon': '🧑‍💼',
        'url': 'pm-workbench',
        'group': '产品经理工作',
        'description': '产品经理每周工作台:KPI 仪表盘(均衡/专项SO/滞销/数据新鲜度) + 上周成果自动统计 + 本周工作清单自动生成(可打勾留痕)',
        'keywords': ['工作台', '产品经理', '周报', '本周工作', '上周成果', '任务清单', 'KPI',
                     '待办', '差一口气', 'workbench'],
    },
    {
        'file': 'pages/44_pm_weekly_work.py',
        'title': '产品组一周工作',
        'icon': '🗓️',
        'url': 'pm-weekly-work',
        'group': '产品经理工作',
        'description': '按周查看固定工作和本周特定工作；产品组可分派，全省业务员可回填进度与结果',
        'keywords': ['产品组', '一周工作', '周工作', '固定工作', '特定工作', '分派',
                     '负责人', '业务员', '进度', '结果', 'weekly'],
    },
    {
        'file': 'pages/41·⚖️ 代理商产品均衡.py',
        'title': '代理商产品均衡管理',
        'icon': '⚖️',
        'url': 'product-balance',
        'group': '产品经理工作',
        'description': '代理商产品均衡:CCTV/数通/配套销售额占比 + 达标判定(CCTV≥70%且数通>3%或配套>10%→1%返点) + 季度快照跟踪/年度补齐',
        'keywords': ['产品均衡', '产品经理', 'CCTV占比', '数通', '配套', '返点', '返利',
                     '均衡返点', 'RP10', '产品线分析', '季度结算', '产品线'],
    },

    # ───── 🕵️ 数据治理 ─────
    {
        'file': 'pages/03·🕵️ 关联挖掘与假商治理.py',
        'title': '假商团伙挖掘',
        'icon': '🕵️',
        'url': 'fake-providers',
        'group': '数据治理',
        'description': '同电话/同地址/同步上线团伙识别 + 马甲/伞形/假签约 4 类假商',
        'keywords': ['马甲', '伞形', '假签约', '套上线', '关联', '团伙', '挤水分'],
    },
    {
        'file': 'pages/23·☂️ 伞形组管理.py',
        'title': '伞形组管理',
        'icon': '☂️',
        'url': 'umbrella-groups',
        'group': '数据治理',
        'description': '所有人工建立的伞形组列表 + 成员明细 + 启发式候选建议',
        'keywords': ['伞形', '组', '一伙', '关联', '老板', '伞形组',
                     '同一店主', '马甲', '套上线'],
    },

    # ───── 📣 营销动作 ROI ─────
    {
        'file': 'pages/26·📣 市场推广ROI.py',
        'title': '市场推广费用与 SO 效率',
        'icon': '📣',
        'url': 'marketing-roi',
        'group': '营销动作 ROI',
        'description': '统一导入市场推广六类数据，按城市/区县/代理商/会议/门头服务商分析费用、SO、费用强度和产出投入比',
        'keywords': ['市场推广', '费用', 'SO', 'ROI', '投入产出', '推广会', '沙龙', '门头',
                     '一级客户', '代理商', '区县', '营销效率', '数据导入'],
    },

    # ───── 🤖 AI 助手（仅 admin） ─────
    {
        'file': 'pages/10·📅 月度地市经营报告.py',
        'title': '月度经营报告 (AI)',
        'icon': '📅',
        'url': 'monthly-report',
        'group': 'AI 助手',
        'description': 'AI 生成地市月度经营 docx 报告：现状 4 章 + 行动 2 章',
        'keywords': ['月报', '报告', 'docx', '月度', '经营月报'],
        'role': 'admin',
    },
    {
        'file': 'pages/10·📰 经营周报.py',
        'title': '每周省区和地市周报 (AI)',
        'icon': '📰',
        'url': 'weekly-report',
        'group': 'AI 助手',
        'description': 'AI 生成全省/地市经营周报：周环比+完成率+激活+跑动+三专项，HTML手机版+docx',
        'keywords': ['周报', '经营周报', '全省周报', '地市周报', 'docx', '周度'],
        'role': 'admin',
    },
    {
        'file': 'pages/25·📊 产品维度报告.py',
        'title': '产品维度报告 (AI)',
        'icon': '📊',
        'url': 'product-report',
        'group': 'AI 助手',
        'description': '产品子系列 × 区县/代理商/服务商 全景分析,自动产出 概览 + 全量 双 HTML',
        'keywords': ['产品', '子系列', '产品报告', '产品维度', 'HTML', '夜视王', '4G', '产品分析'],
        'role': 'admin',
    },
    {
        'file': 'pages/28·🔍 爆款穿透分析.py',
        'title': '爆款穿透分析 (AI)',
        'icon': '🔍',
        'url': 'hotsku-report',
        'group': 'AI 助手',
        'description': '输入型号关键字,优先匹配内部型号,自动 fallback 外部型号,做 9 章 SKU 穿透分析',
        'keywords': ['爆款', '型号', '穿透', '内部型号', '外部型号', 'SKU', '关键字', 'T8', 'AOV', '4G'],
        'role': 'admin',
    },
    {
        'file': 'pages/38·🧩 产品归因分析.py',
        'title': '产品归因分析 (AI)',
        'icon': '🧩',
        'url': 'product-attribution',
        'group': 'AI 助手',
        'description': '按周期从产品子系列穿透(服务商/代理商/地市/型号),LLM 归因/归咎+9因素证据:谁涨谁跌、为什么、该关注什么',
        'keywords': ['归因', '归咎', '产品', '子系列', '换代', '双光', '红外', '为什么涨', '为什么跌', '经营归因', '产品分析', '增量', '做得好', '做得不好'],
        'role': 'admin',
    },
    {
        'file': 'pages/39·📥 NP转入进展.py',
        'title': 'NP 转入进展',
        'icon': '📥',
        'url': 'np-transfer',
        'group': '经营看板',
        'description': 'NP 流转/一站式转入客户的转化漏斗(转入→报备→相关→签约→激活)+ 地市分布 + 明细,管理者看进展',
        'keywords': ['NP', 'np转入', '转入客户', '流转', '一站式', '潜客', '进展', '漏斗', '转化', '签约', '激活'],
        'role': 'admin',
    },

    # ───── 🎯 产品专项攻坚(M13)─────
    {
        'file': 'pages/29·🌙 夜视王专项.py',
        'title': '夜视王专项',
        'icon': '🌙',
        'url': 'focus-yeshiwang',
        'group': '产品专项攻坚',
        'description': '夜视王专项现状(多源穿透)+ 攻坚项目管理。对标海康臻全彩',
        'keywords': ['夜视王', '专项', '攻坚', '臻全彩', '海康', '夜视'],
    },
    {
        'file': 'pages/30·📡 无线专项.py',
        'title': '无线专项',
        'icon': '📡',
        'url': 'focus-wireless',
        'group': '产品专项攻坚',
        'description': '无线 / 4G / 物联 专项现状 + 攻坚',
        'keywords': ['无线', '4G', '物联', '专项', '攻坚'],
    },
    {
        'file': 'pages/31·🎯 场景化专项.py',
        'title': '场景化专项',
        'icon': '🎯',
        'url': 'focus-scene',
        'group': '产品专项攻坚',
        'description': '场景化 / 行业 / 特种安装 专项现状 + 攻坚',
        'keywords': ['场景化', '行业', '特种', '专项', '攻坚'],
    },

    # ───── 📄 SMB 主管周报(M2)─────
    {
        'file': 'pages/32·📄 SMB周报.py',
        'title': 'SMB 主管周报',
        'icon': '📄',
        'url': 'smb-weekly',
        'group': '管理周报',
        'description': '省区 SMB 主管周报 — 4 章节(分销商布局 / 服务商管理 / 技术行销 / 其他)',
        'keywords': ['周报', 'SMB', '主管', '分销商', '服务商', '激活', '签约', '专项'],
    },

    # ───── 📦 库存进销存(M5)─────
    {
        'file': 'pages/00·📦 库存进销存.py',
        'title': '库存进销存',
        'icon': '📦',
        'url': 'inventory',
        'group': 'Admin 工具',
        'description': '代理商盘库明细分析 — 库存总览 / 呆滞品攻坚 / 库存周转 / 季度趋势',
        'keywords': ['库存', '进销存', '盘库', '呆滞', '周转', 'inventory', '趋势'],
        'role': 'admin',
    },
    {
        'file': 'pages/00·🗑️ 呆滞库存清理.py',
        'title': '呆滞库存清理专项',
        'icon': '🗑️',
        'url': 'stale-inventory',
        'group': 'Admin 工具',
        'description': '代理商呆滞库存清理进度 — 总览 / 月度趋势 / 各代理商进度条 / 型号攻坚 / Word 周报',
        'keywords': ['呆滞', '库存', '清理', '专项', '进度', '周报', 'stale', '攻坚', '清库'],
        'role': 'admin',
    },

    # ───── 🎫 Admin 待办工作流 ─────
    {
        'file': 'pages/00·🎫 Admin待办.py',
        'title': 'Admin 待办',
        'icon': '🎫',
        'url': 'admin-todo',
        'group': 'Admin 工具',
        'description': '问题驱动的协作工作台 — Admin 提问/AI 调研/Admin 定思路/AI 拆任务/Admin 审批',
        'keywords': ['admin', '待办', 'issue', '问题', '任务', 'todo', '工作流'],
        'role': 'admin',
    },

    # ───── ⚔️ 战役管理 ─────
    {
        'file': 'pages/00·⚔️ 批发类100%铺货战役.py',
        'title': '批发类 100% 铺货',
        'icon': '⚔️',
        'url': 'campaign-wholesale100',
        'group': '⚔️ 战役管理',
        'description': '战役:让 902 家批发类服务商全部完成铺货(当前 13.7% → 100%)',
        'keywords': ['批发', '铺货', '战役', '攻坚', 'campaign', 'wholesale'],
    },
    {
        'file': 'pages/00·🎯 海康份额抢夺战役.py',
        'title': '海康份额抢夺',
        'icon': '🎯',
        'url': 'campaign-hikvision-share',
        'group': '⚔️ 战役管理',
        'description': '战役:269 家海康强势/竞品TOP 服务商,把大华占比从 X% 提升到 Y%',
        'keywords': ['海康', '竞品', '抢夺', '份额', '占比', 'hikvision'],
    },
    {
        'file': 'pages/08·📋 会议纪要校验.py',
        'title': '会议纪要校验 (AI)',
        'icon': '📋',
        'url': 'audit',
        'group': 'AI 助手',
        'description': '上传会议纪要 → AI 抽取核心声明 → 对比 DB 核实数据',
        'keywords': ['纪要', '校验', '核对', '一致性', '会议'],
        'role': 'admin',
    },
    {
        'file': 'pages/33·🔎 周报校验.py',
        'title': '周报校验 (AI)',
        'icon': '🔎',
        'url': 'weekly-check',
        'group': 'AI 助手',
        'description': '粘上周+本周 SMB 主管周报 → AI 调生产库逐项核实数据/口径/待办兑现 → 出点评卡片',
        'keywords': ['周报', '校验', '点评', '主管', '待办', '兑现', '打分', 'SMB'],
        'role': 'admin',
    },
    {
        'file': 'pages/09·🛠️ AI 代码助手.py',
        'title': 'AI 代码助手 (V3)',
        'icon': '🛠️',
        'url': 'code-agent',
        'group': 'AI 助手',
        'description': 'Docker 沙箱里跑 Claude，AI 自己写 Python/SQL/画图解你的问题',
        'keywords': ['AI', 'Claude', '代码', '编程', '问答', '自定义'],
        'role': 'admin',
    },

    # ───── ⚙️ 系统 / 实验室（admin only）─────
    # 实验性功能（admin 验证，待稳定后晋升到正式分组）
    {
        'file': 'pages/13·🏆 代理商能力评分.py',
        'title': '代理商能力评分（实验中）',
        'icon': '🏆',
        'url': 'dealer-score',
        'group': '系统',
        'description': '一级代理商 F7 综合健康度评分（实验性功能）',
        'keywords': ['代理商', '一级', '评分', '健康度', '能力', '排名', '上级', '实验'],
        'role': 'admin',
    },
    {
        'file': 'pages/17·⚔️ 大PK.py',
        'title': '业务员 / 区域 大 PK（实验中）',
        'icon': '⚔️',
        'url': 'pk',
        'group': '系统',
        'description': '业务员之间、地市之间业绩 PK，排行榜+雷达对比（实验性功能）',
        'keywords': ['PK', '排行', '榜单', '对比', '谁第一', '业绩比较', '排名', '实验'],
        'role': 'admin',
    },
    {
        'file': 'pages/15·🎯 销售机会发现器.py',
        'title': '销售机会发现（实验中)',
        'icon': '🎯',
        'url': 'opportunities',
        'group': '系统',
        'description': '找未开发客户、待提级客户、增量空间，给业务员派活（实验性功能）',
        'keywords': ['机会', '增量', '提级', '空白', '潜力', '挖掘', '新客户', '实验'],
        'role': 'admin',
    },
    {
        'file': 'pages/04·💪 动作刺激分析.py',
        'title': '拜访激励效果（实验中）',
        'icon': '💪',
        'url': 'incentive',
        'group': '系统',
        'description': '拜访打卡 → 上线 时间窗内的影响分析（实验性功能）',
        'keywords': ['拜访', '激励', '刺激', '效果', '上线漏斗', '动作', '实验'],
        'role': 'admin',
    },
    # 系统配置 / 管理
    {
        'file': '_home.py',
        'title': '数据导入与概览',
        'icon': '🏠',
        'url': 'data-import',
        'group': '系统',
        'description': '导入主表 / 安装红包 / 服务商沙盘 / 签约 / 业务员打卡 / KPI 等数据 + 查看库表概况',
        'keywords': ['数据导入', '上传', 'excel', '导入', 'KPI', '数据库', '概览'],
        'role': 'admin',
    },
    {
        'file': 'pages/00·🔐 用户管理.py',
        'title': '用户与权限',
        'icon': '🔐',
        'url': 'users',
        'group': '系统',
        'description': '管理用户账号、角色、数据权限 scope',
        'keywords': ['用户', '权限', '账号', '密码', '角色', 'scope', '管理'],
        'role': 'admin',
    },
    {
        'file': 'pages/00·🏷️ 标签字典.py',
        'title': '标签字典',
        'icon': '🏷️',
        'url': 'tag-dict',
        'group': '系统',
        'description': '维护服务商标签字典：新增 / 改名 / 启停 / 排序（不删除）',
        'keywords': ['标签', '字典', '元数据', '马甲', '伞形', '低效签约',
                     '无采购意向', '竞品Top', '下月重点', 'tag'],
        'role': 'admin',
    },
    {
        'file': 'pages/🧪 实验室.py',
        'title': '实验室',
        'icon': '🧪',
        'url': 'lab',
        'group': '系统',
        'description': '实验性功能 / 自定义 SQL 查询 / 临时分析',
        'keywords': ['实验', 'SQL', '自定义', '临时'],
        'role': 'admin',
    },

    # ───── 📋 代理商经营简报 ─────
    {
        'file': 'pages/31·📋 代理商经营简报.py',
        'title': '代理商经营简报',
        'icon': '📋',
        'url': 'dealer-briefing',
        'group': '管理周报',
        'description': '选时间范围自动生成全省代理商经营简报 HTML：分地市排名 + 每代理商简报(本市/本省SO、红包、服务商分级V0-V5、新注册/新激活、跑动、本年周SO曲线、本市销冠)，固定取数 + LLM 写本周小结，可导出图片',
        'keywords': ['代理商', '简报', '周报', '月报', '经营简报', '排名', '销冠', '战报', '代理商月报', '代理商周报', 'briefing'],
        'role': 'admin',
    },
    {
        'file': 'pages/33·🩺 代理商经营诊断.py',
        'title': '代理商经营诊断（仅admin）',
        'icon': '🩺',
        'url': 'dealer-diagnosis',
        'group': '管理周报',
        'description': '全省代理商按规模/设备/人员聚类，以3个匿名标杆(标杆1/2/3)对标；选客户+对标对象→LLM写诊断小结/追赶里程碑→七维雷达评分图，可导出图片。仅admin',
        'keywords': ['代理商', '诊断', '对标', '经营诊断', '标杆', '雷达', '评分', '里程碑', '追赶', '能力', 'benchmark', 'diagnosis'],
        'role': 'admin',
    },
    {
        'file': 'pages/00·🎯 作战方案生成器.py',
        'title': '代理商作战方案生成器（仅admin）',
        'icon': '🎯',
        'url': 'battle-plan',
        'group': '管理周报',
        'description': '输入代理商+目标月→目标全景(现状/今年/明年翻倍)+逐月渐进爬坡(漏斗+里程碑曲线)+当月目标(基准/挑战/实际)+激励方案(基数×单价·挑战值机制)。可填实际目标自动带入激励基数、导出Excel。仅admin',
        'keywords': ['作战方案', '下月方案', '月度方案', '目标分解', '激励方案', '月度激励', '代理商', '挑战值', '万仞', '目标全景', '进度条', '生成器', 'battle', 'plan'],
        'role': 'admin',
    },
    {
        'file': 'pages/00·🏙️ 城市月度规划.py',
        'title': '城市月度规划（仅admin）',
        'icon': '🏙️',
        'url': 'city-plan',
        'group': '📒 月度规划',
        'description': '城市枢纽：复盘进展 → 定城市SO目标 → 拆SO任务到各代理商(下发) → 汇总各家激励(上收)。与代理商月度规划同源。仅admin',
        'keywords': ['城市规划', '月度规划', '复盘', '拆解', '汇总', '激励预算', '对赌', '资源规划', '杭州', 'city', 'plan'],
        'role': 'admin',
    },
    {
        'file': 'pages/00·📒 代理商月度规划.py',
        'title': '代理商月度规划（仅admin）',
        'icon': '📒',
        'url': 'dealer-plan',
        'group': '📒 月度规划',
        'description': '接城市下发的SO任务 → 定本月关键指标(激活/留存/升档/品类)+激励 → 保存(回传城市汇总)。仅admin',
        'keywords': ['代理商规划', '月度规划', '关键指标', '激活', '留存', '升档', '激励', 'SO任务', '对赌', 'dealer', 'plan'],
        'role': 'admin',
    },
]


def grouped() -> dict:
    """按 group 分组返回，保持原顺序"""
    out = {}
    for p in PAGES:
        out.setdefault(p['group'], []).append(p)
    return out


def find_by_url(url: str) -> dict | None:
    for p in PAGES:
        if p.get('url') == url:
            return p
    return None


def filter_for_role(role: str) -> list[dict]:
    """按角色过滤可访问页面"""
    out = []
    for p in PAGES:
        req = p.get('role')
        if req == 'admin' and role != 'admin':
            continue
        out.append(p)
    return out
