/* 前端渲染自检（不开浏览器）：用 DOM 桩执行 web/index.html 的 <script>，
 * 再把**真实接口**返回的 JSON 喂进去，断言下拉默认值 / 时间轴等待段 / 待核实清单 /
 * 地图分色与站名避让都画对了，最后落一份静态预览页供人眼核对。
 *
 * 跑法（项目根目录，一条命令即可，夹具会自己生成到 _preview/）：
 *     node _render_check.js
 * 人眼核对（可选）：
 *     msedge --headless=new --screenshot=out.png --window-size=1280,2600 _preview/preview.html
 *
 * 它**不进 unittest、也不进前端运行路径** —— 只是"不开浏览器也能判断画对没画对"。
 */
const fs = require('fs');
const vm = require('vm');
const path = require('path');
const { execFileSync } = require('child_process');

const ROOT = __dirname;
const PREVIEW_DIR = path.join(ROOT, '_preview');
const PY = path.join(ROOT, '.venv', 'Scripts', 'python.exe');

/* ---------------- 夹具：三份真实行程 JSON ---------------- */
const GEN_PY = String.raw`
import json, os
from api.routes import dispatch

out = os.path.join("_preview")
os.makedirs(out, exist_ok=True)

def save(name, body):
    st, p = dispatch("POST", "/api/plan", {}, body)
    assert p.get("ok"), (st, p)
    with open(os.path.join(out, name), "w", encoding="utf-8") as f:
        json.dump(p, f, ensure_ascii=False)

base = {"query": "上午去图书馆自习，中午食堂吃饭，顺便取快递", "scenario": "daily",
        "strategy": "stub", "realtime": False, "narrate": True}
save("plan_default.json", base)
save("plan_early.json", dict(base, overrides={"start_time": "06:20", "top_n": 3}))
save("plan_2day.json", dict(base, query="两天：第一天自习加吃饭，第二天取快递和超市",
                            overrides={"days": 2, "top_n": 3}))
print("fixtures ok")
`;

function ensureFixtures() {
  fs.mkdirSync(PREVIEW_DIR, { recursive: true });
  const need = ['plan_default.json', 'plan_early.json', 'plan_2day.json'];
  if (need.every(n => fs.existsSync(path.join(PREVIEW_DIR, n)))) return;
  if (!fs.existsSync(PY)) {
    console.log('找不到 .venv 里的 python：' + PY);
    process.exit(2);
  }
  execFileSync(PY, ['-c', GEN_PY], {
    cwd: ROOT,
    env: { ...process.env, PYTHONPATH: ROOT },
    stdio: 'inherit',
  });
}

ensureFixtures();
const read = n => JSON.parse(fs.readFileSync(path.join(PREVIEW_DIR, n), 'utf8'));
const PLAN_RESP = read('plan_default.json');
const EARLY_RESP = read('plan_early.json');
const TWO_DAY_RESP = read('plan_2day.json');

const html = fs.readFileSync(path.join(ROOT, 'web', 'index.html'), 'utf8');
const code = html.match(/<script>([\s\S]*?)<\/script>/)[1];

/* ---------------- DOM 桩 ---------------- */
const nodes = {};
function node(id) {
  if (!nodes[id]) {
    nodes[id] = {
      id, innerHTML: '', textContent: '', value: '', checked: true,
      hidden: false, disabled: false, classList: { toggle(){}, add(){}, remove(){} },
      children: [],
      addEventListener(){}, appendChild(c){ this.children.push(c); }, removeChild(){},
      querySelectorAll: () => [],
    };
  }
  return nodes[id];
}
global.document = {
  getElementById: node,
  querySelectorAll: () => [],
  createElement: () => ({ value: '', textContent: '', selected: false, appendChild(){} }),
};
global.window = {};

let CURRENT = PLAN_RESP;
global.fetch = async (p) => {
  let body;
  if (p === '/api/health') body = { ok: true, cities: 2, amap: { configured: true, cached_points: 145 } };
  else if (p === '/api/options') body = {
    ok: true, cities: [{ key: 'dalian', name: '大连' }], scenarios: ['daily', 'demo'],
    strategies: ['greedy', 'random', 'stub', 'two_opt'],
    default_city: 'dalian', default_scenario: 'daily', default_strategy: 'stub',
    scenario_strategies: { daily: 'stub', demo: 'stub' },
  };
  else body = CURRENT;
  return { ok: true, status: 200, json: async () => body };
};

vm.runInThisContext(code, { filename: 'index.html<script>' });

/* ---------------- 断言 ---------------- */
let failed = 0;
function check(name, cond, extra) {
  console.log((cond ? '  ok   ' : '  FAIL ') + name + (cond || !extra ? '' : '  → ' + extra));
  if (!cond) failed++;
}

