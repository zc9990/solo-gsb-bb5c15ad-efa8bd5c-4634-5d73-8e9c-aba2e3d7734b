"""无第三方依赖环境下, 用最小桩模块驱动 app/main.py 的端点逻辑做端到端冒烟测试。"""
import sys, types, json, os, tempfile, asyncio

# ---- 桩: pydantic ----
pyd = types.ModuleType("pydantic")
class FieldInfo:
    def __init__(self, default=None, **kw):
        self.default = default; self.kw = kw
def Field(default=None, **kw):
    if default is None and "default_factory" in kw:
        return FieldInfo(default=kw["default_factory"](), **kw)
    return FieldInfo(default=default, **kw)
class BaseModel:
    def __init__(self, **data):
        cls = type(self)
        for name, f in cls.model_fields().items():
            val = data.get(name, f.default if not callable(getattr(f.default, "__call__", None)) else None)
            if val is None and isinstance(f.default, (list,)):
                val = list(f.default)
            setattr(self, name, val)
        self.__post_init_check__(data)
    def __post_init_check__(self, data):
        for k in data:
            if not hasattr(type(self), "_ann"): pass
    def model_dump(self):
        return {k: getattr(self, k) for k in type(self).model_fields()}
    @classmethod
    def model_fields(cls):
        return getattr(cls, "_fields", {})
    @classmethod
    def parse(cls, data):
        # 简易类型转换(数字/布尔/嵌套)
        return cls(**data)
def model_validate(cls, data):
    return _build(cls, data)
def _build(cls, data):
    obj = cls.__new__(cls)
    fields = cls.model_fields()
    for name, f in fields.items():
        val = data.get(name, _factory_default(f))
        ann = cls._annotations.get(name)
        val = _convert(val, ann)
        setattr(obj, name, val)
    return obj
def _factory_default(f):
    return f.default
def _convert(val, ann):
    if val is None: return None
    import typing

pyd.BaseModel = BaseModel
pyd.Field = Field

# 更健壮:重写 BaseModel 以处理嵌套 list[Model] 与 Optional
class BM:
    def __init__(self, **kw):
        for name, f in type(self).model_fields().items():
            setattr(self, name, kw.get(name, _default(f)))
        self._validate(kw)
    def _validate(self, kw):
        allowed = set(type(self).model_fields())
        for k in kw:
            if k not in allowed: raise ValueError(f"unexpected field {k}")
    def model_dump(self):
        out = {}
        for k, f in type(self).model_fields().items():
            v = getattr(self, k)
            if isinstance(v, list):
                out[k] = [x.model_dump() if hasattr(x, "model_dump") else x for x in v]
            elif hasattr(v, "model_dump"):
                out[k] = v.model_dump()
            else:
                out[k] = v
        return out
    @classmethod
    def model_fields(cls):
        return cls._fields
    @classmethod
    def validate(cls, data):
        return _build_model(cls, data)
def _default(f):
    d = f.default
    return list(d) if isinstance(d, list) else d
def _build_model(cls, data):
    obj = cls.__new__(cls)
    for name, f in cls.model_fields().items():
        raw = data.get(name, _default(f))
        setattr(obj, name, _coerce(raw, cls._annotations.get(name)))
    return obj
def _coerce(raw, ann):
    if raw is None: return None
    # 解析泛型
    origin = getattr(ann, "__origin__", None)
    if origin is list:
        inner = ann.__args__[0]
        if isinstance(inner, type) and issubclass(inner, BM):
            return [_build_model(inner, x) for x in raw]
        return list(raw)
    if isinstance(ann, type) and issubclass(ann, BM):
        return _build_model(ann, raw)
    return raw
pyd.BaseModel = BM

# ---- 桩: fastapi ----
fa = types.ModuleType("fastapi")
fa.__path__ = []
class FileResponse:
    def __init__(self, path): self.path = path
class JSONResponse:
    def __init__(self, content, headers=None):
        self.content = content; self.headers = headers or {}
far = types.ModuleType("fastapi.responses")
far.FileResponse = FileResponse
far.JSONResponse = JSONResponse
fa.responses = far
class HTTPException(Exception):
    def __init__(self, status_code=400, detail=""):
        self.status_code=status_code; self.detail=detail
class _RouterLike:
    def __init__(self): self.routes=[]
    def add(self, method, path, fn): self.routes.append((method, path, fn))
    def get(self,p,*a,**k):
        def d(f): self.add("GET",p,f); return f
        return d
    def post(self,p,*a,**k):
        def d(f): self.add("POST",p,f); return f
        return d
    def delete(self,p,*a,**k):
        def d(f): self.add("DELETE",p,f); return f
        return d
