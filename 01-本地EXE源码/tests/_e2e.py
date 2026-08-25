# -*- coding: utf-8 -*-
"""端到端验证：逐个调用所有 API 端点并检查关键字段。"""
import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8000"
fails = []


def get(path, timeout=120):
    req = urllib.request.Request(BASE + path)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def check(name, cond, extra=""):
    tag = "OK " if cond else "FAIL"
    print(f"[{tag}] {name} {extra}")
    if not cond:
        fails.append(name)


# 1. 大盘总览
try:
    ov = get("/api/market/overview")
    check("market/overview", ov.get("indices") and ov.get("breadth") and "temperature" in ov,
          f"指数{len(ov.get('indices', []))}个 温度{ov.get('temperature', {}).get('temperature')}")
except Exception as e:
    check("market/overview", False, str(e)[:80])

# 2. 指数详情
try:
    d = get("/api/market/index/sh000001", timeout=60)
    check("market/index", d.get("kline") and len(d.get("kline", [])) > 50, f"K线{len(d.get('kline', []))}根")
except Exception as e:
    check("market/index", False, str(e)[:80])

# 3. 涨停复盘
try:
    r = get("/api/limitup/review", timeout=60)
    check("limitup/review", "conclusion" in r and "ladder" in r and "leaders" in r,
          f"涨停{r.get('limit_up')} 连板{r.get('max_streak')} 阶段{r.get('phase')}")
except Exception as e:
    check("limitup/review", False, str(e)[:80])

# 4. 小白日报
try:
    dr = get("/api/daily/report", timeout=120)
    check("daily/report", dr.get("market_line") and dr.get("conclusion"),
          f"涨榜{len(dr.get('gainers', []))} 板块{len(dr.get('hot_boards', []))}")
except Exception as e:
    check("daily/report", False, str(e)[:80])

# 5. 个股分析
try:
    s = get("/api/stock/600519", timeout=120)
    check("stock/600519", s.get("score") is not None and s.get("name"),
          f"{s.get('name')} 评分{s.get('score')} 技术{s.get('breakdown', {}).get('technical')}")
except Exception as e:
    check("stock/600519", False, str(e)[:80])

# 6. 个股K线
try:
    kl = get("/api/stock/600519/kline?limit=60", timeout=60)
    check("stock/kline", len(kl.get("kline", [])) > 30, f"{len(kl.get('kline', []))}根")
except Exception as e:
    check("stock/kline", False, str(e)[:80])

# 7. 搜索
try:
    sr = get("/api/stock/search?q=%E8%8C%85%E5%8F%B0", timeout=60)  # 茅台
    check("stock/search", len(sr.get("results", [])) > 0, f"{len(sr.get('results', []))}个结果")
except Exception as e:
    check("stock/search", False, str(e)[:80])

print()
if fails:
    print(f"共 {len(fails)} 项失败: {fails}")
    sys.exit(1)
print("全部端点验证通过 ✓")
