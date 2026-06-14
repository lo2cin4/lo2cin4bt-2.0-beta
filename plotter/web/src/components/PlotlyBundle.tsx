import PlotlyCore from 'plotly.js/lib/core'
import bar from 'plotly.js/lib/bar'
import candlestick from 'plotly.js/lib/candlestick'
import contour from 'plotly.js/lib/contour'
import heatmap from 'plotly.js/lib/heatmap'
import histogram from 'plotly.js/lib/histogram'
import pie from 'plotly.js/lib/pie'
import scatter from 'plotly.js/lib/scatter'
import createPlotlyComponent from 'react-plotly.js/factory'

PlotlyCore.register([scatter, bar, heatmap, contour, histogram, pie, candlestick])

export default createPlotlyComponent(PlotlyCore)
