// Shared look for every dissertation figure (Apache ECharts).
// Colours: one per policy, the same in every figure; validated colour-blind safe.
const C = {
  ink: '#191919',        // primary text
  ink2: '#666666',       // secondary text: ticks, axis names, notes
  grid: '#e7e7e7',       // hairline grid
  axis: '#c2c2c2',       // baseline
  surface: '#ffffff',
  ppo: '#257de0',        // trained PPO agent
  rf: '#f47230',         // supervised Random-Forest baseline
  un: '#00b894',         // untrained network
  hold: '#8f8f8f',       // buy-and-hold: neutral reference
  dep: '#257de0', exp: '#ee7fac', con: '#f2a600',   // exposure split
  trainFill: '#b6d5f8', embargo: '#cfcfcf',
};
const FONT = 'Calibri, "Segoe UI", system-ui, sans-serif';

const BASE = {
  animation: false,
  backgroundColor: C.surface,
  textStyle: { fontFamily: FONT, color: C.ink2, fontSize: 13 },
};

function axisValue(extra = {}) {
  return Object.assign({
    type: 'value',
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: { color: C.ink2, fontSize: 12.5, fontFamily: FONT, margin: 10 },
    splitLine: { lineStyle: { color: C.grid, width: 1 } },
    nameTextStyle: { color: C.ink2, fontSize: 12.5, fontFamily: FONT },
  }, extra);
}

function axisCategory(extra = {}) {
  return Object.assign({
    type: 'category',
    axisLine: { lineStyle: { color: C.axis } },
    axisTick: { show: false },
    axisLabel: { color: C.ink, fontSize: 13.5, fontFamily: FONT, margin: 12 },
    splitLine: { show: false },
  }, extra);
}

function panelTitle(text, left, top = 4) {
  return { text, left, top, textStyle: { fontFamily: FONT, fontSize: 15, fontWeight: 'bold', color: C.ink } };
}

function legend(extra = {}) {
  return Object.assign({
    bottom: 0, itemWidth: 14, itemHeight: 10, itemGap: 26,
    textStyle: { fontFamily: FONT, fontSize: 12.5, color: C.ink },
  }, extra);
}

const pct = (v) => `${v}%`;
const minus = (s) => String(s).replace(/-/g, '−');

function render(option) {
  const el = document.getElementById('chart');
  echarts.init(el, null, { renderer: 'svg' }).setOption(Object.assign({}, BASE, option));
}
