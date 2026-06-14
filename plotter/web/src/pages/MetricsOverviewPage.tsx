import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useRouterState } from '../routing'
import { useQuery } from '@tanstack/react-query'

import { api } from '../api'
import { makeChartLayout, plotConfig } from '../chartTheme'
import { Language, useCopy } from '../i18n'
import { BenchmarkToggleButton } from '../components/BenchmarkToggleButton'
import { Plot, preloadPlotly } from '../components/LazyPlot'
import { MissingState } from '../components/MissingState'
import { SectionCard } from '../components/SectionCard'
import { localizeStrategyRuleValue } from '../components/StrategyRulesPanel'
import { useAppStore } from '../store'
import { benchmarkDisplayLabel, dataHealthLabel } from '../uiVocabulary'

const _CORE_KPIS = [
  ['CAGR', 'cagr'],
  ['Total Return', 'total_return'],
  ['Sharpe', 'sharpe'],
  ['Sortino', 'sortino'],
  ['Calmar', 'calmar'],
  ['MDD', 'max_drawdown'],
  ['Win Rate', 'win_rate'],
  ['Profit Factor', 'profit_factor'],
  ['Trade Count', 'trade_count'],
] as const

const ADVANCED_KPIS = [
  ['Alpha', 'alpha'],
  ['Beta', 'beta'],
  ['Annualized Std', 'annualized_std'],
  ['Average Drawdown', 'average_drawdown'],
  ['Recovery Factor', 'recovery_factor'],
  ['Information Ratio', 'information_ratio'],
  ['Exposure Time', 'exposure_time'],
  ['Avg Trade Return', 'avg_trade_return'],
  ['Max Consecutive Losses', 'max_consecutive_losses'],
  ['Max Holding Period Ratio', 'max_holding_period_ratio'],
  ['Excess Return vs BAH', 'excess_return'],
  ['BAH Total Return', 'bah_total_return'],
  ['BAH CAGR', 'bah_cagr'],
  ['BAH Sharpe', 'bah_sharpe'],
  ['BAH Calmar', 'bah_calmar'],
  ['BAH Max Drawdown', 'bah_max_drawdown'],
] as const

const CATEGORY_OBJECTIVES: Record<string, { label: string; key: string }> = {
  top_20_sharpe: { label: 'Sharpe', key: 'sharpe' },
  top_20_return: { label: 'Return', key: 'total_return' },
  top_20_cagr: { label: 'CAGR', key: 'cagr' },
  top_20_calmar: { label: 'Calmar', key: 'calmar' },
  top_20_sortino: { label: 'Sortino', key: 'sortino' },
  top_20_recovery_factor: { label: 'Recovery Factor', key: 'recovery_factor' },
  top_20_information_ratio: { label: 'Information Ratio', key: 'information_ratio' },
  top_20_profit_factor: { label: 'Profit Factor', key: 'profit_factor' },
  top_20_lowest_mdd: { label: 'MDD', key: 'max_drawdown' },
  top_20_excess_return: { label: 'Excess Return', key: 'excess_return' },
}

const sameStringArray = (left: string[], right: string[]) =>
  left.length === right.length && left.every((value, index) => value === right[index])

type EquityScale = 'linear' | 'log'

const PERCENT_METRIC_KEYS = new Set([
  'total_return',
  'cagr',
  'max_drawdown',
  'mdd',
  'average_drawdown',
  'annualized_std',
  'std',
  'exposure_time',
  'avg_trade_return',
  'win_rate',
  'bah_total_return',
  'bah_cagr',
  'bah_max_drawdown',
  'excess_return',
  'avg_gross_exposure',
  'avg_turnover',
  'trade_cost_drag',
])

const COUNT_METRIC_KEYS = new Set([
  'trade_count',
  'rebalance_count',
  'active_rebalance_count',
  'scheduled_rebalances',
  'trade_events',
])

const zhLabelMap: Record<string, string> = {
  '10 / page': '每頁 10 筆',
  '20 / page': '每頁 20 筆',
  '50 / page': '每頁 50 筆',
  '100 / page': '每頁 100 筆',
  'Top 20 Only': '只顯示前 20',
  'Load All': '載入全部',
  Backtest: '回測',
  Heatmap: '熱圖',
  'Current Selection': '目前選取',
  'Backtest Window': '回測區間',
  'Ranking Basis': '排名依據',
  'Trades / Win Rate': '交易次數 / 勝率',
  'Excess vs BAH': '相對買入持有超額報酬',
  'Primary KPI': '主要指標',
  'Strategy Rules': '策略規則',
  'Strategy Logic': '策略邏輯',
  'Run Health': '執行健康度',
  'Trade Quality': '交易品質',
  'Risk & Stability': '風險與穩定度',
  'Benchmark Context': '基準背景',
  'Show Advanced Raw Metrics': '顯示進階原始指標',
  Entry: '入場',
  Exit: '出場',
  'Search Domain': '搜尋範圍',
  Universe: '標的範圍',
  Mode: '模式',
  Trigger: '觸發條件',
  Selection: '選取條件',
  Execution: '執行方式',
  Benchmark: '基準',
  'Benchmark Return': '基準報酬',
  'Active Rebalances': '有效再平衡',
  Checkpoints: '檢查點',
  'Avg Holdings': '平均持倉數',
  'Avg Exposure': '平均曝險',
  'Avg Turnover': '平均換手率',
  'Data Health': '資料健康度',
  'Effective Start': '有效開始日期',
  'Loaded Assets': '已載入標的',
  'Trade Events': '交易事件',
  Sharpe: '夏普比率',
  Return: '報酬',
  'Total Return': '總報酬',
  CAGR: '年化報酬',
  Calmar: '卡瑪比率',
  MDD: '最大回撤',
  'Max Drawdown': '最大回撤',
  'Win Rate': '勝率',
  Trades: '交易次數',
  Exposure: '曝險',
  'Profit Factor': '獲利因子',
  Sortino: '索提諾比率',
  'Average Drawdown': '平均回撤',
  'Annualized Std': '年化波動率',
  'Exposure Time': '曝險時間',
  'Avg Trade Return': '平均交易報酬',
  'Recovery Factor': '恢復因子',
  'Excess Return': '超額報酬',
  'Excess Return vs BAH': '相對買入持有超額報酬',
  'BAH Total Return': '買入持有總報酬',
  'BAH CAGR': '買入持有年化報酬',
  'BAH Sharpe': '買入持有夏普比率',
  'BAH Calmar': '買入持有卡瑪比率',
  'BAH Max Drawdown': '買入持有最大回撤',
  'Information Ratio': '資訊比率',
  'Max Consecutive Losses': '最長連敗',
  'Max Holding Period Ratio': '最長持有期比例',
}

