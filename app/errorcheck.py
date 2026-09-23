"""已保存配平方案的称重误差校核(只读, 不改动方案本身)。

模型
----
方案中的摆放(固定落槽)与槽位角度直接取自已保存的求解结果, 校核不重新布局。
设第 k 支试管标称质量 m_k、所在槽角度 θ_k, 称重误差 δ_k 可在闭区间
[-ε_k, +ε_k] 内独立变化(ε_k ≥ 0 且 ε_k ≤ m_k, 保证实际质量非负):

    实际质量  m'_k = m_k + δ_k,
    一阶不平衡量(不计半径 R)
        U₁/R = Σ m'_k e^{iθ_k} = S₀ + Σ δ_k e^{iθ_k},
        S₀ = Σ m_k e^{iθ_k}  为标称合成向量。

误差集合 Z = {Σ δ_k e^{iθ_k} : |δ_k| ≤ ε_k} 是平面上的 zonotope(二维
中心对称多胞体); |R·(S₀ + e)| 是 e 的凸函数, 在凸多胞体上的最大值必在
顶点取得。因此**不需要枚举 2ⁿ 种符号组合**: 顶点处每个 δ_k 都取端点
±ε_k, 随最不利方向 φ 满足

    δ_k(φ) = ε_k · sign(cos(θ_k - φ)).

符号只在 φ = θ_k ± 90° 处翻转。将全部翻转角排序, 它们把圆周划分为至多
2n 个开区间; 在每个区间内符号恒定, 取区间中点计算该区域对应的顶点,
再在至多 2n 个顶点中取 |R(S₀ + P)| 最大者即为精确最不利值。

注意: 直接把 Σ R·ε_k(各管误差绝对值之和)当作最不利 |U₁| 是把向量模
按标量相加, 只在所有误差向量严格同向时才成立, 通常显著过保守。
模块同时给出该粗略上界(naive_scalar_bound)仅供对照。
"""
from __future__ import annotations

import math
from typing import Any, Optional

TWO_PI = 2.0 * math.pi


# ---------------------------------------------------------------------------
# 输入解析与校验
# ---------------------------------------------------------------------------
def _placed_tubes(plan: dict[str, Any]) -> list[dict[str, float]]:
    """从已保存方案提取 (tube_id, 标称质量, 槽位, 角度), 仅含实际落槽的试管。

    角度优先取结果摆放行中的 angle, 再回退到 slot_state/请求槽位定义。
    """
    result = plan.get("result", {})
    placements = result.get("placements") or []

    # 槽位角度兜底来源
    slot_angles: dict[int, float] = {}
    for ss in result.get("slot_state", []) or []:
        if ss.get("angle") is not None:
            slot_angles[int(ss["slot"])] = float(ss["angle"])
    req = plan.get("request", {})
    slot_count = int(req.get("slot_count", len(slot_angles) or 0))
    for s in req.get("slots", []) or []:
        idx = int(s["index"])
        ang = s.get("angle")
        slot_angles.setdefault(
            idx,
            float(ang) if ang is not None else 360.0 * idx / max(slot_count, 1),
        )

    out: list[dict[str, float]] = []
    for p in placements:
        slot = p.get("slot")
        if slot is None:
            continue
        slot = int(slot)
        angle = p.get("angle")
        if angle is None:
            angle = slot_angles.get(slot)
        if angle is None:
            raise ValueError(f"试管 {p.get('tube_id')} 的槽位 {slot} 缺少角度信息。")
        out.append({
            "tube_id": int(p["tube_id"]),
            "mass": float(p["mass"]),
            "slot": slot,
            "angle": float(angle),
        })
    return out


def validate_error_inputs(
    tubes: list[dict[str, float]],
    items: dict[int, float],
    limit_g_mm: Optional[float],
) -> None:
    if not tubes:
        raise ValueError("该方案没有已落槽的试管, 无法校核。")
    for t in tubes:
        if not (t["mass"] > 0):
            raise ValueError(f"试管 {t['tube_id']} 标称质量必须为正。")
    known = {int(t["tube_id"]) for t in tubes}
    unknown = sorted(set(items) - known)
    if unknown:
        raise ValueError(f"误差上界中包含方案里不存在或未落槽的试管: {unknown}。")
    for tid, eps in items.items():
        if eps < 0:
            raise ValueError(f"试管 {tid} 的质量误差上界不能为负。")
        mass = next(t["mass"] for t in tubes if t["tube_id"] == tid)
        if eps > mass + 1e-9:
            raise ValueError(
                f"试管 {tid} 的误差上界 {eps:g} g 超过标称质量 {mass:g} g。"
            )
    if limit_g_mm is not None and limit_g_mm < 0:
        raise ValueError("一阶不平衡量限值不能为负。")


