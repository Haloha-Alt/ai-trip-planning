# -*- coding: utf-8 -*-
"""生成答辩 PPT（AI 出行规划与行程调整助手）。

跑法（用托管 Python，它自带 python-pptx；项目本身仍然零第三方依赖）：

    C:\\Users\\18055\\.workbuddy\\binaries\\python\\versions\\3.13.12\\python.exe docs\\submission\\make_deck.py

产物：docs/submission/AI出行规划器-答辩.pptx（16:9，12 页）

为什么用脚本而不是手搓：改一处文案就重跑一次，保证 PPT 与 `答辩讲稿.md` 的数字始终一致。
"""

from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Emu, Inches, Pt

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "submission" / "AI出行规划器-答辩.pptx"
SHOT = ROOT / "_preview" / "ui-check.png"

# ---------------------------------------------------------------- 设计（学术风：蓝白克制）
NAVY = RGBColor(0x14, 0x38, 0x5C)
NAVY_L = RGBColor(0x2C, 0x5F, 0x8A)
INK = RGBColor(0x1F, 0x29, 0x37)
MUTED = RGBColor(0x5B, 0x6B, 0x7C)
LINE = RGBColor(0xD7, 0xDE, 0xE8)
SOFT = RGBColor(0xF2, 0xF5, 0xF9)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
ACCENT = RGBColor(0xC2, 0x41, 0x0C)
ACCENT_SOFT = RGBColor(0xFB, 0xEE, 0xE7)

FONT = "微软雅黑"

W, H = Inches(13.333), Inches(7.5)
ML, MR = Inches(0.9), Inches(0.9)
CW = W - ML - MR                     # 内容宽度


def _text(box, runs, size=14, color=INK, bold=False, align=PP_ALIGN.LEFT,
          line=1.35, space_after=4, anchor=MSO_ANCHOR.TOP):
    """往一个 shape 里写多段文字；runs 是 [(文字, 覆盖参数dict), ...]。"""
    tf = box.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = 0
    tf.margin_top = tf.margin_bottom = 0
    for i, item in enumerate(runs):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.line_spacing = line
        p.space_after = Pt(space_after)
        text, kw = item if isinstance(item, tuple) and len(item) == 2 else (item, {})
        if isinstance(text, tuple):          # ("文字",) 这种单元素元组
            text = text[0]
        r = p.add_run()
        r.text = text
        f = r.font
        f.name = kw.get("font", FONT)
        f.size = Pt(kw.get("size", size))
        f.bold = kw.get("bold", bold)
        f.color.rgb = kw.get("color", color)
    return box


def rect(slide, x, y, w, h, fill=SOFT, line_color=None, shape=MSO_SHAPE.RECTANGLE,
         line_w=1.0):
    s = slide.shapes.add_shape(shape, x, y, w, h)
    if fill is None:
        s.fill.background()
    else:
        s.fill.solid()
        s.fill.fore_color.rgb = fill
    if line_color is None:
        s.line.fill.background()
    else:
        s.line.color.rgb = line_color
        s.line.width = Pt(line_w)
    s.shadow.inherit = False
    return s


def tb(slide, x, y, w, h):
    return slide.shapes.add_textbox(x, y, w, h)


def page(title, kicker="", sub=""):
    """统一母版：kicker / 标题 / 细线 / 页脚页码。返回 (slide, 内容区顶部 y)。"""
    s = prs.slides.add_slide(prs.slide_layouts[6])
    idx = len(prs.slides.__iter__.__self__._sldIdLst)      # 当前页序号
    if kicker:
        _text(tb(s, ML, Inches(0.46), CW, Inches(0.26)), [(kicker,)],
              size=11, color=ACCENT, bold=True)
    _text(tb(s, ML, Inches(0.72), CW, Inches(0.62)), [(title,)],
          size=27, color=NAVY, bold=True)
    if sub:
        _text(tb(s, ML, Inches(1.30), CW, Inches(0.30)), [(sub,)],
              size=13, color=MUTED)
    ln = rect(s, ML, Inches(1.62), CW, Emu(12000), fill=LINE)
    ln.line.fill.background()

    # 页脚
    rect(s, ML, Inches(6.86), CW, Emu(10000), fill=LINE)
    _text(tb(s, ML, Inches(6.98), Inches(8.0), Inches(0.3)),
          [("AI 出行规划与行程调整助手 · 大连理工大学",)], size=10, color=MUTED)
    _text(tb(s, W - MR - Inches(1.4), Inches(6.98), Inches(1.4), Inches(0.3)),
          [("%02d / 12" % idx,)], size=10, color=MUTED, align=PP_ALIGN.RIGHT)
    return s, Inches(1.86)


