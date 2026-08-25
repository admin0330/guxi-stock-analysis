# -*- coding: utf-8 -*-
"""全面稳定性测试：多只个股 + 全部模块。"""
import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8000"
fails = []


def get(path, timeout=150):
    req = urllib.request.Request(BASE + path)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def check(name, cond, extra=""):
    tag = "OK " if cond else "FAIL"
    print(f"[{tag}] {name} {extra}")
    if not cond:
        fails.append(name)


# 多只个股全覆盖
stocks = ["600519", "000001", "300750", "002594", "688981", "601318", "000858", "600036", "002230", "300059"]
for code in stocks:
    try:
        s = get(f"/api/stock/{code}", timeout=150)
        ok = s.get("score") is not None and s.get("name")
        check(f"stock/{code}", ok, f"{s.get('name')} 评分{s.get('score')} [{s.get('label')}]")
    except Exception as e:
        check(f"stock/{code}", False, str(e)[:60])

# 涨停复盘 + 情绪
try:
    r = get("/api/limitup/review", timeout=60)
    check("limitup/review", "conclusion" in r and "leaders" in r and "ladder" in r,
          f"涨停{r.get('limit_up')} 最高{r.get('max_streak')}板 温度{r.get('temperature')}")
except Exception as e:
    check("limitup/review", False, str(e)[:60])

# 小白日报
try:
    dr = get("/api/daily/report", timeout=150)
    check("daily/report", dr.get("market_line") and dr.get("conclusion") and dr.get("gainers"),
          f"涨榜{len(dr.get('gainers', []))} 跌榜{len(dr.get('losers', []))} 板块{len(dr.get('hot_boards', []))}")
except Exception as e:
    check("daily/report", False, str(e)[:60])

# 市场温度
try:
    t = get("/api/market/temperature", timeout=60)
    check("market/temperature", "temperature" in t, f"{t.get('temperature')}分 {t.get('label')}")
except Exception as e:
    check("market/temperature", False, str(e)[:60])

# 搜索
for q in ["茅台", "600519", "宁德", "300"]:
    try:
        import urllib.parse
        sr = get(f"/api/stock/search?q={urllib.parse.quote(q)}", timeout=60)
        check(f"search/{q}", len(sr.get("results", [])) > 0, f"{len(sr.get('results', []))}个")
    except Exception as e:
        check(f"search/{q}", False, str(e)[:60])

print()
if fails:
    print(f"共 {len(fails)} 项失败: {fails}")
    sys.exit(1)
print("全部稳定性测试通过 ✓")