const labelText = (language: Language, label: string) => (language === 'zh-Hant' ? zhLabelMap[label] || label : label)
const pageSizeText = (language: Language, size: number) => (language === 'zh-Hant' ? `每頁 ${size} 筆` : `${size} / page`)
const pageText = (language: Language, page: number, totalPages: number) =>
  language === 'zh-Hant' ? `第 ${page} / ${totalPages} 頁` : `Page ${page} / ${totalPages}`
const thresholdText = (language: Language, label: string) => `${labelText(language, label)} ≥ `

function chartValue(value: unknown, scale: EquityScale): number | null {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return null
  if (scale === 'log' && numeric <= 0) return null
  return numeric
}

function formatFixedCell(value: any, fallback: string = '-') {
  if (typeof value === 'number') {
    if (Number.isNaN(value)) return fallback
    if (!Number.isFinite(value)) return value > 0 ? 'inf' : fallback
    return value.toFixed(3)
  }
  if (value === null || value === undefined || value === '') return fallback
  return String(value)
}

function formatPercentCell(value: any, fallback: string = '-', digits = 1) {
  const numeric = Number(value)
  return Number.isFinite(numeric) ? `${(numeric * 100).toFixed(digits)}%` : fallback
}

function formatCountCell(value: any, fallback: string = '-') {
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric.toLocaleString(undefined, { maximumFractionDigits: 0 }) : fallback
}

function formatMetricValue(key: string, value: any, fallback: string = '-') {
  if (PERCENT_METRIC_KEYS.has(key)) return formatPercentCell(value, fallback)
  if (COUNT_METRIC_KEYS.has(key)) return formatCountCell(value, fallback)
  return formatFixedCell(value, fallback)
}

