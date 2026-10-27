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
from engine.models import PlanRequest, PlanResponse


def plan(req: PlanRequest,
         scenario: str | None = None,
         strategy: str | None = None) -> PlanResponse:
    """生成一个行程方案。

    :param req:       请求（PlanRequest，字段全有默认值，空请求也能跑）
    :param scenario:  场景名，None 表示用环境变量或默认场景
    :param strategy:  策略名，None 表示用场景配置里的 default_strategy
    :return:          PlanResponse，**永不返回 None、永不抛业务异常**
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
    return resp


def clear_caches() -> None:
    """清空数据层缓存（改了 CSV 之后调用）。

    放在这里是为了保持分层单向：UI 只允许 import engine，不允许直接 import data。
    """
    from data.loader import clear_cache

    clear_cache()
