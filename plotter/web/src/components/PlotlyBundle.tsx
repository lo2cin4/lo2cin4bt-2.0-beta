import type { ComponentType } from 'react'
import * as ReactPlotly from 'react-plotly.js'

const Plot = (
  (ReactPlotly as any).default?.default ??
  (ReactPlotly as any).default ??
  ReactPlotly
) as ComponentType<any>

export default Plot
