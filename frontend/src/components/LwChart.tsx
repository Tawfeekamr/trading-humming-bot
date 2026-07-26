import { useEffect, useRef } from 'react'
import {
  createChart,
  CandlestickSeries,
  createSeriesMarkers,
  type IChartApi,
  type ISeriesApi,
  type IPriceLine,
  type SeriesMarker,
  type UTCTimestamp,
  type AutoscaleInfo,
} from 'lightweight-charts'
import type { Candle } from '../lib/binance'

export type PriceLine = { price: number; color: string; title: string; dashed?: boolean }

type Props = {
  candles: Candle[]
  markers?: SeriesMarker<UTCTimestamp>[]
  /** Horizontal price lines (focus mode: ENTRY / EXIT / SL / TP). */
  priceLines?: PriceLine[]
  /** compact = card mini-chart (axes hidden); full = focus chart. */
  compact?: boolean
}

export function LwChart({ candles, markers, priceLines, compact }: Props) {
  const ref = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const markersRef = useRef<any>(null)
  const linesRef = useRef<IPriceLine[]>([])
  const prevLenRef = useRef(0)
  const prevFirstRef = useRef<UTCTimestamp | null>(null)

  useEffect(() => {
    if (!ref.current) return
    const chart = createChart(ref.current, {
      layout: {
        background: { color: 'transparent' },
        textColor: '#7E7A72',
        fontFamily: "'IBM Plex Mono', monospace",
        fontSize: compact ? 9 : 11,
        attributionLogo: false,
      },
      grid: { vertLines: { color: 'rgba(236,233,226,.04)' }, horzLines: { color: 'rgba(236,233,226,.04)' } },
      rightPriceScale: { borderColor: 'rgba(236,233,226,.09)', visible: !compact },
      timeScale: { borderColor: 'rgba(236,233,226,.09)', timeVisible: true, secondsVisible: false, visible: !compact },
      crosshair: { mode: 0, vertLine: { labelVisible: !compact }, horzLine: { labelVisible: !compact } },
    })
    const series = chart.addSeries(CandlestickSeries, {
      upColor: '#4A9D8A', downColor: '#C2523F', borderVisible: false,
      wickUpColor: '#4A9D8A', wickDownColor: '#C2523F',
      priceFormat: { type: 'price', precision: 4, minMove: 0.0001 },
    })
    chartRef.current = chart
    seriesRef.current = series
    if (markers) markersRef.current = createSeriesMarkers(series, markers)
    const apply = () => {
      if (ref.current) chart.applyOptions({ width: ref.current.clientWidth, height: ref.current.clientHeight })
    }
    apply()
    const ro = new ResizeObserver(apply)
    ro.observe(ref.current)
    return () => {
      ro.disconnect(); chart.remove()
      chartRef.current = null; seriesRef.current = null; markersRef.current = null; linesRef.current = []
    }
  }, [])

  // Full setData only on a real re-seed (first load / pair switch / shrink);
  // otherwise update just the last bar. Dynamic priceFormat precision (4 decimals for <$1, 2 for >=$1).
  useEffect(() => {
    const s = seriesRef.current
    if (!s) return
    const first = candles.length ? candles[0].time : null
    const lastBar = candles.length ? candles[candles.length - 1] : null
    if (lastBar) {
      const precision = lastBar.close < 1 ? 4 : 2
      const minMove = lastBar.close < 1 ? 0.0001 : 0.01
      s.applyOptions({ priceFormat: { type: 'price', precision, minMove } })
    }
    const reseed = candles.length < prevLenRef.current || first !== prevFirstRef.current
    if (reseed) {
      s.setData(candles)
      chartRef.current?.timeScale().scrollToRealTime()
    } else if (candles.length) {
      s.update(candles[candles.length - 1])
    }
    prevLenRef.current = candles.length
    prevFirstRef.current = first
  }, [candles])

  useEffect(() => {
    if (markersRef.current) markersRef.current.setMarkers(markers ?? [])
  }, [markers])

  // (Re)draw the horizontal price lines whenever they change (focus mode)
  // and autoscale the price scale so ALL price lines are visible.
  useEffect(() => {
    const s = seriesRef.current
    if (!s) return
    for (const l of linesRef.current) s.removePriceLine(l)
    linesRef.current = []
    for (const pl of priceLines ?? []) {
      if (pl.price == null || Number.isNaN(pl.price)) continue
      linesRef.current.push(s.createPriceLine({
        price: pl.price,
        color: pl.color,
        title: pl.title,
        lineStyle: pl.dashed ? 2 : 0, // 0 = solid, 2 = dashed
        lineWidth: 1,
        axisLabelVisible: true,
      }))
    }

    if (priceLines && priceLines.length > 0) {
      s.applyOptions({
        autoscaleInfoProvider: (original: () => AutoscaleInfo | null) => {
          const res = original()
          let min = res?.priceRange?.minValue ?? Infinity
          let max = res?.priceRange?.maxValue ?? -Infinity
          for (const pl of priceLines) {
            if (pl.price != null && !Number.isNaN(pl.price)) {
              if (pl.price < min) min = pl.price
              if (pl.price > max) max = pl.price
            }
          }
          if (min === Infinity || max === -Infinity) return res
          // Add a small 2% padding above and below
          const pad = (max - min) * 0.02
          return { priceRange: { minValue: min - pad, maxValue: max + pad } } as AutoscaleInfo
        },
      })
    }
  }, [priceLines])

  return <div className={compact ? 'card-chart' : 'focus-chart'} ref={ref} />
}
