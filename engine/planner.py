# @author: 甲
# @reviewer: 乙
# @since: 2026-10-27
"""引擎门面：全项目唯一的规划入口。

调用链：请求 -> 选策略 -> 出行程 -> 约束校验 -> 评分 -> 补 warnings

UI / main.py / eval 都只调这里的 plan()，谁都不许直接 import strategies。
"""

from __future__ import annotations

from datetime import datetime

from engine import constraints, strategies
from engine.config import ScenarioConfig, load_scenario
from engine.context import PlanContext
from engine.models import PlanRequest, PlanResponse


def plan(req: PlanRequest,
         scenario: str | None = None,
         strategy: str | None = None,
         ctx: "PlanContext | None" = None) -> PlanResponse:
    """生成一个行程方案。

    :param req:       请求（PlanRequest，字段全有默认值，空请求也能跑）
    :param scenario:  场景名，None 表示用环境变量或默认场景
    :param strategy:  策略名，None 表示用场景配置里的 default_strategy
    :param ctx:       实时上下文（PlanContext）。**None = 完全离线行为**，
                      传了才会把天气/闭馆信息写进结果，并在必要时重排一次。
    :return:          PlanResponse，**永不返回 None、永不抛业务异常**

    ⚠️ 引擎内部**零 IO**：实时数据必须先由 `engine/realtime.py` 抓成快照，
    再通过 ctx 注入。只有这样才能保证"同样的输入 → 同样的输出"，
    评测能比、出问题能回放。
    """
    if req is None:
        req = PlanRequest()

    cfg: ScenarioConfig = load_scenario(scenario)
    name = strategy or cfg.default_strategy
    fn = strategies.get(name)                    # 换算法只影响这一行

    resp = fn(req, cfg)
    resp.strategy = name
    resp.scenario = cfg.name
    resp.generated_at = datetime.now().isoformat(timespec="seconds")

    # 约束检查：只往 warnings 里写，绝不抛异常
    for v in constraints.validate_request(req, cfg):
        resp.warnings.append(str(v))
    for d in resp.days:
        for v in constraints.validate_day(d, cfg, req):
            resp.warnings.append(str(v))

    resp.metrics = constraints.score(resp, cfg)

    # 权重档位：profile 优先 → 场景 [weights] → 都没有就交给 PlanMetrics.overall 的内置默认值。
    # 写进 meta 是为了让 UI / 导出 / 评测用同一套权重算总分，不各算各的。
    weights = None
    if req.profile and cfg.weight_presets.get(req.profile):
        weights = cfg.weight_presets[req.profile]
    elif cfg.weights:
        weights = cfg.weights
    if weights:
        resp.meta["weights"] = dict(weights)

    # 实时数据：只在这里统一生效一次，策略内部一律不碰它
    if ctx is not None and getattr(ctx, "snapshot", None) is not None:
        try:
            from engine.replan import apply_snapshot

            resp = apply_snapshot(resp, ctx.snapshot, ctx,
                                  scenario=cfg.name, strategy=name)
        except Exception as e:                  # noqa: BLE001 - 重排失败不能连累主结果
            resp.warnings.append("[实时] 处理失败，已保留原方案：%s" % e)

    return resp


def clear_caches() -> None:
    """清空数据层缓存（改了 CSV 之后调用）。

    放在这里是为了保持分层单向：UI 只允许 import engine，不允许直接 import data。
    """
    from data.loader import clear_cache

    clear_cache()