def bullets(s, x, y, w, items, size=14, gap=0.06):
    """items = [(主句, 补充说明 or ''), ...]，主句加粗、补充灰字。"""
    cy = y
    for head, tail in items:
        h = Inches(0.34 if not tail else 0.62)
        box = tb(s, x, cy, w, h)
        runs = [(head, {"bold": True, "size": size, "color": INK})]
        if tail:
            runs.append(("\n" + tail, {"size": size - 2, "color": MUTED}))
        _text(box, runs, size=size, line=1.5, space_after=0)
        cy = cy + h + Inches(gap)
    return cy


def metric(s, x, y, w, number, label, note=""):
    """大数字锚点：数字巨大、标签小、说明灰。"""
    rect(s, x, y, w, Inches(1.42), fill=SOFT)
    _text(tb(s, x + Inches(0.24), y + Inches(0.18), w - Inches(0.4), Inches(0.6)),
          [(number,)], size=30, color=NAVY, bold=True)
    _text(tb(s, x + Inches(0.24), y + Inches(0.80), w - Inches(0.4), Inches(0.26)),
          [(label,)], size=12, color=INK, bold=True)
    if note:
        _text(tb(s, x + Inches(0.24), y + Inches(1.06), w - Inches(0.4), Inches(0.26)),
              [(note,)], size=10, color=MUTED)


prs = Presentation()
prs.slide_width, prs.slide_height = W, H

# ================================================================ 01 封面
s = prs.slides.add_slide(prs.slide_layouts[6])
rect(s, 0, 0, W, H, fill=NAVY)
rect(s, 0, Inches(4.34), W, Emu(26000), fill=RGBColor(0x2C, 0x5F, 0x8A))
_text(tb(s, ML, Inches(1.28), CW, Inches(0.3)),
      [("《AI 大模型及其运用》课程项目 · 成果汇报",)], size=13,
      color=RGBColor(0xA9, 0xC3, 0xDC), bold=True)
_text(tb(s, ML, Inches(1.86), CW, Inches(1.5)),
      [("AI 日常出行规划",)], size=52, color=WHITE, bold=True)
_text(tb(s, ML, Inches(2.92), CW, Inches(1.0)),
      [("与行程调整助手",)], size=52, color=WHITE, bold=True)
_text(tb(s, ML, Inches(4.72), CW, Inches(0.9)),
      [("一句话说清今天要做什么 → 守住时间、预算、开关门的行程 → 随时能改",
        {"size": 16, "color": RGBColor(0xD6, 0xE3, 0xF0)})], line=1.5)
_text(tb(s, ML, Inches(6.36), CW, Inches(0.5)),
      [("场景：日常出行（校园生活圈 46 个点） ｜ 小组 2 人 ｜ 2026-10-30",
        {"size": 12, "color": RGBColor(0xA9, 0xC3, 0xDC)})])

# ================================================================ 02 问题
s, y = page("真实问题不是「去哪玩」，而是「今天怎么过」", "01 问题",
            "学生的日常出行是一串必须做完的琐事，且每个点都有开关门时间")
rect(s, ML, y, CW, Inches(1.02), fill=SOFT)
rect(s, ML, y, Emu(30000), Inches(1.02), fill=ACCENT)
_text(tb(s, ML + Inches(0.28), y + Inches(0.20), CW - Inches(0.5), Inches(0.62)),
      [("「上午去图书馆自习，中午食堂吃饭，顺便取快递，别太赶」",
        {"size": 20, "color": NAVY, "bold": True})])

