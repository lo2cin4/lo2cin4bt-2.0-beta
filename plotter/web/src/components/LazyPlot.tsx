import { lazy, Suspense } from 'react'

const PlotlyChart = lazy(() => import('react-plotly.js'))

export function preloadPlotly() {
  void import('react-plotly.js')
}

export function Plot(props: any) {
  return (
    <Suspense fallback={<div className="chart-loading" />}>
      <PlotlyChart {...props} />
    </Suspense>
  )
}
