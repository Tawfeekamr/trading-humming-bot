// One builder per figure. Each takes the DATA object written by make_figures.py
// and returns an ECharts option. Nothing here contains a hand-typed result.

const FIGURES = {

  // ------------------------------------------------------------ Figure 3.2
  fig_3_2_walk_forward(D) {
    const kinds = [
      ['train', 'Train (180 days)', C.trainFill],
      ['embargo', 'Embargo (70 bars)', C.embargo],
      ['test', 'Test (30 days)', C.ppo],
    ];
    const n = D.folds.length;
    const series = kinds.map(([key, name, color]) => ({
      type: 'custom', name, itemStyle: { color },
      renderItem(params, api) {
        const a = api.coord([api.value(0), api.value(2)]);
        const b = api.coord([api.value(1), api.value(2)]);
        const h = api.size([0, 1])[1] * 0.5;
        return { type: 'rect', shape: { x: a[0], y: a[1] - h / 2, width: Math.max(b[0] - a[0], 2), height: h, r: key === 'test' ? [0, 3, 3, 0] : key === 'train' ? [3, 0, 0, 3] : 0 }, style: { fill: color } };
      },
      encode: { x: [0, 1], y: 2 },
      data: D.folds.map((f, i) => [f[key][0], f[key][1], `Fold ${i + 1}`]),
    }));
    return {
      grid: { left: 60, right: 20, top: 10, bottom: 54 },
      xAxis: axisValue({ type: 'time', min: D.xmin, max: D.xmax,
        minInterval: 89 * 86400000, maxInterval: 93 * 86400000,
        axisLabel: { color: C.ink2, fontSize: 12.5, fontFamily: FONT, formatter: '{MMM} {yyyy}', hideOverlap: true } }),
      yAxis: axisCategory({ data: D.folds.map((_, i) => `Fold ${i + 1}`), inverse: true, axisLine: { show: false } }),
      legend: legend({ data: kinds.map(k => k[1]) }),
      series,
    };
  },

  // ------------------------------------------------------------ Figure 4.1
  fig_4_1_time_vs_capital(D) {
    const rows = ['PPO (trained)', 'Random Forest', 'Untrained network', 'Buy and hold'];
    const colors = [C.ppo, C.rf, C.un, C.hold];
    const grids = [{ left: 150, width: 205, top: 40, bottom: 58 }, { left: 395, width: 205, top: 40, bottom: 58 }];
    const series = [];
    D.panels.forEach((p, g) => {
      p.rows.forEach((r, i) => {
        const y = rows[i];
        series.push({ type: 'line', xAxisIndex: g, yAxisIndex: g, silent: true, symbol: 'none',
          lineStyle: { color: colors[i], width: 3, opacity: 0.35, cap: 'round' },
          data: [[r.capital, y], [r.time, y]] });
        const lab = (v, dec) => ({ show: i === 0, position: 'top', distance: 7, color: C.ink, fontSize: 12.5, fontFamily: FONT, formatter: `${v.toFixed(dec)}%` });
        series.push({ type: 'scatter', xAxisIndex: g, yAxisIndex: g, symbolSize: 11, z: 4,
          itemStyle: { color: colors[i], borderColor: C.surface, borderWidth: 2 },
          data: [{ value: [r.capital, y], label: lab(r.capital, 1) }] });
        series.push({ type: 'scatter', xAxisIndex: g, yAxisIndex: g, symbolSize: 12, z: 3,
          itemStyle: { color: C.surface, borderColor: colors[i], borderWidth: 2.6 },
          data: [{ value: [r.time, y], label: lab(r.time, 0) }] });
      });
    });
    // Legend-only series explaining the two marker kinds.
    series.push({ type: 'scatter', name: 'Capital deployed (mean |position| / equity)', data: [], itemStyle: { color: C.ink2 } });
    series.push({ type: 'scatter', name: 'Reported time in market (active-engine bars)', data: [], itemStyle: { color: C.surface, borderColor: C.ink2, borderWidth: 2 } });
    return {
      title: D.panels.map((p, g) => panelTitle(p.name, grids[g].left - 4)),
      grid: grids,
      xAxis: grids.map((_, g) => axisValue({ gridIndex: g, min: 0, max: 100, interval: 25, axisLabel: { formatter: pct, color: C.ink2, fontSize: 12.5, fontFamily: FONT } })),
      yAxis: grids.map((_, g) => axisCategory({ gridIndex: g, data: rows, inverse: true, axisLine: { show: false }, axisLabel: { show: g === 0, color: C.ink, fontSize: 13.5, fontFamily: FONT, margin: 14 } })),
      legend: legend({ data: series.filter(s => s.name).map(s => s.name), icon: 'circle', itemWidth: 11, itemHeight: 11 }),
      series,
    };
  },

  // ------------------------------------------------------------ Figure 4.2
  fig_4_2_exposure_split(D) {
    const names = [`Deployed by PPO (${D.rows[0].deployed.toFixed(1)} ETH, ${D.rows[1].deployed.toFixed(1)} BNB)`,
                   'Explicit abstention (chose FLAT)', 'Concealed abstention (active action, little capital)'];
    const keys = ['deployed', 'explicit', 'concealed'];
    const colors = [C.dep, C.exp, C.con];
    const textOn = ['#ffffff', '#0b0b0b', '#0b0b0b'];
    const series = keys.map((k, i) => ({
      type: 'bar', name: names[i], stack: 'x', barWidth: 34,
      itemStyle: { color: colors[i], borderColor: C.surface, borderWidth: 2 },
      label: { show: true, position: 'insideLeft', distance: 8, color: textOn[i], fontSize: 13, fontFamily: FONT,
        formatter: (p) => (p.value > 6 ? p.value.toFixed(1) : '') },
      data: D.rows.map(r => r[k]),
    }));
    series.push({ type: 'scatter', symbolSize: 0, silent: true,
      label: { show: true, position: 'right', distance: 10, color: C.ink2, fontSize: 12.5, fontFamily: FONT,
        formatter: (p) => `${p.value[0].toFixed(1)}% untrained network` },
      data: D.rows.map(r => [r.total, r.name]) });
    return {
      grid: { left: 50, right: 20, top: 8, bottom: 128 },
      xAxis: axisValue({ min: 0, max: 70, interval: 10, name: 'Capital-weighted exposure (percentage points)', nameLocation: 'middle', nameGap: 30,
        axisLabel: { formatter: pct, color: C.ink2, fontSize: 12.5, fontFamily: FONT } }),
      yAxis: axisCategory({ data: D.rows.map(r => r.name), inverse: true, axisLine: { show: false } }),
      legend: legend({ data: names, orient: 'vertical', left: 50, bottom: 0, itemGap: 6 }),
      series,
    };
  },

  // ------------------------------------------------------------ Figure 4.3
  fig_4_3_action_mix(D) {
    const cats = ['FLAT (do nothing)', 'Grid', 'Trend + swing'];
    const grids = [{ left: 128, width: 215, top: 36, bottom: 52 }, { left: 385, width: 215, top: 36, bottom: 52 }];
    const series = [];
    D.panels.forEach((p, g) => {
      [['trained', 'PPO (trained)', C.ppo], ['untrained', 'Untrained network', C.un]].forEach(([k, name, color]) => {
        series.push({ type: 'bar', name, xAxisIndex: g, yAxisIndex: g, barWidth: 12, barGap: '35%',
          itemStyle: { color, borderRadius: [0, 3, 3, 0] },
          label: { show: true, position: 'right', distance: 6, color: C.ink2, fontSize: 12, fontFamily: FONT, formatter: (q) => `${q.value.toFixed(1)}%` },
          data: p[k] });
      });
    });
    return {
      title: D.panels.map((p, g) => panelTitle(p.name, grids[g].left - 4)),
      grid: grids,
      xAxis: grids.map((_, g) => axisValue({ gridIndex: g, min: 0, max: 80, interval: 20, axisLabel: { formatter: pct, color: C.ink2, fontSize: 12.5, fontFamily: FONT } })),
      yAxis: grids.map((_, g) => axisCategory({ gridIndex: g, data: cats, inverse: true, axisLine: { show: false }, axisLabel: { show: g === 0, color: C.ink, fontSize: 13.5, fontFamily: FONT } })),
      legend: legend({ data: ['PPO (trained)', 'Untrained network'] }),
      series,
    };
  },

  // ------------------------------------------------------------ Figure 4.4
  fig_4_4_fold_drawdowns(D) {
    const pols = [['ppo', 'PPO (trained)', C.ppo], ['rf', 'Random Forest', C.rf], ['ta', 'Buy and hold', C.hold]];
    const grids = [{ left: 52, width: 196, top: 36, bottom: 70 }, { left: 346, width: 196, top: 36, bottom: 70 }];
    const series = [];
    D.panels.forEach((p, g) => {
      pols.forEach(([k, name, color]) => {
        const med = p.median[k];
        series.push({ type: 'bar', name, xAxisIndex: g, yAxisIndex: g, barWidth: 8, barGap: '25%',
          itemStyle: { color, borderRadius: [3, 3, 0, 0] }, data: p[k],
          markLine: { silent: true, symbol: 'none',
            lineStyle: { color, width: 1.2, type: [2, 3] },
            label: { position: 'end', color: C.ink2, fontSize: 11.5, fontFamily: FONT, formatter: `median ${med.toFixed(1)}%` },
            data: [{ yAxis: med }] } });
      });
    });
    return {
      title: D.panels.map((p, g) => panelTitle(p.name, grids[g].left - 4)),
      grid: grids,
      xAxis: grids.map((_, g) => axisCategory({ gridIndex: g, data: ['1', '2', '3', '4', '5', '6'], name: 'Fold', nameLocation: 'middle', nameGap: 28,
        nameTextStyle: { color: C.ink2, fontSize: 12.5, fontFamily: FONT }, axisLabel: { color: C.ink2, fontSize: 12.5, fontFamily: FONT } })),
      yAxis: grids.map((_, g) => axisValue({ gridIndex: g, min: 0, max: 50, interval: 10,
        name: g === 0 ? 'Maximum drawdown' : '', nameLocation: 'middle', nameGap: 38, nameRotate: 90,
        axisLabel: { show: g === 0, formatter: pct, color: C.ink2, fontSize: 12.5, fontFamily: FONT } })),
      legend: legend({ data: pols.map(p => p[1]) }),
      series,
    };
  },

  // ------------------------------------------------------------ Figure 4.5
  fig_4_5_flat_vs_trained(D) {
    const lose = D.cells.filter(c => c.diff < 0).map(c => [c.x, c.diff]);
    const win = D.cells.filter(c => c.diff >= 0).map(c => ({ value: [c.x, c.diff],
      label: { show: true, position: 'left', distance: 10, color: C.ink2, fontSize: 12, fontFamily: FONT, offset: [0, -20], formatter: 'the only cell where PPO beat doing nothing', align: 'right' } }));
    return {
      grid: { left: 66, right: 24, top: 34, bottom: 34 },
      title: { text: `Below the line, doing nothing scored higher under the agent's own reward (${D.n_lose} of ${D.cells.length} seed–fold cells).`,
        left: 62, top: 0, textStyle: { fontFamily: FONT, fontSize: 12.5, fontWeight: 'normal', color: C.ink2 } },
      xAxis: [
        axisValue({ min: -0.5, max: D.groups.length - 0.5, show: false, splitLine: { show: false } }),
        axisCategory({ data: D.groups, position: 'bottom', axisLabel: { color: C.ink, fontSize: 13, fontFamily: FONT } }),
      ],
      yAxis: axisValue({ min: -0.6, max: 0.1, interval: 0.1, name: 'Reward: trained − do-nothing', nameLocation: 'middle', nameGap: 46, nameRotate: 90,
        axisLabel: { color: C.ink2, fontSize: 12.5, fontFamily: FONT, formatter: (v) => minus(v.toFixed(1)) } }),
      series: [
        { type: 'scatter', symbolSize: 10, itemStyle: { color: C.ppo, borderColor: C.surface, borderWidth: 1.5 }, data: lose,
          markLine: { silent: true, symbol: 'none', lineStyle: { color: C.ink, width: 1.4, type: 'solid' },
            label: { position: 'insideStartTop', color: C.ink, fontSize: 12.5, fontFamily: FONT, formatter: 'do-nothing policy = 0' },
            data: [{ yAxis: 0 }] } },
        { type: 'scatter', symbolSize: 12, z: 5, itemStyle: { color: C.surface, borderColor: C.ppo, borderWidth: 2.6 }, data: win },
      ],
    };
  },

  // ------------------------------------------------------------ Figure 4.6
  fig_4_6_detectable_effect(D) {
    const names = D.rows.map(r => r.name);
    return {
      grid: { left: 56, right: 24, top: 14, bottom: 52 },
      xAxis: axisValue({ min: -200, max: 280, interval: 50, name: 'Cumulative return difference, PPO − Random Forest (percentage points)', nameLocation: 'middle', nameGap: 32,
        axisLabel: { color: C.ink2, fontSize: 12.5, fontFamily: FONT, formatter: (v) => (v > 0 ? '+' : '') + minus(v), showMaxLabel: false } }),
      yAxis: axisCategory({ data: names, inverse: true, axisLine: { show: false } }),
      series: [
        { type: 'custom', silent: true, z: 1,
          renderItem(params, api) {
            const m = api.value(0), y = api.value(1);
            const a = api.coord([-m, y]), b = api.coord([m, y]);
            const h = api.size([0, 1])[1] * 0.42;
            return { type: 'group', children: [
              { type: 'rect', shape: { x: a[0], y: a[1] - h / 2, width: b[0] - a[0], height: h, r: 3 }, style: { fill: '#ececec' } },
              { type: 'line', shape: { x1: a[0], y1: a[1] - h / 2, x2: a[0], y2: a[1] + h / 2 }, style: { stroke: '#b0b0b0', lineWidth: 1.2 } },
              { type: 'line', shape: { x1: b[0], y1: b[1] - h / 2, x2: b[0], y2: b[1] + h / 2 }, style: { stroke: '#b0b0b0', lineWidth: 1.2 } },
              { type: 'text', x: b[0] + 8, y: b[1], style: { text: `±${Math.round(m)} pp needed to detect`, fill: C.ink2, font: `12.5px ${FONT}`, verticalAlign: 'middle' } },
            ] };
          },
          data: D.rows.map(r => [r.mde, r.name]) },
        { type: 'scatter', symbolSize: 13, z: 3, itemStyle: { color: C.ppo, borderColor: C.surface, borderWidth: 2 },
          label: { show: true, position: 'top', distance: 9, backgroundColor: '#ffffff', padding: [1, 4], color: C.ink, fontSize: 12.5, fontFamily: FONT,
            formatter: (p) => `observed ${p.value[0] > 0 ? '+' : ''}${minus(p.value[0].toFixed(1))} pp` },
          data: D.rows.map(r => [r.observed, r.name]) },
      ],
    };
  },
};
