"""称重误差校核测试: python -m unittest discover -s tests

验证最不利 |U₁| 的精确 zonotope 顶点算法:
- 不枚举 2^n 组合, 结果与小规模暴力枚举一致;
- 严格小于(或等于)把各管误差绝对值直接相加的粗略上界;
- 实际质量非负、误差不越界;
- 限值判定与输入校验。
"""
import math
import unittest

from app.errorcheck import check_plan, worst_case_u1


def _plan(tubes_placements, radius=100.0, rpm=4000, pid=1):
    """tubes_placements: [(tid, mass, slot, angle), ...]"""
    slots = sorted({p[2] for p in tubes_placements})
    slot_state = [
        {"slot": s, "angle": next(a for (_, _, sl, a) in tubes_placements if sl == s),
         "enabled": True, "tube_id": next((t for (t, _, sl, _) in tubes_placements if sl == s), None),
         "locked": False}
        for s in slots
    ]
    max_slot = max(slots) + 1
    request_slots = []
    ang_by_slot = {sl: a for (_, _, sl, a) in tubes_placements}
    for i in range(max_slot):
        request_slots.append({"index": i, "angle": ang_by_slot.get(i, 360.0 * i / max_slot),
                              "enabled": True})
    placements = [
        {"tube_id": tid, "mass": m, "locked": False, "slot": sl, "angle": a,
         "previous_slot": None, "moved": False}
        for (tid, m, sl, a) in tubes_placements
    ]
    u1x = sum(m * math.cos(math.radians(a)) for (_, m, _, a) in tubes_placements)
    u1y = sum(m * math.sin(math.radians(a)) for (_, m, _, a) in tubes_placements)
    return {
        "id": pid, "name": "测试方案",
        "request": {"slots": request_slots, "radius_mm": radius, "rpm": rpm},
        "result": {
            "config": {"radius_mm": radius, "rpm": rpm},
            "placements": placements,
            "slot_state": slot_state,
            "metrics": {
                "u1_mag_g_mm": radius * math.hypot(u1x, u1y),
                "u1_x_g_mm": radius * u1x, "u1_y_g_mm": radius * u1y,
                "grade": "A",
            },
        },
    }


def _brute_force_worst(tubes, eps):
    """小规模暴力枚举 2^n 符号组合, 返回 (最大模, 符号元组)。"""
    best = (-1.0, None)
    n = len(tubes)
    for mask in range(1 << n):
        x = y = 0.0
        signs = {}
        for i, t in enumerate(tubes):
            sgn = 1 if (mask >> i) & 1 else -1
            signs[t["tube_id"]] = sgn
            m = t["mass"] + sgn * eps.get(t["tube_id"], 0.0)
            a = math.radians(t["angle"])
            x += m * math.cos(a)
            y += m * math.sin(a)
        mag = math.hypot(x, y)
        if mag > best[0] + 1e-12:
            best = (mag, tuple(signs[t["tube_id"]] for t in tubes))
    return best