cols = [
    ("目的地", "图书馆 / 食堂 / 快递点", "兴趣标签匹配"),
    ("先后偏好", "「顺便」= 顺路", "途经点强制顺序"),
    ("软约束", "「别太赶」= 每天少两站", "节奏 → 站数系数"),
    ("隐含硬约束", "07:30 开门、19:30 关门、周一闭馆", "关门就跳过"),
]
cw = Inches(2.78)
for i, (t, d, how) in enumerate(cols):
    x = ML + (cw + Inches(0.22)) * i
    rect(s, x, y + Inches(1.32), cw, Inches(1.62), fill=WHITE, line_color=LINE)
    _text(tb(s, x + Inches(0.2), y + Inches(1.48), cw - Inches(0.36), Inches(0.3)),
          [(t,)], size=13, color=ACCENT, bold=True)
    _text(tb(s, x + Inches(0.2), y + Inches(1.80), cw - Inches(0.36), Inches(0.7)),
          [(d,)], size=12, color=INK, line=1.45)
    _text(tb(s, x + Inches(0.2), y + Inches(2.50), cw - Inches(0.36), Inches(0.3)),
          [("→ " + how,)], size=10, color=MUTED)

_text(tb(s, ML, y + Inches(3.22), CW, Inches(0.9)),
      [("人手排这个不是算不出来，是", {"size": 15, "color": INK}),
       ("算不完", {"size": 15, "color": ACCENT, "bold": True}),
       ("：46 个点的开关门、30 多个点对的步速、预算、还有「别太赶」；"
        "而只要有人说一句「第 2 站太贵了」，就得重排一遍。",
        {"size": 15, "color": INK})], line=1.6)

# ================================================================ 03 数据与场景
s, y = page("场景与数据：一个校园生活圈，五套可切换的情形", "02 场景",
            "数据全部外置成 CSV 与 TOML，代码里不出现任何业务数值")
metric(s, ML, y, Inches(2.86), "46", "个生活圈站点", "教学楼 / 食堂 / 快递 / 校医院…")
metric(s, ML + Inches(3.10), y, Inches(2.86), "145", "条真实步行耗时", "高德 API 实测，运行期零联网")
metric(s, ML + Inches(6.20), y, Inches(2.86), "5", "套场景配置", "daily / commute / errand…")
metric(s, ML + Inches(9.30), y, Inches(3.13), "6", "种日常情形", "含雨天、赶时间、办事日")

_text(tb(s, ML, y + Inches(1.78), CW, Inches(0.34)),
      [("每条数据都写清来源与「是否已核实」", {"size": 14, "color": NAVY, "bold": True})])
rows = [
    ("站点基础信息", "poi_id / 名称 / 分类 / 坐标 / 地址", "种子数据，坐标为校区近似值"),
    ("可达性", "open_time / close_time / closed_weekdays", "校医院 17:00 关门、博物馆周一闭馆"),
    ("花费", "cost + cost_unit（人均 / 单次 / 每晚）", "任务书要求：费用必须有计价单位"),
    ("可信度", "source / source_date", "未标日期的点自动进「待核实清单」上屏"),
]
ry = y + Inches(2.22)
for name, cols_txt, note in rows:
    rect(s, ML, ry, CW, Inches(0.56), fill=SOFT)
    _text(tb(s, ML + Inches(0.22), ry + Inches(0.14), Inches(1.5), Inches(0.3)),
          [(name,)], size=12, color=NAVY, bold=True)
    _text(tb(s, ML + Inches(1.86), ry + Inches(0.14), Inches(4.5), Inches(0.3)),
          [(cols_txt,)], size=11, color=INK)
    _text(tb(s, ML + Inches(6.60), ry + Inches(0.14), Inches(5.4), Inches(0.3)),
          [(note,)], size=11, color=MUTED)
    ry = ry + Inches(0.62)

# ================================================================ 04 架构
s, y = page("架构：入口 / 用例 / 引擎 / 数据，四层单向", "03 设计",
            "三条纪律：契约冻结、分层单向、业务数值只准写在配置里")

