import { describe, expect, it } from 'vitest'

import { makeChartLayout, plotConfig } from './chartTheme'

describe('chartTheme', () => {
  it('keeps Plotly config lightweight and responsive', () => {
    expect(plotConfig).toEqual({ displaylogo: false, responsive: true })
  })

  it('merges chart layout defaults with caller options', () => {
    const layout = makeChartLayout({
      xTitle: 'Date',
      yTitle: 'Equity',
      margin: { l: 1, r: 2, t: 3, b: 4 },
      legend: { orientation: 'h' },
      xaxis: { type: 'date' },
      yaxis: { rangemode: 'tozero' },
      height: 360,
    })

    expect(layout.template).toBe('plotly_dark')
    expect(layout.margin).toEqual({ l: 1, r: 2, t: 3, b: 4 })
    expect((layout as { height?: number }).height).toBe(360)
    expect(layout.legend).toMatchObject({ orientation: 'h' })
    expect(layout.xaxis).toMatchObject({
      automargin: true,
      title: { text: 'Date', font: { color: '#e8b97d', size: 13 }, standoff: 10 },
      type: 'date',
    })
    expect(layout.yaxis).toMatchObject({
      automargin: true,
      title: { text: 'Equity', font: { color: '#e8b97d', size: 13 }, standoff: 10 },
      rangemode: 'tozero',
    })
  })

  it('omits axis titles when titles are not provided', () => {
    const layout = makeChartLayout()

    expect(layout.xaxis).toMatchObject({ title: undefined })
    expect(layout.yaxis).toMatchObject({ title: undefined })
  })
})
