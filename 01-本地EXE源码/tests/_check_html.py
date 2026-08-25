# -*- coding: utf-8 -*-
import re

html = open("frontend/index.html", encoding="utf-8").read()

for t in ["div", "span", "section", "table", "thead", "tbody", "tr", "ul", "li", "button", "b", "em", "small", "p", "h1", "h2"]:
    o = len(re.findall(r"<%s(\s|>)" % t, html))
    c = len(re.findall(r"</%s>" % t, html))
    status = "OK" if o == c else "IMBALANCE"
    print(f"{t}: open={o} close={c} {status}")

print()
lines = html.split("\n")
for i, line in enumerate(lines, 1):
    if "<span" in line and "</span>" not in line:
        print(f"line {i} span NO CLOSE: {line.strip()[:130]}")
    if "<div" in line and "</div>" not in line:
        print(f"line {i} div NO CLOSE: {line.strip()[:130]}")