layers = [
    ("入口层", "CLI main.py ｜ Streamlit ui/app.py ｜ 网页 api/server.py + web/index.html", NAVY),
    ("用例层", "service/usecases.py：options / build_request / plan_from_text / adjust_plan", NAVY_L),
]
ly = y
for name, desc, color in layers:
    rect(s, ML, ly, CW, Inches(0.72), fill=SOFT)
    rect(s, ML, ly, Emu(46000), Inches(0.72), fill=color)
    _text(tb(s, ML + Inches(0.24), ly + Inches(0.12), Inches(1.1), Inches(0.3)),
          [(name,)], size=12, color=color, bold=True)
    _text(tb(s, ML + Inches(1.40), ly + Inches(0.13), CW - Inches(1.7), Inches(0.4)),
          [(desc,)], size=11, color=INK)
    ly = ly + Inches(0.84)

# 引擎层：三个模块并排
rect(s, ML, ly, CW, Inches(1.52), fill=WHITE, line_color=NAVY_L, line_w=1.2)
_text(tb(s, ML + Inches(0.24), ly + Inches(0.12), Inches(1.1), Inches(0.3)),
      [("引擎层",)], size=12, color=NAVY_L, bold=True)
_text(tb(s, ML + Inches(1.40), ly + Inches(0.12), CW - Inches(1.7), Inches(0.3)),
      [("engine/planner.py 是唯一入口：选策略 → 出行程 → 校验 → 评分",)], size=11, color=INK)
mods = [
    ("strategies.py", "策略注册表：stub / random / greedy / two_opt"),
    ("constraints.py", "硬约束校验 + 四维评分（可行性扣分）"),
    ("models.py", "冻结契约 v1.4：字段只增不改"),
]
mw = Inches(3.66)
for i, (m, d) in enumerate(mods):
    x = ML + Inches(0.24) + (mw + Inches(0.22)) * i
    rect(s, x, ly + Inches(0.56), mw, Inches(0.80), fill=SOFT)
    _text(tb(s, x + Inches(0.16), ly + Inches(0.66), mw - Inches(0.3), Inches(0.28)),
          [(m,)], size=11, color=NAVY, bold=True)
    _text(tb(s, x + Inches(0.16), ly + Inches(0.94), mw - Inches(0.3), Inches(0.4)),
          [(d,)], size=10, color=MUTED, line=1.35)
ly = ly + Inches(1.64)

rect(s, ML, ly, CW, Inches(0.72), fill=SOFT)
rect(s, ML, ly, Emu(46000), Inches(0.72), fill=NAVY_L)
_text(tb(s, ML + Inches(0.24), ly + Inches(0.12), Inches(1.1), Inches(0.3)),
      [("数据层",)], size=12, color=NAVY_L, bold=True)
_text(tb(s, ML + Inches(1.40), ly + Inches(0.13), CW - Inches(1.7), Inches(0.4)),
      [("data/loader.py（唯一出口）→ data/cities/<key>/*.csv　·　城市 = 数据路由，不是 if-else",)],
      size=11, color=INK)

_text(tb(s, ML, Inches(6.28), CW, Inches(0.4)),
      [("换算法 = 写一个函数 + 注册一行；换场景/城市 = 改 TOML / CSV；两者都不碰其它层。",
        {"size": 12, "color": ACCENT, "bold": True})])

# ================================================================ 05 算法
s, y = page("算法：只在可行解里挑「此刻最划算」的一站", "04 实现",
            "优化什么就评什么 —— 贪心的目标函数用的是评分函数同一套权重")

left = Inches(7.1)
rect(s, ML, y, left, Inches(2.36), fill=SOFT)
_text(tb(s, ML + Inches(0.26), y + Inches(0.20), left - Inches(0.5), Inches(0.3)),
      [("贪心的净收益（每一步现算）", {"size": 13, "color": NAVY, "bold": True})])