(async () => {
  await new Promise(r => setTimeout(r, 20));      // 等 boot() 把 /api/options 填进下拉框
  const selS = node('strategy').children.filter(o => o.selected).map(o => o.value);
  const selC = node('scenario').children.filter(o => o.selected).map(o => o.value);
  console.log('[0] 下拉框默认值（AC-44）');
  check('算法默认 = 场景配置的 stub（不是字母序第一个 greedy）',
        selS.length === 1 && selS[0] === 'stub',
        JSON.stringify(node('strategy').children.map(o => o.value)) + ' 选中=' + JSON.stringify(selS));
  check('场景默认 = daily', selC[0] === 'daily', JSON.stringify(selC));

  await plan();
  const sum = node('summary').innerHTML;
  const tl = node('pane-timeline').innerHTML;
  const list = node('pane-list').innerHTML;
  const map = node('pane-map').innerHTML;

  console.log('[1] 默认行程（daily + stub）');
  check('行程表有站点', list.includes('<table>'), list.slice(0, 80));
  check('渲染出待核实清单', sum.includes('待核实'), 'meta.pending 没被渲染');
  check('待核实条数 > 0', /待核实（\d+ 项）/.test(sum));
  check('时间轴图例含「等开门」', tl.includes('等开门'));
  check('地图有多天图例', map.includes('第 1 天'));

  /* 换成"06:20 出发"的行程：第一站会等开门，这里必须画出等待段 */
  CURRENT = EARLY_RESP;
  await plan();
  const tl2 = node('pane-timeline').innerHTML;
  const waitRects = (tl2.match(/fill="#f59e0b"/g) || []).length;
  console.log('[2] 早出发（06:20）');
  check('画出等待段（橙色块 ≥ 1）', waitRects >= 1, '未画出等待段：' + waitRects);
  check('等待段带分钟数', /等开门 \d+ 分钟/.test(tl2));
  check('没有 NaN / undefined 泄漏', !/NaN|undefined/.test(tl2 + node('summary').innerHTML));

  /* 两日行程：地图要分色 + 每天一条虚线，时间轴要两条 */
  CURRENT = TWO_DAY_RESP;
  await plan();
  const map2 = node('pane-map').innerHTML;
  const tl3 = node('pane-timeline').innerHTML;
  console.log('[3] 两日行程');
  check('地图图例有第 1 / 第 2 天', map2.includes('第 1 天') && map2.includes('第 2 天'));
  check('地图两天不同色', (map2.match(/stroke="#(2563eb|0d9488)"/g) || []).length >= 2);
  check('时间轴两天两块', (tl3.match(/class="tl-day"/g) || []).length === 2);

  /* 站名避让：把 SVG 里实际写出的标签位置取回来，两两比一遍框
   * （校园里食堂/教学楼只隔几十米，只试"上/下"两个位置会叠成一团） */
  const labs = [...map2.matchAll(/<text x="([-\d.]+)" y="([-\d.]+)" fill="#1f2328" font-size="11"[^>]*>([^<]*)<\/text>/g)]
    .map(m => ({ x: +m[1], y: +m[2], half: Math.max(24, m[3].length * 6), name: m[3] }));
  let dup = 0;
  for (let i = 0; i < labs.length; i++) {
    for (let j = i + 1; j < labs.length; j++) {
      const a = labs[i], b = labs[j];
      if (Math.abs(a.x - b.x) < a.half + b.half && Math.abs(a.y - b.y) < 14) dup++;
    }
  }
  check('站名标签不重叠', labs.length > 0 && dup === 0,
        labs.length + ' 个标签，' + dup + ' 对重叠：' + labs.map(l => l.name).join('/'));

  console.log(failed ? ('\n' + failed + ' 项未通过') : '\n全部通过');

  /* 顺手落一份静态预览：把人眼要看的四块拼到一张页面上，用无头浏览器截图核对
   * （纯文本断言抓不到"画得丑"这类问题）。 */
  const style = html.match(/<style>([\s\S]*?)<\/style>/)[1];
  const preview = '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
    + '<title>预览</title><style>' + style + '</style></head><body>'
    + '<div class="wrap"><div></div><div>' + node('summary').innerHTML
    + node('pane-list').innerHTML + node('pane-timeline').innerHTML
    + node('pane-map').innerHTML + '</div></div></body></html>';
  const previewPath = path.join(PREVIEW_DIR, 'preview.html');
  fs.writeFileSync(previewPath, preview, 'utf8');
  console.log('预览已写出：' + path.relative(ROOT, previewPath));

  process.exit(failed ? 1 : 0);
})();