# ---------------------------------------------------------------------------
# 核心: zonotope 顶点扫掠, 精确求最不利 |U₁|
# ---------------------------------------------------------------------------
def worst_case_u1(
    tubes: list[dict[str, float]],
    eps: dict[int, float],
) -> dict[str, Any]:
    """在 δ_k ∈ [-ε_k, +ε_k] 独立变化下求 max |Σ (m_k+δ_k) e^{iθ_k}|。

    返回:
      nom_u1           标称(质量加权, 不含 R)复数向量
      worst_u1         最不利实际复数向量(不含 R)
      worst_mag        最不利 |U₁|/R
      worst_angle_deg  最不利向量方向(度, [0,360))
      signs            {tube_id: +1/-1}, δ_k = sign·ε_k
      naive_bound      Σ ε_k, 直接绝对值相加的粗略(过保守)上界
    """
    # 标称向量
    s0x = s0y = 0.0
    for t in tubes:
        a = math.radians(t["angle"])
        s0x += t["mass"] * math.cos(a)
        s0y += t["mass"] * math.sin(a)

    active = [t for t in tubes if eps.get(t["tube_id"], 0.0) > 0.0]

    # 无正误差上界: 结果唯一(标称值)
    if not active:
        mag = math.hypot(s0x, s0y)
        return {
            "nom_u1": complex(s0x, s0y),
            "worst_u1": complex(s0x, s0y),
            "worst_mag": mag,
            "worst_angle_deg": (math.degrees(math.atan2(s0y, s0x)) % 360.0),
            "signs": {int(t["tube_id"]): 1 for t in tubes},
            "naive_bound": 0.0,
        }

    # 翻转角(弧度): 对每个生成元, cos(θ-φ)=0 的两个方向
    cuts = {0.0, TWO_PI}
    for t in active:
        theta = math.radians(t["angle"])
        for b in (theta + math.pi / 2.0, theta - math.pi / 2.0):
            cuts.add(b % TWO_PI)
    cuts = sorted(cuts)

    def vertex_at_phi(phi: float) -> tuple[complex, dict[int, int]]:
        """给定最不利方向 φ, 构造使投影最大的 zonotope 顶点。"""
        px = s0x
        py = s0y
        signs: dict[int, int] = {}
        for t in tubes:
            tid = int(t["tube_id"])
            e = eps.get(tid, 0.0)
            if e <= 0.0:
                signs[tid] = 1
                continue
            a = math.radians(t["angle"])
            c = math.cos(a - phi)
            sgn = 1 if c >= 0.0 else -1
            signs[tid] = sgn
            d = sgn * e
            px += d * math.cos(a)
            py += d * math.sin(a)
        return complex(px, py), signs

    # 相邻翻转角之间符号恒定; 区间中点代表该区域对应的顶点
    best: Optional[tuple[float, complex, dict[int, int]]] = None
    for lo, hi in zip(cuts, cuts[1:]):
        mid = (lo + hi) / 2.0
        v, signs = vertex_at_phi(mid)
        mag = abs(v)
        if best is None or mag > best[0] + 1e-12:
            best = (mag, v, signs)

    assert best is not None
    worst_mag, worst_vec, signs = best
    return {
        "nom_u1": complex(s0x, s0y),
        "worst_u1": worst_vec,
        "worst_mag": worst_mag,
        "worst_angle_deg": (
            math.degrees(math.atan2(worst_vec.imag, worst_vec.real)) % 360.0
        ),
        "signs": signs,
        "naive_bound": sum(eps.get(t["tube_id"], 0.0) for t in tubes),
    }