_text(tb(s, ML + Inches(0.26), y + Inches(0.62), left - Inches(0.5), Inches(1.5)),
      [("u = 0.45·满意度 + 0.20·(停留/可用时长)", {"size": 13, "color": INK}),
       ("      − 0.25·(花费/剩余预算) − 0.20·((在途+干等)/可用时长)",
        {"size": 13, "color": INK})], line=1.7)
_text(tb(s, ML + Inches(0.26), y + Inches(1.70), left - Inches(0.5), Inches(0.5)),
      [("干等也扣分：06:30 出门却排一个 07:30 才开门的点，等于白等一小时。",
        {"size": 11, "color": MUTED})], line=1.4)

bullets(s, ML + left + Inches(0.34), y + Inches(0.04), CW - left - Inches(0.34), [
    ("只在可行解里挑", "超预算、装不进时间窗、会在关门前走不完的候选，根本不进比较"),
    ("目标与评分同源", "不做「算法觉得更好、分数却更低」的两套口径"),
    ("2-opt 只改顺序", "硬顺序前缀（起点 / 途经点 / 必去点）不翻转"),
    ("预算极紧就降级", "放开预算重排 + 如实报 BUDGET_OVER，宁可给不完美结果也不给空行程"),
], size=13)

# 对比条形（横向）
by = y + Inches(2.66)
_text(tb(s, ML, by, Inches(3.0), Inches(0.3)),
      [("6 用例平均总分（--scenario daily）", {"size": 12, "color": NAVY, "bold": True})])
bars = [("two_opt", 0.5835, True), ("greedy", 0.5819, False),
        ("stub", 0.5007, False), ("random", 0.4954, False)]
barmax = Inches(7.4)
by2 = by + Inches(0.42)
for name, val, best in bars:
    _text(tb(s, ML, by2 + Inches(0.02), Inches(1.3), Inches(0.28)),
          [(name,)], size=11, color=NAVY if best else MUTED, bold=best)
    wid = Emu(int(barmax * (val / 0.5835)))
    rect(s, ML + Inches(1.40), by2 + Inches(0.03), barmax, Inches(0.22), fill=SOFT)
    rect(s, ML + Inches(1.40), by2 + Inches(0.03), wid, Inches(0.22),
         fill=ACCENT if best else NAVY_L)
    _text(tb(s, ML + Inches(1.40) + barmax + Inches(0.12), by2 + Inches(0.01),
             Inches(0.9), Inches(0.26)),
          [("%.4f" % val,)], size=11, color=INK, bold=best)
    by2 = by2 + Inches(0.40)

metric(s, ML + Inches(10.0), y + Inches(2.66), Inches(2.4), "+17.8%",
       "真算法 vs 随机基线", "two_opt 0.5835 / random 0.4954")

# ================================================================ 06 界面
s, y = page("四个页签：行程表 / 时间轴 / 地图 / 算法对比", "05 界面",
            "全部离线渲染，断网也能演示；截图来自真实接口返回")
if SHOT.exists():
    pic_h = Inches(4.55)
    pic = s.shapes.add_picture(str(SHOT), ML, y, height=pic_h)
    px = ML + pic.width + Inches(0.3)
    pw = W - MR - px
    if pic.width > Inches(4.3):
        pic.width = Inches(4.3)
        pic.height = int(Inches(4.3) * pic_h / Inches(4.3))
        pw = W - MR - px
    bullets(s, px, y + Inches(0.10), pw, [
        ("行程表", "每站：到达–离开、停留、花费、交通方式、一句理由"),
        ("时间轴", "蓝=停留 灰=在途 橙=等开门；横轴是真实整点，空白就是空闲"),
        ("地图", "等距投影 + 按纬度压缩经度；多天分色，站名自动避让"),
        ("算法对比", "结论句 + 最优行 ★ + 以本次最高分为满格的条形图"),
    ], size=12)
else:
    bullets(s, ML, y, CW, [("（截图缺失：先跑 node _render_check.js 生成 _preview/preview.html）", "")])

# ================================================================ 07 两日 + 闭馆
s, y = page("两个必演示的场景：两日行程、闭馆 / 晚到", "06 对照",
            "任务书明确要求：必须做两日行程与闭馆晚到的对照")
