import warnings
warnings.filterwarnings("ignore")
from backend.analysis import stock

for code in ["000001", "300750", "002594", "688981", "600519"]:
    try:
        r = stock.analyze(code)
        name = r.get("name", "?")
        score = r.get("score", 0)
        label = r.get("label", "?")
        bd = r.get("breakdown", {})
        print(f"{code} OK: {name} 评分{score} [{label}] 技术{bd.get('technical')}/基本{bd.get('fundamental')}/资金{bd.get('fund')}")
    except Exception as e:
        print(f"{code} FAIL: {type(e).__name__} {str(e)[:100]}")
