#!/usr/bin/env python3
"""Build the dissertation figures with Apache ECharts and Mermaid.

1. Python reads the committed evidence under reports/ and computes each
   figure's numbers (nothing is typed in by hand).
2. Each figure is written as a small HTML page: shared theme
   (figures/src/theme.js) + its chart builder (figures/src/charts.js), or a
   Mermaid diagram (figures/src/*.mmd).
3. Headless Google Chrome renders each page to a 300-dpi PNG for Word.

Run:  python docs/thesis/make_figures.py
Out:  docs/thesis/figures/fig_*.png   (pages kept in figures/src/build/)

Needs: pandas, numpy, pillow; Google Chrome; internet for the two CDN scripts
(echarts@5.5.1, mermaid@10.9.1, pinned).
"""
from __future__ import annotations

import glob
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parents[2]
REPORTS = ROOT / "reports"
FIG = Path(__file__).resolve().parent / "figures"
SRC = FIG / "src"
BUILD = SRC / "build"
BUILD.mkdir(parents=True, exist_ok=True)

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CALIBRI = "file:///Applications/Microsoft%20Word.app/Contents/Resources/DFonts/Calibri.ttf"
CALIBRI_B = "file:///Applications/Microsoft%20Word.app/Contents/Resources/DFonts/Calibrib.ttf"
ECHARTS = "https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js"
MERMAID = "https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js"

WIDTH_PX = 630      # CSS px; rendered at 3x = 1890 px = 6.3 in at 300 dpi
SCALE = 3
PAIRS = {"ETHUSDT": "ETH", "BNBUSDT": "BNB"}


# ---------------------------------------------------------------- data
def load_traces(pair: str, policy: str) -> pd.DataFrame:
    files = sorted(glob.glob(str(REPORTS / "exposure_traces" / f"{pair}_{policy}_fold*.csv")))
    return pd.concat((pd.read_csv(f) for f in files), ignore_index=True)


def fold_drawdowns(pair: str, policy: str) -> list[float]:
    """Canonical max drawdown per fold (multiplicative equity curve), in %."""
    out = []
    for fold in range(6):
        r = pd.read_csv(REPORTS / "returns" / f"{pair}_{policy}_fold{fold}.csv")["return"]
        eq = (1 + r).cumprod()
        out.append(round(float((1 - eq / eq.cummax()).max()) * 100, 2))
    return out


TR = {(p, pol): load_traces(p, pol) for p in PAIRS for pol in ("trained", "untrained")}
DEFS = json.loads((REPORTS / "exposure_definitions.json").read_text())


def expo(df: pd.DataFrame) -> pd.Series:
    return df["position_value/equity"].abs()


def data_walk_forward():
    wf = json.loads((REPORTS / "rl_walk_forward_ETHUSDT.json").read_text())["slices"]
    ms = lambda s: int(pd.Timestamp(s).timestamp() * 1000)
    folds = [{"train": [ms(s["train_start"]), ms(s["train_end"])],
              "embargo": [ms(s["embargo_start"]), ms(s["embargo_end"])],
              "test": [ms(s["test_start"]), ms(s["test_end"])]} for s in wf]
    day = 86_400_000
    return {"folds": folds, "xmin": folds[0]["train"][0] - 10 * day, "xmax": folds[-1]["test"][1] + 10 * day}


def data_time_vs_capital():
    panels = []
    for pair, name in PAIRS.items():
        d, un = DEFS[pair], TR[(pair, "untrained")]
        rows = [
            (d["ppo"]["time_in_market"], d["ppo"]["capital_weighted_exposure"]),
            (d["rf"]["time_in_market"], d["rf"]["capital_weighted_exposure"]),
            ((un.action_label != "FLAT").mean(), expo(un).mean()),
            (1.0, 1.0),
        ]
        panels.append({"name": name, "rows": [{"time": t * 100, "capital": c * 100} for t, c in rows]})
    return {"panels": panels}


def data_exposure_split():
    """FIX_REPORT B13.4: counterfactual = untrained mean exposure on active bars.
    Concealed = trained active share x (untrained active mean - trained active mean);
    explicit = rest of the gap. Reproduces 20.5491 / 24.3362 (ETH) exactly."""
    rows = []
    for pair, name in PAIRS.items():
        tr, un = TR[(pair, "trained")], TR[(pair, "untrained")]
        e_tr, e_un = expo(tr).mean() * 100, expo(un).mean() * 100
        un_act = expo(un[un.action_label != "FLAT"]).mean() * 100
        tr_act = expo(tr[tr.action_label != "FLAT"]).mean() * 100
        concealed = (tr.action_label != "FLAT").mean() * (un_act - tr_act)
        rows.append({"name": name, "deployed": e_tr, "explicit": (e_un - e_tr) - concealed,
                     "concealed": concealed, "total": e_un})
    return {"rows": rows}


def data_action_mix():
    panels = []
    for pair, name in PAIRS.items():
        p = {"name": name}
        for pol in ("trained", "untrained"):
            fam = TR[(pair, pol)].action_label.str.split("_").str[0]
            fam = fam.replace({"trend": "immediate", "swing": "immediate"})
            share = fam.value_counts(normalize=True) * 100
            p[pol] = [round(float(share.get(k, 0.0)), 2) for k in ("FLAT", "grid", "immediate")]
        panels.append(p)
    return {"panels": panels}


