import { lazy, Suspense } from 'react'

(globalThis as any).global ??= globalThis

const PlotlyChart = lazy(() => import('./PlotlyBundle'))

export function preloadPlotly() {
  void import('./PlotlyBundle')
}

export function Plot(props: any) {
  return (
    <Suspense fallback={<div className="chart-loading" />}>
      <PlotlyChart {...props} />
    </Suspense>
  )
}