# ---------------------------------------------------------------------------
# 校核入口
# ---------------------------------------------------------------------------
def check_plan(
    plan: dict[str, Any],
    error_bounds: list[dict[str, Any]],
    limit_g_mm: Optional[float] = None,
) -> dict[str, Any]:
    """对已保存方案执行称重误差校核(只读)。

    参数
    ----
    plan: GET /api/plan/{id} 返回的完整方案(含 request/result)。
    error_bounds: [{"tube_id": int, "error_bound_g": 非负, ≤标称质量}]
                  缺省的试管误差上界按 0 处理。
    limit_g_mm: 一阶不平衡量限值 (g·mm), 可为 None(仅给结果不判定)。
    """
    result = plan.get("result", {})
    radius_mm = float(result.get("config", {}).get("radius_mm")
                      or plan.get("request", {}).get("radius_mm", 100.0))
    rpm = float(result.get("config", {}).get("rpm")
                or plan.get("request", {}).get("rpm", 0.0))

    tubes = _placed_tubes(plan)
    items: dict[int, float] = {}
    for it in error_bounds or []:
        tid = int(it["tube_id"])
        bound = it.get("error_bound_g", 0.0)
        items[tid] = 0.0 if bound is None else float(bound)
    validate_error_inputs(tubes, items, limit_g_mm)

    wc = worst_case_u1(tubes, items)
    R = radius_mm
    nom_u1, worst_u1 = R * wc["nom_u1"], R * wc["worst_u1"]
    nom_mag, worst_mag = abs(nom_u1), abs(worst_u1)
    # 粗略(过保守)做法: 标称向量模 + 各管误差绝对值标量相加, 把误差当全同向
    error_scalar_sum = R * wc["naive_bound"]
    naive_scalar_bound = nom_mag + error_scalar_sum

    # 实际质量与逐管明细
    actual_masses: dict[int, float] = {}
    rows: list[dict[str, Any]] = []
    for t in tubes:
        tid = int(t["tube_id"])
        e = items.get(tid, 0.0)
        sgn = wc["signs"].get(tid, 1)
        actual = t["mass"] + sgn * e
        actual_masses[tid] = actual
        rows.append({
            "tube_id": tid,
            "slot": t["slot"],
            "angle_deg": t["angle"],
            "nominal_mass_g": t["mass"],
            "error_bound_g": e,
            "error_sign": sgn,
            "actual_mass_g": actual,
            "mass_delta_g": sgn * e,
        })
    rows.sort(key=lambda r: (r["slot"], r["tube_id"]))

    omega = TWO_PI * rpm / 60.0

    def force(mag_g_mm: float) -> float:
        return omega ** 2 * mag_g_mm * 1e-6

    out: dict[str, Any] = {
        "plan_id": plan.get("id"),
        "plan_name": plan.get("name"),
        "radius_mm": R,
        "rpm": rpm,
        "limit_g_mm": limit_g_mm,
        "nominal": {
            "u1_mag_g_mm": nom_mag,
            "u1_angle_deg": math.degrees(math.atan2(nom_u1.imag, nom_u1.real)) % 360.0,
            "u1_x_g_mm": nom_u1.real,
            "u1_y_g_mm": nom_u1.imag,
            "centrifugal_force_n": force(nom_mag),
        },
        "worst_case": {
            "u1_mag_g_mm": worst_mag,
            "u1_angle_deg": wc["worst_angle_deg"],
            "u1_x_g_mm": worst_u1.real,
            "u1_y_g_mm": worst_u1.imag,
            "centrifugal_force_n": force(worst_mag),
            "actual_masses_g": actual_masses,
        },
        "naive_scalar_bound_g_mm": naive_scalar_bound,
        "error_scalar_sum_g_mm": error_scalar_sum,
        "naive_scalar_force_n": force(naive_scalar_bound),
        "magnification": (worst_mag / nom_mag) if nom_mag > 1e-12 else None,
        "increment_g_mm": worst_mag - nom_mag,
        "tubes": rows,
        "method": (
            "各管误差在 ±误差上界内独立变化; 误差集合为二维 zonotope, "
            "凸性保证最不利 |U₁| 在顶点取得, 按方向扫掠全部 2n 个符号翻转点"
            "精确求解, 未枚举 2^n 组合; naive_scalar_bound = 标称|U₁| + ΣR·ε_k, "
            "即把各管误差绝对值直接标量相加(视全部误差同向)的过保守粗略上界, "
            "仅作对照; error_scalar_sum 为其中的 ΣR·ε_k 部分。"
        ),
    }
    if limit_g_mm is not None:
        out["verdict"] = {
            "limit_g_mm": limit_g_mm,
            "nominal_pass": nom_mag <= limit_g_mm + 1e-9,
            "worst_case_pass": worst_mag <= limit_g_mm + 1e-9,
            "margin_g_mm": limit_g_mm - worst_mag,
        }
    return out


def error_form_context(plan: dict[str, Any]) -> dict[str, Any]:
    """校核表单的只读上下文: 固定落槽/角度、标称质量与当前一阶指标。"""
    tubes = _placed_tubes(plan)
    m = plan.get("result", {}).get("metrics", {})
    cfg = plan.get("result", {}).get("config", {})
    return {
        "plan_id": plan.get("id"),
        "plan_name": plan.get("name"),
        "radius_mm": float(cfg.get("radius_mm")
                           or plan.get("request", {}).get("radius_mm", 100.0)),
        "rpm": float(cfg.get("rpm") or plan.get("request", {}).get("rpm", 0.0)),
        "nominal_u1_mag_g_mm": m.get("u1_mag_g_mm"),
        "nominal_grade": m.get("grade"),
        "tubes": sorted(
            (
                {
                    "tube_id": int(t["tube_id"]),
                    "slot": int(t["slot"]),
                    "angle_deg": float(t["angle"]),
                    "nominal_mass_g": float(t["mass"]),
                }
                for t in tubes
            ),
            key=lambda r: (r["slot"], r["tube_id"]),
        ),
    }