def data_fold_drawdowns():
    panels = []
    for pair, name in PAIRS.items():
        p = {"name": name, "median": {}}
        for k in ("ppo", "rf", "ta"):
            p[k] = fold_drawdowns(pair, k)
            p["median"][k] = float(np.median(p[k]))
        panels.append(p)
    return {"panels": panels}


def data_flat_vs_trained():
    cells = pd.DataFrame(json.loads((REPORTS / "flat_vs_trained_by_seed.json").read_text()))
    groups, out = [], []
    for gi, ((pair, fold), g) in enumerate(cells.groupby(["pair", "fold"], sort=False)):
        groups.append(f"{PAIRS[pair]} · fold {fold + 1}")
        for x, v in zip(gi + np.linspace(-0.22, 0.22, len(g)), g.diff_trained_minus_flat):
            out.append({"x": round(float(x), 3), "diff": float(v)})
    return {"groups": groups, "cells": out, "n_lose": sum(c["diff"] < 0 for c in out)}


def data_detectable_effect():
    mde = json.loads((REPORTS / "mde_power.json").read_text())["per_bar_basis"]
    return {"rows": [{"name": name,
                      "mde": mde[pair]["new"]["mde_cumulative_pp_compounded"],
                      "observed": mde[pair]["new"]["cumulative_diff_pp_compounded"]}
                     for pair, name in PAIRS.items()]}


CHARTS = {  # name: (data builder, height in CSS px)
    "fig_3_2_walk_forward": (data_walk_forward, 250),
    "fig_4_1_time_vs_capital": (data_time_vs_capital, 270),
    "fig_4_2_exposure_split": (data_exposure_split, 280),
    "fig_4_3_action_mix": (data_action_mix, 250),
    "fig_4_4_fold_drawdowns": (data_fold_drawdowns, 290),
    "fig_4_5_flat_vs_trained": (data_flat_vs_trained, 280),
    "fig_4_6_detectable_effect": (data_detectable_effect, 190),
}


# ---------------------------------------------------------------- pages
HEAD = f"""<!doctype html><html><head><meta charset="utf-8">
<style>
@font-face {{ font-family: Calibri; src: url("{CALIBRI}"); }}
@font-face {{ font-family: Calibri; font-weight: bold; src: url("{CALIBRI_B}"); }}
html, body {{ margin: 0; background: #fff; font-family: Calibri, sans-serif; }}
</style>"""


def chart_page(name: str, data: dict, height: int) -> str:
    return f"""{HEAD}
<script src="{ECHARTS}"></script>
<script src="../theme.js"></script><script src="../charts.js"></script></head>
<body><div id="chart" style="width:{WIDTH_PX}px;height:{height}px"></div>
<script>
document.fonts.load('13px Calibri').then(() => render(FIGURES['{name}']({json.dumps(data)})));
</script></body></html>"""


def mermaid_page(definition: str) -> str:
    return f"""{HEAD}
<style>
#d {{ width: {WIDTH_PX}px; margin: 0; }}
#d small {{ color: #666666; font-size: 12px; line-height: 1.3; }}
#d .cluster-label span {{ font-weight: bold; font-size: 12px; letter-spacing: .02em; color: #666666 !important; }}
#d .edgeLabel, #d .edgeLabel p {{ color: #666666; font-size: 12px; background: transparent !important; }}
</style>
<script src="{MERMAID}"></script></head>
<body><pre id="d" class="mermaid">{definition}</pre>
<script>
mermaid.initialize({{
  startOnLoad: true, securityLevel: 'loose', theme: 'base',
  flowchart: {{ htmlLabels: true, curve: 'basis', useMaxWidth: true, nodeSpacing: 22, rankSpacing: 34, padding: 10 }},
  themeVariables: {{ fontFamily: 'Calibri, sans-serif', fontSize: '14px', primaryTextColor: '#191919',
    lineColor: '#8a8a8a', primaryColor: '#ffffff', primaryBorderColor: '#d2d2d2',
    clusterBkg: '#f6f6f6', clusterBorder: 'transparent', edgeLabelBackground: 'transparent' }}
}});
</script></body></html>"""


# ---------------------------------------------------------------- render
def trim(png: Path, pad: int = 18) -> None:
    """Crop surplus white margin (Chrome renders the whole window)."""
    im = Image.open(png).convert("RGB")
    bbox = ImageChops.difference(im, Image.new("RGB", im.size, "white")).getbbox()
    if bbox:
        l, t, r, b = bbox
        im = im.crop((max(l - pad, 0), max(t - pad, 0), min(r + pad, im.width), min(b + pad, im.height)))
    im.save(png, dpi=(300, 300))


def shoot(page: Path, out: Path, height: int) -> None:
    subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                    "--allow-file-access-from-files", f"--force-device-scale-factor={SCALE}",
                    f"--window-size={WIDTH_PX + 20},{height}", "--virtual-time-budget=10000",
                    f"--screenshot={out}", page.as_uri()],
                   check=True, capture_output=True)
    trim(out)
    print("wrote", out.name)


if __name__ == "__main__":
    for old in FIG.glob("fig_*.pdf"):   # vector copies from the earlier matplotlib version
        old.unlink()
    mmd = (SRC / "fig_3_1_architecture.mmd").read_text()
    page = BUILD / "fig_3_1_architecture.html"
    page.write_text(mermaid_page(mmd))
    shoot(page, FIG / "fig_3_1_architecture.png", 900)

    for name, (builder, h) in CHARTS.items():
        page = BUILD / f"{name}.html"
        page.write_text(chart_page(name, builder(), h))
        shoot(page, FIG / f"{name}.png", h + 20)