class FastAPI(_RouterLike):
    def __init__(self, title="", version=""):
        super().__init__(); self.title=title; self.startup=[]
    def on_event(self, ev):
        def d(f):
            if ev=="startup": self.startup.append(f)
            return f
        return d
    def mount(self,*a,**k): pass
fa.FastAPI=FastAPI; fa.HTTPException=HTTPException

st=types.ModuleType("fastapi.staticfiles")
class StaticFiles:
    def __init__(self,directory=None): self.directory=directory
st.StaticFiles=StaticFiles

sys.modules["pydantic"]=pyd
sys.modules["fastapi"]=fa
sys.modules["fastapi.responses"]=far
sys.modules["fastapi.staticfiles"]=st

# ---- 导入被测应用 ----
tmpdir = tempfile.mkdtemp()
os.environ["BALANCER_DB"] = os.path.join(tmpdir, "test.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import main  # noqa
from app.main import app

for f in app.startup: f()

def find(method, path):
    import re
    path = path.split("?", 1)[0]
    for m,p,fn in app.routes:
        if m!=method: continue
        # 把 {param} 转为单段正则([^/]+)
        pat = "^" + re.sub(r"\{[^}]+\}", r"([^/]+)", p) + "$"
        mmx = re.match(pat, path)
        if mmx:
            args = []
            for a in mmx.groups():
                args.append(int(a) if a.isdigit() else a)
            return fn, tuple(args)
    return None

def call(method, path, body=None):
    raw_query = path.split("?", 1)[1] if "?" in path else ""
    r = find(method,path)
    if isinstance(r, tuple): fn, args = r
    else: fn, args = r, ()
    if raw_query:
        import urllib.parse
        qargs = {}
        for k, v in urllib.parse.parse_qsl(raw_query):
            qargs[k] = int(v) if v.lstrip("-").isdigit() else v
        args = tuple(qargs.get(p) for p in fn.__code__.co_varnames[:fn.__code__.co_argcount])
    if body is not None:
        model_cls = main.PlanRequest
        req = _build_concrete(model_cls, body)
        return fn(req)
    return fn(*args)

def _build_concrete(cls, data):
    """根据 main.py 中具体 Pydantic 模型做嵌套转换。"""
    def slot(d):
        o = types.SimpleNamespace(**d)
        o.model_dump = lambda d=d: dict(d)
        return o
    def tube(d):
        o = types.SimpleNamespace(
            id=int(d["id"]), mass=float(d["mass"]),
            locked=bool(d.get("locked", False)),
            slot=(int(d["slot"]) if d.get("slot") is not None else None))
        o.model_dump = lambda: {"id": o.id, "mass": o.mass, "locked": o.locked, "slot": o.slot}
        return o
    def prev(d):
        o = types.SimpleNamespace(tube_id=int(d["tube_id"]), slot=int(d["slot"]))
        o.model_dump = lambda: {"tube_id": o.tube_id, "slot": o.slot}
        return o
    defaults = {
        "name":"未命名方案","slot_count":12,"slots":[],
        "radius_mm":100.0,"rpm":4000.0,"w1":1.0,"w2":0.000001,
        "move_weight":0.0,"restarts":10,"chain_steps":2500,
        "seed":20260923,"previous":None,"parent_id":None,"save":True,
    }
    merged = {**defaults, **data}
    return types.SimpleNamespace(
        name=merged["name"],
        slot_count=int(merged["slot_count"]),
        slots=[slot(d) for d in merged["slots"]],
        tubes=[tube(d) for d in merged["tubes"]],
        radius_mm=float(merged["radius_mm"]), rpm=float(merged["rpm"]),
        w1=float(merged["w1"]), w2=float(merged["w2"]),
        move_weight=float(merged["move_weight"]),
        restarts=int(merged["restarts"]), chain_steps=int(merged["chain_steps"]),
        seed=int(merged["seed"]),
        previous=([prev(d) for d in merged["previous"]] if merged["previous"] is not None else None),
        parent_id=merged["parent_id"], save=bool(merged.get("save", True)),
    )

# ---- 用例 ----
slots=[{"index":i,"angle":i*30,"enabled": i!=6} for i in range(12)]
req_body={"name":"冒烟方案","slot_count":12,"slots":slots,
          "tubes":[{"id":1,"mass":50},{"id":2,"mass":50},{"id":3,"mass":51},
                   {"id":4,"mass":49},{"id":5,"mass":30,"locked":True,"slot":0},
                   {"id":6,"mass":30}],
          "radius_mm":100,"rpm":4000,"restarts":4,"chain_steps":800}
rec = call("POST","/api/plan",req_body)
assert rec["id"]==1, rec.keys()
m=rec["result"]["metrics"]
print("plan#1 U1=%.2f U2=%.2f grade=%s F=%.3f M=%.4f steps=%d" % (
    m["u1_mag_g_mm"],m["u2_mag_g_mm"],m["grade"],
    m["centrifugal_force_n"],m["resultant_moment_n_m"],len(rec["result"]["steps"])))
# locked respected
assert next(p for p in rec["result"]["placements"] if p["tube_id"]==5)["slot"]==0
# disabled respected
assert all(p["slot"]!=6 for p in rec["result"]["placements"])

# 400 validation
bad=dict(req_body); bad["tubes"]=bad["tubes"]*4
try:
    call("POST","/api/plan",bad); raise SystemExit("should 400")
except main.HTTPException as e:
    assert e.status_code==400; print("validation 400 OK:",e.detail)

# 增量重排
inc=dict(req_body); inc["name"]="增量方案"
inc["tubes"]=[dict(t) for t in req_body["tubes"]]+[{"id":7,"mass":20}]
inc["previous"]=[{"tube_id":p["tube_id"],"slot":p["slot"]} for p in rec["result"]["placements"]]
inc["parent_id"]=1
rec2=call("POST","/api/plan",inc)
print("plan#2 incremental U1=%.2f moved=%s" % (
    rec2["result"]["metrics"]["u1_mag_g_mm"], rec2["result"]["moved_tube_ids"]))

# 方案比较
cmp12 = call("GET", "/api/plans/compare?a=1&b=2")
assert cmp12["plan_a"]["id"] == 1 and cmp12["plan_b"]["id"] == 2
# plan#2 比 plan#1 多一支试管 -> 有 1 条 added, 无 removed
assert cmp12["placement_counts"]["added"] == 1, cmp12["placement_counts"]
added = [r for r in cmp12["placements"] if r["status"] == "added"]
assert added and added[0]["tube_id"] == 7
# 试管清单不同 -> 非同条件, 必须在参数差异中体现
assert cmp12["same_conditions"] is False
assert "tubes" in cmp12["differing_groups"]
# 指标并列含一阶/二阶模值与角度, 差值为 B-A
um = {x["key"]: x for x in cmp12["metrics"]}
assert set(("u1_mag_g_mm", "u2_mag_g_mm", "u1_angle_deg", "u2_angle_deg")) <= set(um)
assert abs(um["u1_mag_g_mm"]["delta_b_minus_a"]
           - (rec2["result"]["metrics"]["u1_mag_g_mm"]
              - rec["result"]["metrics"]["u1_mag_g_mm"])) < 1e-6
print("compare added/diff-condition OK")

# 同条件比较: 用相同输入再求解保存一份(plan#3)
same = dict(req_body); same["name"] = "同条件副本"
rec3 = call("POST", "/api/plan", same)
cmp13 = call("GET", "/api/plans/compare?a=1&b=3")
assert cmp13["same_conditions"] is True, cmp13["differing_groups"]
assert not cmp13["input_diff"]["rotor"]
assert not cmp13["input_diff"]["solver"]
assert not cmp13["input_diff"]["tubes"]
assert all(r["status"] == "unchanged" for r in cmp13["placements"])
assert all(abs(r["delta_b_minus_a"]) < 1e-9
           for r in cmp13["metrics"] if not r["is_angle"])
print("compare same-conditions OK")

# a==b 拒绝; 不存在 404
try:
    call("GET", "/api/plans/compare?a=1&b=1"); raise SystemExit("should 400")
except main.HTTPException as e:
    assert e.status_code == 400
try:
    call("GET", "/api/plans/compare?a=1&b=999"); raise SystemExit("should 404")
except main.HTTPException as e:
    assert e.status_code == 404
print("compare validation OK")

# 列表 / 详情 / 导出 / 删除
lst=call("GET","/api/plans")
assert len(lst["items"])==3
detail=call("GET","/api/plan/2")
assert detail["result"]["total_tubes"]==7
exp=call("GET","/api/plan/2/export")
assert json.loads(json.dumps(exp.content))["id"]==2
d=call("DELETE","/api/plan/1")
assert d["deleted"]==1
try:
    call("GET","/api/plan/1"); raise SystemExit("should 404")
except main.HTTPException as e:
    assert e.status_code==404
print("list/detail/export/delete OK")

# health/config
assert call("GET","/api/health")["status"]=="ok"
assert "physics" in call("GET","/api/config")
print("health/config OK")

# 纯等量完美平衡
perf=dict(req_body); perf["tubes"]=[{"id":i,"mass":50} for i in range(1,5)]
perf["slots"]=[{"index":i,"angle":i*30,"enabled":True} for i in range(12)]
rp=call("POST","/api/plan",perf)
assert rp["result"]["metrics"]["u1_mag_g_mm"]<1e-6
print("perfect balance U1=0 OK")
print("BACKEND SMOKE TESTS PASS")