rect(s, ML, y, Inches(5.7), Inches(4.3), fill=SOFT)
_text(tb(s, ML + Inches(0.28), y + Inches(0.22), Inches(5.2), Inches(0.3)),
      [("两日行程", {"size": 15, "color": NAVY, "bold": True})])
bullets(s, ML + Inches(0.28), y + Inches(0.66), Inches(5.1), [
    ("切段排程", "每天独立铺时间轴，不被跨天重复选中"),
    ("地图分色", "第 1 / 第 2 天两种颜色，圆点序号是当天序号"),
    ("预算折日均", "两天花掉 2 倍日均预算不再被判超支"),
], size=12)

rect(s, ML + Inches(6.0), y, CW - Inches(6.0), Inches(4.3), fill=WHITE, line_color=LINE)
_text(tb(s, ML + Inches(6.28), y + Inches(0.22), Inches(5.0), Inches(0.3)),
      [("闭馆 / 晚到：从「排完报错」到「排前跳过」", {"size": 15, "color": NAVY, "bold": True})])
rows = [
    ("以前", "16:50 出发 → 把 17:00 关门的校医院排进去 → 再报 CLOSED（自相矛盾）", MUTED),
    ("现在", "离开时会过关门时刻 → 直接跳过这一站，换下一家", INK),
    ("闭馆日", "候选池按 date 的星期排除（周一闭馆的博物馆不进池）", INK),
    ("早到", "等开门写成橙色等待段，不再伪装成「在路上走了很久」", INK),
]
ry = y + Inches(0.70)
for tag, txt, color in rows:
    _text(tb(s, ML + Inches(6.28), ry, Inches(1.1), Inches(0.3)),
          [(tag,)], size=11, color=ACCENT if tag == "以前" else NAVY, bold=True)
    _text(tb(s, ML + Inches(7.30), ry, CW - Inches(7.9), Inches(0.62)),
          [(txt,)], size=11, color=color, line=1.45)
    ry = ry + Inches(0.78)
_text(tb(s, ML + Inches(6.28), ry + Inches(0.02), CW - Inches(6.9), Inches(0.5)),
      [("命令：16:50 出发 + 关心「看病/邮政」→ 结果里没有校医院，也没有 CLOSED。",
        {"size": 10, "color": MUTED})], line=1.4)

# ================================================================ 08 可解释
s, y = page("可解释不是一句漂亮话，是三处「如实说」", "07 诚实性",
            "哪些数据没核实、哪些时间是估算、哪些约束没满足，都由应用自己说出来")
cards = [
    ("待核实清单", "meta.pending", "数据里没标来源适用日期的站点逐条列出，界面上写「待核实 N 项」"),
    ("数据覆盖度", "meta.data.matrix_edges", "有实测路网就报条数；没有就直说「在途全部按直线估算」"),
    ("约束未满足", "warnings", "BUDGET_OVER / MUST_VISIT_MISS / NO_CANDIDATE… 写具体数值，不写「综合评分低」"),
]
cw2 = Inches(3.86)
for i, (t, key, d) in enumerate(cards):
    x = ML + (cw2 + Inches(0.24)) * i
    rect(s, x, y, cw2, Inches(2.34), fill=SOFT)
    _text(tb(s, x + Inches(0.26), y + Inches(0.24), cw2 - Inches(0.5), Inches(0.3)),
          [(t,)], size=14, color=NAVY, bold=True)
    _text(tb(s, x + Inches(0.26), y + Inches(0.64), cw2 - Inches(0.5), Inches(0.28)),
          [(key,)], size=10, color=ACCENT, bold=True)
    _text(tb(s, x + Inches(0.26), y + Inches(1.02), cw2 - Inches(0.5), Inches(1.1)),
          [(d,)], size=11, color=INK, line=1.55)

