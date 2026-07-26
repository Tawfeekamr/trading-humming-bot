import { useEffect, useRef } from 'react'
import {
  createChart,
  CandlestickSeries,
  createSeriesMarkers,
  type IChartApi,
  type ISeriesApi,
  type SeriesMarker,
  type UTCTimestamp,
} from 'lightweight-charts'
import type { Candle } from '../lib/binance'

type Props = {
  candles: Candle[]
  markers?: SeriesMarker<UTCTimestamp>[]
  /** compact = card mini-chart (axes hidden); full = focus chart. */
  compact?: boolean
}

export function LwChart({ candles, markers, compact }: Props) {
  const ref = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  // createSeriesMarkers is generic over Time; an explicit any here avoids the
  // UTCTimestamp-vs-Time variance fight. Only .setMarkers is ever called.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const markersRef = useRef<any>(null)

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
      chartRef.current = null; seriesRef.current = null; markersRef.current = null
    }
    // Mount once — `compact`/`markers` are stable per usage; live updates flow
    // through the data effects below.
  }, [])

  useEffect(() => {
    if (seriesRef.current && candles.length) seriesRef.current.setData(candles)
  }, [candles])

  useEffect(() => {
    if (markersRef.current) markersRef.current.setMarkers(markers ?? [])
  }, [markers])

  return <div className={compact ? 'card-chart' : 'focus-chart'} ref={ref} />
}