export function MetricsOverviewPage() {
  const navigate = useNavigate()
  const search = useRouterState({ select: (state) => state.location.search }) as Record<string, string | undefined>
  const selectedMetricsRunId = useAppStore((state) => state.selectedMetricsRunId)
  const setSelectedMetricsRunId = useAppStore((state) => state.setSelectedMetricsRunId)
  const selectedCategory = useAppStore((state) => state.selectedCategory)
  const setSelectedCategory = useAppStore((state) => state.setSelectedCategory)
  const selectedBacktestId = useAppStore((state) => state.selectedBacktestId)
  const setSelectedBacktestId = useAppStore((state) => state.setSelectedBacktestId)
  const benchmarkVisible = useAppStore((state) => state.benchmarkVisible)
  const setBenchmarkVisible = useAppStore((state) => state.setBenchmarkVisible)
  const language = useAppStore((state) => state.language)
  const t = useCopy(language)
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const [minSharpe, setMinSharpe] = useState('')
  const [minReturn, setMinReturn] = useState('')
  const [minCalmar, setMinCalmar] = useState('')
  const [maxMdd, setMaxMdd] = useState('')
  const [minWinRate, setMinWinRate] = useState('')
  const [minTradeCount, setMinTradeCount] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)
  const [loadAll, setLoadAll] = useState(false)

  useEffect(() => {
    preloadPlotly()
  }, [])

  const runsQuery = useQuery({ queryKey: ['metrics-runs'], queryFn: api.metricsRuns, staleTime: 60000 })
  const availableRunIds = useMemo(
    () => (runsQuery.data || []).map((run: any) => run.run_id),
    [runsQuery.data],
  )
  const requestedRunId = search.runId || selectedMetricsRunId || ''
  const runId = availableRunIds.includes(String(requestedRunId))
    ? String(requestedRunId)
    : runsQuery.data?.[0]?.run_id || ''
  const hasResolvedRun = Boolean(runId && availableRunIds.includes(String(runId)))
  const overviewQuery = useQuery({
    queryKey: ['metrics-overview', runId],
    queryFn: () => api.metricsOverview(runId),
    enabled: hasResolvedRun,
    staleTime: 60000,
  })

  useEffect(() => {
    if (!runsQuery.data?.length) return
    if (!runId || !availableRunIds.includes(runId)) {
      const fallbackRunId = runsQuery.data[0].run_id
      setSelectedMetricsRunId(fallbackRunId)
      setSelectedBacktestId('')
      navigate({ to: '/metrics', search: { runId: fallbackRunId }, replace: true })
    }
  }, [availableRunIds, navigate, runId, runsQuery.data, setSelectedBacktestId, setSelectedMetricsRunId])

  useEffect(() => {
    if (runId && runId !== selectedMetricsRunId) {
      setSelectedMetricsRunId(runId)
    }
  }, [runId, selectedMetricsRunId, setSelectedMetricsRunId])

  useEffect(() => {
    if (!overviewQuery.error) {
      return
    }
    const fallbackRunId = runsQuery.data?.[0]?.run_id || ''
    if (fallbackRunId && fallbackRunId !== runId) {
      setSelectedMetricsRunId(fallbackRunId)
      setSelectedBacktestId('')
      navigate({ to: '/metrics', search: { runId: fallbackRunId }, replace: true })
    }
  }, [navigate, overviewQuery.error, runId, runsQuery.data, setSelectedBacktestId, setSelectedMetricsRunId])

  useEffect(() => {
    if (overviewQuery.data) {
      setPage((current) => (current === 1 ? current : 1))
      const nextIds = overviewQuery.data.categories?.[selectedCategory] || []
      setSelectedIds((current) => (sameStringArray(current, nextIds) ? current : nextIds))
    }
  }, [overviewQuery.data, selectedCategory])

  useEffect(() => {
    setLoadAll((current) => (current ? false : current))
    setPage((current) => (current === 1 ? current : 1))
  }, [selectedCategory, runId])

  const rows = overviewQuery.data?.rows || []
  const filteredRows = useMemo(() => {
    const categoryIds = new Set(overviewQuery.data?.categories?.[selectedCategory] || [])
    const objective = CATEGORY_OBJECTIVES[selectedCategory] || CATEGORY_OBJECTIVES.top_20_sharpe
    return rows
      .filter((row: any) => loadAll || categoryIds.size === 0 || categoryIds.has(row.backtest_id))
      .filter((row: any) => (minSharpe ? (row.sharpe ?? Number.NEGATIVE_INFINITY) >= Number(minSharpe) : true))
      .filter((row: any) => (minReturn ? (row.total_return ?? Number.NEGATIVE_INFINITY) >= Number(minReturn) : true))
      .filter((row: any) => (minCalmar ? (row.calmar ?? Number.NEGATIVE_INFINITY) >= Number(minCalmar) : true))
      .filter((row: any) => (maxMdd ? (row.max_drawdown ?? Number.POSITIVE_INFINITY) >= Number(maxMdd) : true))
      .filter((row: any) => (minWinRate ? (row.win_rate ?? Number.NEGATIVE_INFINITY) >= Number(minWinRate) : true))
      .filter((row: any) => (minTradeCount ? (row.trade_count ?? Number.NEGATIVE_INFINITY) >= Number(minTradeCount) : true))
      .sort((left: any, right: any) => {
        const lv = typeof left[objective.key] === 'number' ? left[objective.key] : Number.NEGATIVE_INFINITY
        const rv = typeof right[objective.key] === 'number' ? right[objective.key] : Number.NEGATIVE_INFINITY
        if (rv !== lv) return rv - lv
        return String(left.label).localeCompare(String(right.label))
      })
  }, [overviewQuery.data, rows, selectedCategory, minSharpe, minReturn, minCalmar, maxMdd, minWinRate, minTradeCount, loadAll])

  useEffect(() => {
    if (!filteredRows.length) return
    if (!selectedBacktestId || !filteredRows.some((row: any) => row.backtest_id === selectedBacktestId)) {
      setSelectedBacktestId(filteredRows[0].backtest_id)
    }
  }, [filteredRows, selectedBacktestId, setSelectedBacktestId])

  const selectedRow = filteredRows.find((row: any) => row.backtest_id === selectedBacktestId) || filteredRows[0]
  const objective = CATEGORY_OBJECTIVES[selectedCategory] || CATEGORY_OBJECTIVES.top_20_sharpe
  const totalPages = Math.max(1, Math.ceil(filteredRows.length / pageSize))
  const pageRows = filteredRows.slice((page - 1) * pageSize, page * pageSize)

  useEffect(() => {
    if (page > totalPages) setPage((current) => (current === totalPages ? current : totalPages))
  }, [page, totalPages])

  const plotData = useMemo(() => {
    const selectedSet = new Set(selectedIds)
    const series = (overviewQuery.data?.series || [])
      .filter((item: any) => selectedSet.has(item.backtest_id))
      .map((item: any) => ({
        x: item.x,
        y: item.y,
        type: 'scatter',
        mode: 'lines',
        name: item.label,
        customdata: item.backtest_id,
      }))
    if (benchmarkVisible && overviewQuery.data?.benchmark_series) {
      series.push({
        x: overviewQuery.data.benchmark_series.x,
        y: overviewQuery.data.benchmark_series.y,
        type: 'scatter',
        mode: 'lines',
        name: overviewQuery.data.benchmark_series.label,
        line: { dash: 'dash', color: '#7e9bcc' },
      })
    }
    return series
  }, [benchmarkVisible, overviewQuery.data, selectedIds])

  const formatCell = formatFixedCell

  if (runsQuery.isLoading || (!hasResolvedRun && runsQuery.data?.length) || overviewQuery.isLoading) return <div className="page-loading">{t('common.loading.metricsOverview')}</div>
  if (!runId) return <MissingState message={language === 'zh-Hant' ? '目前尚未有可用的指標回測結果。' : 'No metrics backtest results are available yet.'} />
  if (overviewQuery.error || !overviewQuery.data) return <div className="page-error">{language === 'zh-Hant' ? '無法載入本次回測的指標總覽。' : 'Unable to load the metrics overview for this run.'}</div>
  if (overviewQuery.data.result_type === 'portfolio') {
    return <PortfolioMetricsOverview payload={overviewQuery.data} formatCell={formatCell} formatMetricValue={formatMetricValue} />
  }

  return (
    <div className="page-stack">
      <SectionCard
        title={t('metricsOverview.title')}
        subtitle={t('metricsOverview.subtitle')}
      >
        <div className="control-row control-row-wrap">
          <select className="text-input" value={selectedCategory} onChange={(event) => setSelectedCategory(event.target.value)}>
            {(overviewQuery.data.available_categories || []).map((category: any) => (
              <option key={category.id} value={category.id}>{category.label}</option>
            ))}
          </select>
          <BenchmarkToggleButton
            visible={benchmarkVisible}
            language={language}
            onChange={setBenchmarkVisible}
          />
        </div>
        <Plot
          data={plotData}
          layout={makeChartLayout({
            xTitle: 'Date',
            yTitle: 'Normalized Equity',
            legend: { orientation: 'h' },
          })}
          config={plotConfig}
          className="plot-card"
          useResizeHandler
          style={{ width: '100%', height: '420px' }}
          onClick={(event: any) => {
            const backtestId = event?.points?.[0]?.data?.customdata
            if (backtestId) setSelectedBacktestId(String(backtestId))
          }}
        />
      </SectionCard>

      <SectionCard
        title={t('metricsOverview.strategyTable')}
        subtitle={t('metricsOverview.strategyTableSubtitle')}
        actions={
          <div className="inline-actions">
            <select className="text-input text-input-compact" value={String(pageSize)} onChange={(event) => { setPageSize(Number(event.target.value)); setPage(1) }}>
              {[10, 20, 50, 100].map((size) => <option key={size} value={size}>{pageSizeText(language, size)}</option>)}
            </select>
            <button className="ghost-button" onClick={() => { setLoadAll((current) => !current); setPage(1) }}>
              {labelText(language, loadAll ? 'Top 20 Only' : 'Load All')}
            </button>
            <button className="ghost-button" onClick={() => setSelectedIds(filteredRows.map((row: any) => row.backtest_id))}>{t('common.selectAll')}</button>
            <button className="ghost-button" onClick={() => setSelectedIds([])}>{t('common.clearAll')}</button>
          </div>
        }
      >
        <div className="filter-grid">
          <input className="text-input" placeholder={thresholdText(language, 'Sharpe')} value={minSharpe} onChange={(event) => { setMinSharpe(event.target.value); setPage(1) }} />
          <input className="text-input" placeholder={thresholdText(language, 'Return')} value={minReturn} onChange={(event) => { setMinReturn(event.target.value); setPage(1) }} />
          <input className="text-input" placeholder={thresholdText(language, 'Calmar')} value={minCalmar} onChange={(event) => { setMinCalmar(event.target.value); setPage(1) }} />
          <input className="text-input" placeholder={thresholdText(language, 'MDD')} value={maxMdd} onChange={(event) => { setMaxMdd(event.target.value); setPage(1) }} />
          <input className="text-input" placeholder={thresholdText(language, 'Win Rate')} value={minWinRate} onChange={(event) => { setMinWinRate(event.target.value); setPage(1) }} />
          <input className="text-input" placeholder={thresholdText(language, 'Trades')} value={minTradeCount} onChange={(event) => { setMinTradeCount(event.target.value); setPage(1) }} />
        </div>
        <div className="data-table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>{t('common.show')}</th>
                <th>{t('common.select')}</th>
                <th>{t('common.rank')}</th>
                <th>{t('common.label')}</th>
                <th>{labelText(language, objective.label)}</th>
                <th>{t('metricsTable.trades')}</th>
                <th>{t('metricsTable.exposure')}</th>
                <th>{t('metricsTable.profitFactor')}</th>
                <th>{t('metricsTable.lastTrade')}</th>
                <th>{t('common.dateRange')}</th>
                <th className="action-column-header">{t('common.actions')}</th>
              </tr>
            </thead>
            <tbody>
              {pageRows.map((row: any, index: number) => (
                <tr key={row.backtest_id} className={row.backtest_id === selectedBacktestId ? 'row-selected' : ''} onClick={() => setSelectedBacktestId(row.backtest_id)}>
                  <td onClick={(event) => event.stopPropagation()}>
                    <input
                      type="checkbox"
                      checked={selectedIds.includes(row.backtest_id)}
                      onChange={(event) =>
                        setSelectedIds((current) =>
                          event.target.checked ? [...new Set([...current, row.backtest_id])] : current.filter((value) => value !== row.backtest_id),
                        )
                      }
                    />
                  </td>
                  <td onClick={(event) => event.stopPropagation()}>
                    <input
                      type="radio"
                      name="selected-strategy"
                      checked={row.backtest_id === selectedBacktestId}
                      onChange={() => setSelectedBacktestId(row.backtest_id)}
                    />
                  </td>
                  <td>{(page - 1) * pageSize + index + 1}</td>
                  <td>{row.label}</td>
                  <td>{formatMetricValue(objective.key, row[objective.key])}</td>
                  <td>{row.trade_count}</td>
                  <td>{formatMetricValue('exposure_time', row.exposure_time)}</td>
                  <td>{formatCell(row.profit_factor)}</td>
                  <td>{row.last_trade_time ? String(row.last_trade_time).slice(0, 10) : '-'}</td>
                  <td>
                    {row.date_range_start && row.date_range_end
                      ? `${row.date_range_start.slice(0, 10)} - ${row.date_range_end.slice(0, 10)}`
                      : '-'}
                  </td>
                  <td className="action-column-cell" onClick={(event) => event.stopPropagation()}>
                  <div className="row-actions row-actions-compact">
                    <button className="inline-action-button inline-action-button-compact" onClick={() => navigate({ to: '/metrics/backtests', search: { runId, backtestId: row.backtest_id } })}>{labelText(language, 'Backtest')}</button>
                    <button className="inline-action-button inline-action-button-compact" onClick={() => navigate({ to: '/metrics/parameter-matrix', search: { runId, backtestId: row.backtest_id } })}>{labelText(language, 'Heatmap')}</button>
                    <button className="inline-action-button inline-action-button-compact" onClick={() => window.open(api.backtestCsvUrl(runId, row.backtest_id), '_blank', 'noopener,noreferrer')}>CSV</button>
                  </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="pagination-row">
          <button className="ghost-button" disabled={page <= 1} onClick={() => setPage((current) => Math.max(1, current - 1))}>{t('common.previous')}</button>
          <span className="muted">{pageText(language, page, totalPages)}</span>
          <button className="ghost-button" disabled={page >= totalPages} onClick={() => setPage((current) => Math.min(totalPages, current + 1))}>{t('common.next')}</button>
        </div>
      </SectionCard>

      {selectedRow ? (() => {
        const strategySummary = overviewQuery.data?.strategy_summary || {}
        const summaryPrimary = [
          ['Total Return', 'total_return'],
          ['CAGR', 'cagr'],
          ['Sharpe', 'sharpe'],
          ['Max Drawdown', 'max_drawdown'],
        ] as const
        const tradeQuality = [
          ['Win Rate', 'win_rate'],
          ['Profit Factor', 'profit_factor'],
          ['Trade Count', 'trade_count'],
          ['Avg Trade Return', 'avg_trade_return'],
          ['Recovery Factor', 'recovery_factor'],
        ] as const
        const riskAndStability = [
          ['Sortino', 'sortino'],
          ['Calmar', 'calmar'],
          ['Average Drawdown', 'average_drawdown'],
          ['Annualized Std', 'annualized_std'],
          ['Exposure Time', 'exposure_time'],
        ] as const
        const benchmarkContext = [
          ['Excess Return vs BAH', 'excess_return'],
          ['BAH Total Return', 'bah_total_return'],
          ['BAH CAGR', 'bah_cagr'],
          ['BAH Sharpe', 'bah_sharpe'],
          ['BAH Calmar', 'bah_calmar'],
          ['BAH Max Drawdown', 'bah_max_drawdown'],
        ] as const
        const selectionWindow = selectedRow.date_range_start && selectedRow.date_range_end
          ? `${String(selectedRow.date_range_start).slice(0, 10)} -> ${String(selectedRow.date_range_end).slice(0, 10)}`
          : language === 'zh-Hant' ? '日期範圍未提供' : 'Date range unavailable'

        return (
          <SectionCard title={t('metricsOverview.selectedStrategySummary')} subtitle={`${t('metricsOverview.currentSelection')}: ${selectedRow.label}`} subtitlePrivate>
            <div className="snapshot-hero-shell">
              <div className="snapshot-identity-card">
                <div className="snapshot-eyebrow">{labelText(language, 'Current Selection')}</div>
                <div className="snapshot-strategy-title" data-private-strategy="identity">{selectedRow.label}</div>
                <div className="snapshot-strategy-subtitle" data-private-strategy="identity">
                  {language === 'zh-Hant'
                    ? '先閱讀目前策略的核心表現，再查看交易品質、風險與基準比較等分組資訊。'
                    : 'Headline read of the active strategy before drilling into charts. Start with return, growth, sharpe, and drawdown; then use the grouped panels for trade quality, risk, and benchmark context.'}
                </div>
                <div className="snapshot-meta-grid">
                  <div className="snapshot-meta-pill">
                    <span className="snapshot-meta-label">{labelText(language, 'Backtest Window')}</span>
                    <span className="snapshot-meta-value">{selectionWindow}</span>
                  </div>
                  <div className="snapshot-meta-pill">
                    <span className="snapshot-meta-label">{labelText(language, 'Ranking Basis')}</span>
                    <span className="snapshot-meta-value">{labelText(language, objective.label)}</span>
                  </div>
                  <div className="snapshot-meta-pill">
                    <span className="snapshot-meta-label">{labelText(language, 'Trades / Win Rate')}</span>
                    <span className="snapshot-meta-value">{formatMetricValue('trade_count', selectedRow.trade_count)} / {formatMetricValue('win_rate', selectedRow.win_rate)}</span>
                  </div>
                  <div className="snapshot-meta-pill">
                    <span className="snapshot-meta-label">{labelText(language, 'Excess vs BAH')}</span>
                    <span className="snapshot-meta-value">{formatMetricValue('excess_return', selectedRow.excess_return)}</span>
                  </div>
                </div>
              </div>

              <div className="snapshot-primary-grid">
                {summaryPrimary.map(([label, key], index) => (
                  <div key={label} className="snapshot-primary-card">
                    <div className="snapshot-primary-accent">{labelText(language, 'Primary KPI')} {index + 1}</div>
                    <div className="snapshot-primary-label">{labelText(language, label)}</div>
                    <div className="snapshot-primary-value">{formatMetricValue(key, selectedRow[key])}</div>
                    <div className="snapshot-primary-meta">
                      {label === 'Total Return' && `${labelText(language, 'Profit Factor')} ${formatCell(selectedRow.profit_factor)}`}
                      {label === 'CAGR' && `${labelText(language, 'BAH CAGR')} ${formatMetricValue('bah_cagr', selectedRow.bah_cagr)}`}
                      {label === 'Sharpe' && `${labelText(language, 'Sortino')} ${formatCell(selectedRow.sortino)}`}
                      {label === 'Max Drawdown' && `${labelText(language, 'Average Drawdown')} ${formatMetricValue('average_drawdown', selectedRow.average_drawdown)}`}
                    </div>
                    <div className="snapshot-primary-delta">
                      {label === 'Total Return' && `${labelText(language, 'Trades')} ${formatMetricValue('trade_count', selectedRow.trade_count)}`}
                      {label === 'CAGR' && `${labelText(language, 'Exposure')} ${formatMetricValue('exposure_time', selectedRow.exposure_time)}`}
                      {label === 'Sharpe' && `${labelText(language, 'Win Rate')} ${formatMetricValue('win_rate', selectedRow.win_rate)}`}
                      {label === 'Max Drawdown' && `${labelText(language, 'Calmar')} ${formatCell(selectedRow.calmar)}`}
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div className="snapshot-context-grid">
              <div className="kpi-panel" data-private-strategy="rules">
                <div className="kpi-panel-title">{labelText(language, 'Strategy Rules')}</div>
                <div className="kpi-compact-list">
                  <div className="kpi-compact-row">
                    <span className="kpi-compact-label">{labelText(language, 'Entry')}</span>
                    <span className="kpi-compact-value">{strategySummary.entry_rule || '-'}</span>
                  </div>
                  <div className="kpi-compact-row">
                    <span className="kpi-compact-label">{labelText(language, 'Exit')}</span>
                    <span className="kpi-compact-value">{strategySummary.exit_rule || '-'}</span>
                  </div>
                  <div className="kpi-compact-row">
                    <span className="kpi-compact-label">{labelText(language, 'Search Domain')}</span>
                    <span className="kpi-compact-value">{strategySummary.parameter_domain_label || '-'}</span>
                  </div>
                </div>
              </div>
              <div className="kpi-panel">
                <div className="kpi-panel-title">{labelText(language, 'Trade Quality')}</div>
                <div className="kpi-compact-list">
                  {tradeQuality.map(([label, key]) => (
                    <div key={label} className="kpi-compact-row">
                      <span className="kpi-compact-label">{labelText(language, label)}</span>
                      <span className="kpi-compact-value">{formatMetricValue(key, selectedRow[key])}</span>
                    </div>
                  ))}
                </div>
              </div>

              <div className="kpi-panel">
                <div className="kpi-panel-title">{labelText(language, 'Risk & Stability')}</div>
                <div className="kpi-compact-list">
                  {riskAndStability.map(([label, key]) => (
                    <div key={label} className="kpi-compact-row">
                      <span className="kpi-compact-label">{labelText(language, label)}</span>
                      <span className="kpi-compact-value">{formatMetricValue(key, selectedRow[key])}</span>
                    </div>
                  ))}
                </div>
              </div>

              <div className="kpi-panel">
                <div className="kpi-panel-title">{labelText(language, 'Benchmark Context')}</div>
                <div className="kpi-compact-list">
                  {benchmarkContext.map(([label, key]) => (
                    <div key={label} className="kpi-compact-row">
                      <span className="kpi-compact-label">{labelText(language, label)}</span>
                      <span className="kpi-compact-value">{formatMetricValue(key, selectedRow[key])}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            <details className="advanced-metrics">
              <summary>
                <span className="advanced-metrics-chevron" aria-hidden="true">▾</span>
                <span>{labelText(language, 'Show Advanced Raw Metrics')}</span>
              </summary>
              <div className="metric-grid">
                {ADVANCED_KPIS.map(([label, key]) => (
                  <div key={label} className="metric-card compact">
                    <div className="metric-label">{labelText(language, label)}</div>
                    <div className="metric-value">{formatMetricValue(key, selectedRow[key])}</div>
                  </div>
                ))}
              </div>
            </details>
          </SectionCard>
        )
      })() : null}
    </div>
  )
}

function PortfolioMetricsOverview({
  payload,
  formatCell,
  formatMetricValue,
}: {
  payload: any
  formatCell: (value: any, fallback?: string) => string
  formatMetricValue: (key: string, value: any, fallback?: string) => string
}) {
  const navigate = useNavigate()
  const benchmarkVisible = useAppStore((state) => state.benchmarkVisible)
  const setBenchmarkVisible = useAppStore((state) => state.setBenchmarkVisible)
  const language = useAppStore((state) => state.language)
  const t = useCopy(language)
  const rows = payload?.rows || []
  const portfolioRuns = payload?.portfolio?.runs || []
  const runId = payload?.run_id || ''
  const [selectedPortfolioId, setSelectedPortfolioId] = useState<string>(rows[0]?.backtest_id || '')
  const [shownPortfolioIds, setShownPortfolioIds] = useState<string[]>(rows.slice(0, Math.min(3, rows.length)).map((row: any) => row.backtest_id))
  const [equityScale, setEquityScale] = useState<EquityScale>('linear')
  const [portfolioLoadAll, setPortfolioLoadAll] = useState(false)
  const [portfolioPage, setPortfolioPage] = useState(1)
  const [portfolioPageSize, setPortfolioPageSize] = useState(10)
  const [minPortfolioSharpe, setMinPortfolioSharpe] = useState('')
  const [minPortfolioReturn, setMinPortfolioReturn] = useState('')
  const [minPortfolioCagr, setMinPortfolioCagr] = useState('')
  const [minPortfolioMdd, setMinPortfolioMdd] = useState('')
  const [minPortfolioRebalances, setMinPortfolioRebalances] = useState('')
  const [minPortfolioHoldings, setMinPortfolioHoldings] = useState('')
  const selectedRun = portfolioRuns.find((item: any) => item?.summary?.backtest_id === selectedPortfolioId) || portfolioRuns[0]
  const summary = selectedRun?.summary || payload?.portfolio?.summary || rows[0] || {}
  const strategySummary = payload?.strategy_summary || {}
  const selectedSeries = (payload?.series || []).find((item: any) => item.backtest_id === summary.backtest_id) || payload?.series?.[0] || { x: [], y: [], label: 'Portfolio' }
  const shownSeries = (payload?.series || []).filter((item: any) => shownPortfolioIds.includes(item.backtest_id))
  const plotSeries = shownSeries.length ? shownSeries : [selectedSeries].filter(Boolean)
  const selectedBenchmarkSeries = Array.isArray(selectedRun?.benchmark_series) ? selectedRun.benchmark_series : []
  const payloadBenchmarkSeries = Array.isArray(payload?.benchmark_series) ? payload.benchmark_series : []
  const benchmarkSeries = selectedBenchmarkSeries.length ? selectedBenchmarkSeries : payloadBenchmarkSeries
  const benchmarkLabel = benchmarkDisplayLabel(selectedRun?.benchmark_label || summary.benchmark_label || strategySummary.benchmark_label)
  const dataQuality = selectedRun?.data_quality || payload?.portfolio?.data_quality || {}
  const dataHealthStatus = dataQuality.status || (dataQuality.legacy_missing_validation ? 'legacy_missing_validation' : '-')
  const dataHealthStatusText = dataHealthLabel(dataHealthStatus, language)
  const hasLegacyValidationWarning = Boolean(dataQuality.legacy_missing_validation || dataQuality.validation_available === false)
  const turnoverSummary = selectedRun?.turnover_summary || {}
  const portfolioExposure = (row: any) => {
    const explicit = Number(row?.avg_gross_exposure)
    if (Number.isFinite(explicit)) return explicit
    const cash = Number(row?.avg_cash_weight)
    return Number.isFinite(cash) ? 1 - cash : null
  }
  const formatPortfolioPercent = (value: any) => {
    const numeric = Number(value)
    return Number.isFinite(numeric) ? `${(numeric * 100).toFixed(1)}%` : '-'
  }
  const portfolioMetricDisplay = (row: any, key: string) => {
    if (key === 'avg_gross_exposure') return formatPortfolioPercent(portfolioExposure(row))
    if (key === 'avg_turnover') return formatPortfolioPercent(row.avg_turnover)
    return formatMetricValue(key, row[key])
  }
  const passesPortfolioMin = (value: any, threshold: string) => {
    if (!threshold.trim()) return true
    const numericValue = Number(value)
    const numericThreshold = Number(threshold)
    if (!Number.isFinite(numericValue) || !Number.isFinite(numericThreshold)) return false
    return numericValue >= numericThreshold
  }
  const filteredPortfolioRows = useMemo(() => rows.filter((row: any) =>
    passesPortfolioMin(row.sharpe, minPortfolioSharpe) &&
    passesPortfolioMin(row.total_return, minPortfolioReturn) &&
    passesPortfolioMin(row.cagr, minPortfolioCagr) &&
    passesPortfolioMin(row.max_drawdown, minPortfolioMdd) &&
    passesPortfolioMin(row.rebalance_count, minPortfolioRebalances) &&
    passesPortfolioMin(row.avg_holdings, minPortfolioHoldings),
  ), [rows, minPortfolioSharpe, minPortfolioReturn, minPortfolioCagr, minPortfolioMdd, minPortfolioRebalances, minPortfolioHoldings])
  const visiblePortfolioRows = portfolioLoadAll ? filteredPortfolioRows : filteredPortfolioRows.slice(0, 20)
  const portfolioTotalPages = Math.max(1, Math.ceil(visiblePortfolioRows.length / portfolioPageSize))
  const portfolioPageRows = visiblePortfolioRows.slice((portfolioPage - 1) * portfolioPageSize, portfolioPage * portfolioPageSize)
  const linePalette = ['#e1b12c', '#7e9bcc', '#62d5a8', '#ff9f80', '#b987ff', '#58c7f3', '#f3d66b', '#f08fb6']
  const portfolioPlotData: any[] = [
    ...plotSeries.map((item: any, index: number) => ({
      x: item.x || [],
      y: (item.y || []).map((value: unknown) => chartValue(value, equityScale)),
      type: 'scatter',
      mode: 'lines',
      name: item.label || (language === 'zh-Hant' ? `投資組合 ${index + 1}` : `Portfolio ${index + 1}`),
      line: { color: linePalette[index % linePalette.length] },
    })),
    ...(benchmarkVisible && benchmarkSeries.length ? [{
      x: benchmarkSeries.map((item: any) => item.time),
      y: benchmarkSeries.map((item: any) => chartValue(item.value, equityScale)),
      type: 'scatter',
      mode: 'lines',
      name: benchmarkLabel,
      line: { color: '#dfe6f5', dash: 'dash' },
    }] : []),
  ]
  const headlineKpis = [
    ['Total Return', 'total_return'],
    ['CAGR', 'cagr'],
    ['Sharpe', 'sharpe'],
    ['Max Drawdown', 'max_drawdown'],
    ['Calmar', 'calmar'],
    ['Excess Return', 'excess_return'],
  ] as const
  const headlineToneClass = (key: string, value: any) => {
    if (key === 'max_drawdown') return 'tone-negative'
    const numeric = Number(value)
    if (!Number.isFinite(numeric)) return 'tone-neutral'
    if (numeric > 0) return 'tone-positive'
    if (numeric < 0) return 'tone-negative'
    return 'tone-neutral'
  }
  const diagnosticChips = [
    ['Benchmark', benchmarkLabel || '-'],
    ['Benchmark Return', portfolioMetricDisplay(summary, 'bah_total_return')],
    ['Active Rebalances', portfolioMetricDisplay(summary, 'trade_count')],
    ['Checkpoints', portfolioMetricDisplay(summary, 'rebalance_count')],
    ['Avg Holdings', portfolioMetricDisplay(summary, 'avg_holdings')],
    ['Avg Exposure', portfolioMetricDisplay(summary, 'avg_gross_exposure')],
    ['Avg Turnover', portfolioMetricDisplay(summary, 'avg_turnover')],
  ] as const
  const strategyValue = (key: string, fallback = '-') =>
    localizeStrategyRuleValue(key, String(strategySummary?.[key] || fallback), language)
  const strategyFlow = [
    ['Universe', strategyValue('asset_label')],
    ['Mode', strategyValue('mode_label')],
    ['Trigger', strategyValue('entry_rule')],
    ['Selection', strategyValue('parameter_domain_label')],
    ['Execution', strategySummary.execution_label ? strategyValue('execution_label') : strategyValue('workflow_label')],
  ] as const
  const healthRows = [
    ['Data Health', dataHealthStatusText],
    ['Effective Start', dataQuality.effective_start_date || '-'],
    [
      'Loaded Assets',
      Array.isArray(dataQuality.loaded_symbols) && dataQuality.loaded_symbols.length
        ? dataQuality.loaded_symbols.join(', ')
        : hasLegacyValidationWarning ? (language === 'zh-Hant' ? '未記錄' : 'not recorded') : '-',
    ],
    ['Trade Events', formatMetricValue('trade_events', turnoverSummary.trade_events)],
  ] as const
  const strategyNarrative = [
    strategyValue('asset_label', language === 'zh-Hant' ? '投資組合標的範圍' : 'Portfolio universe'),
    strategyValue('mode_label', language === 'zh-Hant' ? '已設定策略' : 'Configured strategy'),
    strategyValue('parameter_domain_label', language === 'zh-Hant' ? '固定策略' : 'Fixed strategy'),
    strategySummary.execution_label
      ? strategyValue('execution_label', language === 'zh-Hant' ? '已設定執行方式' : 'Configured execution')
      : strategyValue('workflow_label', language === 'zh-Hant' ? '已設定執行方式' : 'Configured execution'),
  ].filter(Boolean).join(' -> ')

  useEffect(() => {
    if (!rows.length) return
    setSelectedPortfolioId((current) => rows.some((row: any) => row.backtest_id === current) ? current : rows[0].backtest_id)
    setShownPortfolioIds((current) => {
      const valid = current.filter((id) => rows.some((row: any) => row.backtest_id === id))
      return valid.length ? valid : rows.slice(0, Math.min(3, rows.length)).map((row: any) => row.backtest_id)
    })
  }, [payload?.run_id])

  useEffect(() => {
    setPortfolioPage((current) => Math.min(current, portfolioTotalPages))
  }, [portfolioTotalPages])

  return (
    <div className="page-stack">
      <SectionCard
        title={t('metricsOverview.portfolioEquity')}
        subtitle={t('metricsOverview.portfolioEquitySubtitle')}
        actions={
          <div className="inline-actions">
            <BenchmarkToggleButton
              visible={benchmarkVisible}
              language={language}
              onChange={setBenchmarkVisible}
            />
            <select
              className="text-input text-input-compact"
              value={equityScale}
              onChange={(event) => setEquityScale(event.target.value as EquityScale)}
              aria-label={language === 'zh-Hant' ? '資金曲線尺度' : 'Equity scale'}
            >
              <option value="linear">{language === 'zh-Hant' ? '線性尺度' : 'Linear Scale'}</option>
              <option value="log">{language === 'zh-Hant' ? '對數尺度' : 'Log Scale'}</option>
            </select>
          </div>
        }
      >
        {hasLegacyValidationWarning ? (
          <div className="page-warning portfolio-warning">
            {language === 'zh-Hant'
              ? '舊版投資組合回測：配置與貢獻資料仍可查看，但此結果未包含新版執行驗證報告。'
              : 'Legacy portfolio backtest: allocation and contribution data remain available, but this result does not include the newer execution validation report.'}
          </div>
        ) : null}
        <Plot
          data={portfolioPlotData}
          layout={makeChartLayout({
            xTitle: language === 'zh-Hant' ? '日期' : 'Date',
            yTitle: equityScale === 'log'
              ? language === 'zh-Hant' ? '資金曲線（對數）' : 'Equity Curve (Log)'
              : language === 'zh-Hant' ? '資金曲線' : 'Equity Curve',
            legend: { orientation: 'h' },
            yaxis: { type: equityScale },
          })}
          config={plotConfig}
          className="plot-card"
          useResizeHandler
          style={{ width: '100%', height: '420px' }}
        />
      </SectionCard>

      <SectionCard
        title={t('metricsOverview.strategyTable')}
        subtitle={t('metricsOverview.portfolioStrategyTableSubtitle')}
        actions={
          <div className="inline-actions">
            <select className="text-input text-input-compact" value={String(portfolioPageSize)} onChange={(event) => { setPortfolioPageSize(Number(event.target.value)); setPortfolioPage(1) }}>
              {[10, 20, 50, 100].map((size) => <option key={size} value={size}>{pageSizeText(language, size)}</option>)}
            </select>
            <button className="ghost-button" onClick={() => { setPortfolioLoadAll((current) => !current); setPortfolioPage(1) }}>
              {labelText(language, portfolioLoadAll ? 'Top 20 Only' : 'Load All')}
            </button>
            <button className="ghost-button" onClick={() => setShownPortfolioIds(filteredPortfolioRows.map((row: any) => row.backtest_id))}>{t('common.selectAll')}</button>
            <button className="ghost-button" onClick={() => setShownPortfolioIds([])}>{t('common.clearAll')}</button>
          </div>
        }
      >
        <div className="filter-grid">
          <input className="text-input" placeholder={thresholdText(language, 'Sharpe')} value={minPortfolioSharpe} onChange={(event) => { setMinPortfolioSharpe(event.target.value); setPortfolioPage(1) }} />
          <input className="text-input" placeholder={thresholdText(language, 'Return')} value={minPortfolioReturn} onChange={(event) => { setMinPortfolioReturn(event.target.value); setPortfolioPage(1) }} />
          <input className="text-input" placeholder={thresholdText(language, 'CAGR')} value={minPortfolioCagr} onChange={(event) => { setMinPortfolioCagr(event.target.value); setPortfolioPage(1) }} />
          <input className="text-input" placeholder={thresholdText(language, 'MDD')} value={minPortfolioMdd} onChange={(event) => { setMinPortfolioMdd(event.target.value); setPortfolioPage(1) }} />
          <input className="text-input" placeholder={thresholdText(language, 'Checkpoints')} value={minPortfolioRebalances} onChange={(event) => { setMinPortfolioRebalances(event.target.value); setPortfolioPage(1) }} />
          <input className="text-input" placeholder={thresholdText(language, 'Avg Holdings')} value={minPortfolioHoldings} onChange={(event) => { setMinPortfolioHoldings(event.target.value); setPortfolioPage(1) }} />
        </div>
        <div className="data-table-wrap paged-table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>{t('common.show')}</th>
                <th>{t('common.select')}</th>
                <th>{t('common.rank')}</th>
                <th>{t('common.label')}</th>
                <th>{labelText(language, 'Sharpe')}</th>
                <th>{t('metricsTable.checkpoints')}</th>
                <th>{t('metricsTable.exposure')}</th>
                <th>{t('metricsTable.totalReturn')}</th>
                <th>{t('metricsTable.maxDrawdown')}</th>
                <th>{t('common.dateRange')}</th>
                <th className="action-column-header">{t('common.actions')}</th>
              </tr>
            </thead>
            <tbody>
              {portfolioPageRows.map((row: any, index: number) => (
                <tr key={row.backtest_id} className={row.backtest_id === selectedPortfolioId ? 'row-selected' : ''} onClick={() => setSelectedPortfolioId(row.backtest_id)}>
                  <td onClick={(event) => event.stopPropagation()}>
                    <input
                      type="checkbox"
                      checked={shownPortfolioIds.includes(row.backtest_id)}
                      onChange={(event) =>
                        setShownPortfolioIds((current) =>
                          event.target.checked ? [...new Set([...current, row.backtest_id])] : current.filter((value) => value !== row.backtest_id),
                        )
                      }
                    />
                  </td>
                  <td onClick={(event) => event.stopPropagation()}>
                    <input
                      type="radio"
                      name="selected-portfolio"
                      checked={row.backtest_id === selectedPortfolioId}
                      onChange={() => setSelectedPortfolioId(row.backtest_id)}
                    />
                  </td>
                  <td>{(portfolioPage - 1) * portfolioPageSize + index + 1}</td>
                  <td>{row.label}</td>
                  <td>{formatCell(row.sharpe)}</td>
                  <td>{formatMetricValue('rebalance_count', row.rebalance_count)}</td>
                  <td>{formatPortfolioPercent(portfolioExposure(row))}</td>
                  <td>{formatMetricValue('total_return', row.total_return)}</td>
                  <td>{formatMetricValue('max_drawdown', row.max_drawdown)}</td>
                  <td>
                    {row.date_range_start && row.date_range_end
                      ? `${String(row.date_range_start).slice(0, 10)} - ${String(row.date_range_end).slice(0, 10)}`
                      : '-'}
                  </td>
                  <td className="action-column-cell" onClick={(event) => event.stopPropagation()}>
                    <div className="row-actions row-actions-compact">
                      <button className="inline-action-button inline-action-button-compact" onClick={() => navigate({ to: '/metrics/backtests', search: { runId, backtestId: row.backtest_id } })}>{labelText(language, 'Backtest')}</button>
                      <button className="inline-action-button inline-action-button-compact" onClick={() => navigate({ to: '/metrics/parameter-matrix', search: { runId, backtestId: row.backtest_id } })}>{labelText(language, 'Heatmap')}</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="pagination-row">
          <button className="ghost-button" disabled={portfolioPage <= 1} onClick={() => setPortfolioPage((current) => Math.max(1, current - 1))}>{t('common.previous')}</button>
          <span className="muted">{pageText(language, portfolioPage, portfolioTotalPages)}</span>
          <button className="ghost-button" disabled={portfolioPage >= portfolioTotalPages} onClick={() => setPortfolioPage((current) => Math.min(portfolioTotalPages, current + 1))}>{t('common.next')}</button>
        </div>
      </SectionCard>

      <SectionCard
        title={t('metricsOverview.selectedStrategySummary')}
        subtitle={`${t('metricsOverview.currentSelection')}: ${summary.label || selectedPortfolioId || (language === 'zh-Hant' ? '投資組合' : 'Portfolio')}`}
        subtitlePrivate
      >
        <div className="portfolio-command-summary">
          <div className="portfolio-headline-strip">
            {headlineKpis.map(([label, key]) => (
              <div key={key} className="portfolio-headline-metric">
                <span className="portfolio-headline-label">{labelText(language, label)}</span>
                <span className={`portfolio-headline-value ${headlineToneClass(key, summary[key])}`}>
                  {portfolioMetricDisplay(summary, key)}
                </span>
              </div>
            ))}
          </div>

          <div className="portfolio-logic-layout">
            <div className="portfolio-flow-panel">
              <div className="portfolio-flow-title">{labelText(language, 'Strategy Logic')}</div>
              <div className="portfolio-strategy-sentence">{strategyNarrative}</div>
            </div>
            <div className="portfolio-flow-panel">
              <div className="portfolio-flow-rail">
                {strategyFlow.map(([label, value]) => (
                  <div key={label} className="portfolio-flow-node">
                    <span className="portfolio-flow-label">{labelText(language, label)}</span>
                    <span className="portfolio-flow-value">{value}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div className="portfolio-status-rail">
            <span className="portfolio-status-rail-title">{labelText(language, 'Run Health')}</span>
            {healthRows.map(([label, value]) => (
              <div key={label} className="portfolio-status-item">
                <span>{labelText(language, label)}</span>
                <strong>{value}</strong>
              </div>
            ))}
          </div>

          <div className="portfolio-chip-panel">
            {diagnosticChips.map(([label, value]) => (
              <div key={label} className="portfolio-diagnostic-chip">
                <span className="portfolio-chip-label">{labelText(language, label)}</span>
                <span className="portfolio-chip-value">{value}</span>
              </div>
            ))}
          </div>
        </div>
      </SectionCard>
    </div>
  )
}