class TestWorstCaseU1(unittest.TestCase):
    def setUp(self):
        # 4 支, 非对称角度(避免简并掩盖问题)
        self.tubes = [
            {"tube_id": 1, "mass": 50.0, "slot": 0, "angle": 0.0},
            {"tube_id": 2, "mass": 50.0, "slot": 1, "angle": 92.0},
            {"tube_id": 3, "mass": 51.0, "slot": 2, "angle": 178.0},
            {"tube_id": 4, "mass": 49.0, "slot": 3, "angle": 271.0},
        ]
        self.eps = {1: 0.5, 2: 0.3, 3: 0.8, 4: 0.2}

    def test_matches_brute_force(self):
        wc = worst_case_u1(self.tubes, self.eps)
        bf_mag, bf_signs = _brute_force_worst(self.tubes, self.eps)
        self.assertAlmostEqual(wc["worst_mag"], bf_mag, places=9)
        got_signs = tuple(wc["signs"][t["tube_id"]] for t in self.tubes)
        self.assertEqual(got_signs, bf_signs)

    def test_random_geometries_vs_brute_force(self):
        import random
        rng = random.Random(42)
        for trial in range(40):
            n = rng.randint(1, 6)
            tubes = []
            eps = {}
            used_angles = set()
            for i in range(n):
                a = rng.uniform(0, 360)
                tubes.append({"tube_id": i + 1, "mass": rng.uniform(10, 100),
                              "slot": i, "angle": a})
                eps[i + 1] = rng.uniform(0, tubes[-1]["mass"])
                used_angles.add(a)
            wc = worst_case_u1(tubes, eps)
            bf_mag, _ = _brute_force_worst(tubes, eps)
            self.assertAlmostEqual(wc["worst_mag"], bf_mag, places=8,
                                   msg=f"trial {trial}")

    def test_zero_bounds_gives_nominal(self):
        wc = worst_case_u1(self.tubes, {})
        nom = abs(wc["nom_u1"])
        self.assertAlmostEqual(wc["worst_mag"], nom, places=12)
        self.assertEqual(wc["naive_bound"], 0.0)

    def test_single_tube_worst_is_nominal_plus_bound(self):
        tubes = [{"tube_id": 7, "mass": 40.0, "slot": 3, "angle": 55.0}]
        wc = worst_case_u1(tubes, {7: 1.0})
        # 单管无其他向量可对消, 最不利 = m + ε, 最有利 = m - ε
        self.assertAlmostEqual(wc["worst_mag"], 41.0, places=12)
        self.assertEqual(wc["signs"][7], 1)

    def test_actual_masses_nonnegative_and_within_bounds(self):
        out = check_plan(_plan([
            (1, 50.0, 0, 0.0), (2, 50.0, 6, 180.0),
            (3, 30.0, 2, 60.0),
        ]), [{"tube_id": 1, "error_bound_g": 1.0},
             {"tube_id": 2, "error_bound_g": 0.5},
             {"tube_id": 3, "error_bound_g": 2.0}])
        for row in out["tubes"]:
            self.assertGreaterEqual(row["actual_mass_g"], 0.0)
            self.assertLessEqual(abs(row["mass_delta_g"]),
                                 row["error_bound_g"] + 1e-9)
            # 回报向量与实际质量一致
        # 用回报的实际质量复核 |U₁|
        mass_by = {r["tube_id"]: r["actual_mass_g"] for r in out["tubes"]}
        ang_by = {r["tube_id"]: r["angle_deg"] for r in out["tubes"]}
        vx = sum(m * math.cos(math.radians(ang_by[t])) for t, m in mass_by.items())
        vy = sum(m * math.sin(math.radians(ang_by[t])) for t, m in mass_by.items())
        self.assertAlmostEqual(100.0 * math.hypot(vx, vy),
                               out["worst_case"]["u1_mag_g_mm"], places=6)

    def test_naive_scalar_bound_is_overconservative(self):
        # 两支近似对置: 精确最不利远小于标量相加的粗略上界
        plan = _plan([
            (1, 50.0, 0, 0.0),
            (2, 50.0, 1, 180.0),
        ])
        out = check_plan(plan, [
            {"tube_id": 1, "error_bound_g": 1.0},
            {"tube_id": 2, "error_bound_g": 1.0},
        ], limit_g_mm=500.0)
        # 标称完美对消为 0; 最不利误差残余为 2 g * 100 mm = 200(两误差同向相加)
        self.assertAlmostEqual(out["worst_case"]["u1_mag_g_mm"], 200.0, places=6)
        # 标量上界: |S0| + ΣRε = 0 + 200 —— 此简并情形恰好相等
        self.assertAlmostEqual(out["naive_scalar_bound_g_mm"], 200.0, places=6)

        # 三管非对称: 标量上界(以标称为底)严格大于精确最不利
        plan2 = _plan([
            (1, 50.0, 0, 0.0),
            (2, 40.0, 1, 120.0),
            (3, 30.0, 2, 240.0),
        ])
        out2 = check_plan(plan2, [
            {"tube_id": 1, "error_bound_g": 1.0},
            {"tube_id": 2, "error_bound_g": 1.0},
            {"tube_id": 3, "error_bound_g": 1.0},
        ])
        nom = out2["nominal"]["u1_mag_g_mm"]
        self.assertGreaterEqual(
            out2["naive_scalar_bound_g_mm"], out2["worst_case"]["u1_mag_g_mm"] - 1e-9)
        # 以标称为底的标量法 = 标称|U₁| + ΣRε; 此几何下严格过保守
        self.assertAlmostEqual(
            out2["naive_scalar_bound_g_mm"],
            nom + out2["error_scalar_sum_g_mm"], places=9)
        self.assertGreater(
            out2["naive_scalar_bound_g_mm"] - out2["worst_case"]["u1_mag_g_mm"], 1e-6)

    def test_verdict_limit(self):
        plan = _plan([(1, 50.0, 0, 0.0), (2, 50.0, 1, 180.0)])
        out = check_plan(plan, [
            {"tube_id": 1, "error_bound_g": 1.0},
            {"tube_id": 2, "error_bound_g": 1.0},
        ], limit_g_mm=150.0)
        self.assertTrue(out["verdict"]["nominal_pass"])       # 标称 0
        self.assertFalse(out["verdict"]["worst_case_pass"])   # 最不利 200 > 150
        self.assertAlmostEqual(out["verdict"]["margin_g_mm"], -50.0, places=6)

        out_ok = check_plan(plan, [
            {"tube_id": 1, "error_bound_g": 0.2},
            {"tube_id": 2, "error_bound_g": 0.2},
        ], limit_g_mm=100.0)
        self.assertTrue(out_ok["verdict"]["worst_case_pass"])

    def test_nominal_matches_saved_metrics(self):
        plan = _plan([(1, 50.0, 0, 0.0), (2, 49.5, 1, 90.0)])
        out = check_plan(plan, [{"tube_id": 1, "error_bound_g": 0.1}])
        expect = 100.0 * math.hypot(50.0, 49.5)
        self.assertAlmostEqual(out["nominal"]["u1_mag_g_mm"], expect, places=6)
        self.assertGreaterEqual(
            out["worst_case"]["u1_mag_g_mm"], out["nominal"]["u1_mag_g_mm"] - 1e-9)

    def test_bound_exceeding_nominal_mass_rejected(self):
        plan = _plan([(1, 50.0, 0, 0.0)])
        with self.assertRaises(ValueError):
            check_plan(plan, [{"tube_id": 1, "error_bound_g": 50.5}])

    def test_negative_and_unknown_rejected(self):
        plan = _plan([(1, 50.0, 0, 0.0)])
        with self.assertRaises(ValueError):
            check_plan(plan, [{"tube_id": 1, "error_bound_g": -0.1}])
        with self.assertRaises(ValueError):
            check_plan(plan, [{"tube_id": 9, "error_bound_g": 0.1}])

    def test_bound_equal_nominal_mass_ok(self):
        # ε = m 允许(实际质量下界为 0)
        plan = _plan([(1, 50.0, 0, 0.0), (2, 50.0, 1, 180.0)])
        out = check_plan(plan, [
            {"tube_id": 1, "error_bound_g": 50.0},
            {"tube_id": 2, "error_bound_g": 50.0},
        ])
        for row in out["tubes"]:
            self.assertGreaterEqual(row["actual_mass_g"], 0.0 - 1e-9)
        # 最不利 = 全部质量同向: 100 g * 100 mm
        self.assertAlmostEqual(out["worst_case"]["u1_mag_g_mm"], 10000.0, places=6)

    def test_form_context_readonly(self):
        from app.errorcheck import error_form_context
        plan = _plan([(1, 50.0, 0, 0.0), (2, 40.0, 2, 60.0)])
        ctx = error_form_context(plan)
        self.assertEqual(ctx["plan_id"], 1)
        self.assertEqual([t["tube_id"] for t in ctx["tubes"]], [1, 2])
        self.assertEqual(ctx["tubes"][0]["angle_deg"], 0.0)
        self.assertGreater(ctx["nominal_u1_mag_g_mm"], 0.0)


if __name__ == "__main__":
    unittest.main()