rect(s, ML, y + Inches(2.62), CW, Inches(1.72), fill=WHITE, line_color=LINE)
_text(tb(s, ML + Inches(0.26), y + Inches(2.80), Inches(4.0), Inches(0.3)),
      [("降级链：任何一环坏掉都不许崩", {"size": 13, "color": NAVY, "bold": True})])
degs = ["城市未注册 → 回退默认数据", "无矩阵 → 全估算并报 0 条", "CSV 编码错 → 换编码重试",
        "实时接口失败 → 不重排不报错", "模型不可用 → 规则模板解说", "候选被过滤光 → 放宽到全部点"]
for i, d in enumerate(degs):
    x = ML + Inches(0.26) + Inches(4.2) * (i % 2)
    yy = y + Inches(3.22) + Inches(0.44) * (i // 2)
    _text(tb(s, x, yy, Inches(4.0), Inches(0.3)), [("· " + d,)], size=11, color=INK)

# ================================================================ 09 评测与验证
s, y = page("验证：210 个测试 + 可复现的对照实验 + 64 条验收项", "08 验证",
            "结论必须能被别人一键复现，否则只是「我觉得挺好」")
metric(s, ML, y, Inches(2.86), "210", "个单元/集成测试", "标准库 unittest，零依赖")
metric(s, ML + Inches(3.10), y, Inches(2.86), "64", "条验收项", "每条含动作 + 期望 + 修法")
metric(s, ML + Inches(6.20), y, Inches(2.86), "13", "项前端自动断言", "DOM 桩跑网页脚本，不用开浏览器")
metric(s, ML + Inches(9.30), y, Inches(3.13), "0", "字节差异", "同一输入两次评测逐字节一致")

_text(tb(s, ML, y + Inches(1.76), CW, Inches(0.3)),
      [("三条命令复现全部结论", {"size": 13, "color": NAVY, "bold": True})])
cmds = [
    (".venv\\Scripts\\python.exe -m unittest discover -s tests -t .", "210 个测试全绿"),
    (".venv\\Scripts\\python.exe -m eval.baseline --scenario daily", "6 用例 × 4 策略对照表"),
    ("node _render_check.js", "前端 13 项断言 + 静态预览页"),
]
cy = y + Inches(2.18)
for cmd, note in cmds:
    rect(s, ML, cy, CW - Inches(3.4), Inches(0.52), fill=RGBColor(0x1E, 0x29, 0x37))
    _text(tb(s, ML + Inches(0.18), cy + Inches(0.14), CW - Inches(3.8), Inches(0.3)),
          [(cmd, {"font": "Consolas", "size": 11, "color": RGBColor(0xE2, 0xE8, 0xF0)})])
    _text(tb(s, W - MR - Inches(3.1), cy + Inches(0.14), Inches(3.1), Inches(0.3)),
          [(note, {"size": 11, "color": MUTED})])
    cy = cy + Inches(0.66)

# ================================================================ 10 局限
s, y = page("局限：我们没做到的部分，说清楚", "09 局限",
            "这一页是加分项：知道自己错在哪，比声称「全都能做」更可信")
items = [
    ("在途时间只有 14% 是实测的", "145 条步行真实耗时；公交 / 驾车全部直线估算，跨校区行程误差偏大"),
    ("two_opt 相对 greedy 增益很小", "0.5835 vs 0.5819，6 个用例里 4 个同分 —— 数据规模太小，局部搜索没什么可优化"),
    ("关门只做「跳过」不做「改天」", "跨天仍是每天独立排，不把整趟当一个整体优化"),
    ("第二城市只有 12 个点、0 条路网", "北京只用来证明「换城市不改代码」，不用于真实出行"),
    ("地图是离线等距投影", "只表示相对位置与先后顺序，不能当导航用"),
]
bullets(s, ML, y, CW, items, size=13)
rect(s, ML, Inches(5.92), CW, Inches(0.70), fill=ACCENT_SOFT)
_text(tb(s, ML + Inches(0.24), Inches(6.06), CW - Inches(0.5), Inches(0.44)),
      [("如果再给一周：接公交/骑行真实耗时 → 整趟统一优化（把多天当一个大问题）→ 真实瓦片地图。",
        {"size": 12, "color": ACCENT, "bold": True})])

# ================================================================ 11 分工
s, y = page("两个人怎么并行而不互相踩", "10 协作",
            "靠的不是默契，是三件机制：契约冻结、验收清单、结论落盘")
cols = [
    ("甲 · 引擎与数据", ["engine/strategies.py 贪心与 2-opt", "constraints.py 约束与评分",
                    "数据包与 145 条实测耗时", "eval 对照实验"], NAVY),
    ("乙 · 交互与呈现", ["web/index.html 四个页签", "Streamlit 界面", "service/dto.py 形状转换",
                    "报告 / PPT / 答辩"], NAVY_L),
]
for i, (t, lst, color) in enumerate(cols):
    x = ML + Inches(4.2) * i
    rect(s, x, y, Inches(3.9), Inches(3.06), fill=SOFT)
    _text(tb(s, x + Inches(0.26), y + Inches(0.24), Inches(3.4), Inches(0.3)),
          [(t,)], size=14, color=color, bold=True)
    yy = y + Inches(0.72)
    for it in lst:
        _text(tb(s, x + Inches(0.26), yy, Inches(3.4), Inches(0.3)),
              [("· " + it,)], size=11, color=INK)
        yy = yy + Inches(0.46)

mech = [
    ("契约冻结", "字段只增不改，两边同时改不打架；改契约要三方同步"),
    ("验收清单当合同", "64 条 AC：跑什么、期望什么、不符合怎么修"),
    ("结论落盘", "每个决定写进 docs/，避免「换个对话就改回去」（真的发生过 3 次）"),
]
x2 = ML + Inches(8.7)
yy = y
for t, d in mech:
    rect(s, x2, yy, CW - Inches(8.7), Inches(0.92), fill=WHITE, line_color=LINE)
    _text(tb(s, x2 + Inches(0.22), yy + Inches(0.12), CW - Inches(9.2), Inches(0.28)),
          [(t,)], size=12, color=ACCENT, bold=True)
    _text(tb(s, x2 + Inches(0.22), yy + Inches(0.42), CW - Inches(9.2), Inches(0.44)),
          [(d,)], size=10, color=MUTED, line=1.35)
    yy = yy + Inches(1.06)

# ================================================================ 12 收尾
s = prs.slides.add_slide(prs.slide_layouts[6])
rect(s, 0, 0, W, H, fill=NAVY)
_text(tb(s, ML, Inches(2.02), CW, Inches(0.4)),
      [("一句话总结",)], size=13, color=RGBColor(0xA9, 0xC3, 0xDC), bold=True)
_text(tb(s, ML, Inches(2.52), CW, Inches(1.8)),
      [("一句自然语言，换一条守住硬约束、", {"size": 30, "color": WHITE, "bold": True}),
       ("逐站有理由、随时能改的行程 ——", {"size": 30, "color": WHITE, "bold": True}),
       ("并且在数据不全、没网、没模型时依然可用。", {"size": 30, "color": WHITE, "bold": True})],
      line=1.5)
rect(s, ML, Inches(4.56), Inches(1.6), Emu(30000), fill=RGBColor(0xC2, 0x41, 0x0C))
_text(tb(s, ML, Inches(4.82), CW, Inches(1.2)),
      [("技术上最值钱的不是算法多复杂，而是把「可解释、可降级、可验收」做成了结构性约束：",
        {"size": 13, "color": RGBColor(0xD6, 0xE3, 0xF0)}),
       ("契约冻结 · 数值外置 · 验收清单 · 降级链 · 诚实的待核实清单。",
        {"size": 13, "color": RGBColor(0xD6, 0xE3, 0xF0), "bold": True})], line=1.7)
_text(tb(s, ML, Inches(6.30), CW, Inches(0.4)),
      [("谢谢，欢迎提问。", {"size": 18, "color": WHITE, "bold": True})])

OUT.parent.mkdir(parents=True, exist_ok=True)
prs.save(str(OUT))
print("已生成：%s（%d 页）" % (OUT, len(prs.slides._sldIdLst)))
