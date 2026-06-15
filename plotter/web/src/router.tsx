import { lazy, Suspense, useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'

import { api } from './api'
import { AppShell } from './components/AppShell'
import { CustomSelect } from './components/CustomSelect'
import { ShareToolbar } from './components/ShareToolbar'
import { StrategyRulesPanel, hasRenderableStrategySummary } from './components/StrategyRulesPanel'
import { Language, useCopy } from './i18n'
import { BrowserRouter, Link, Outlet, Route, Routes, useNavigate, useRouterState } from './routing'
import { useAppStore } from './store'

const CommandCenterPage = lazy(() =>
  import('./pages/CommandCenterPage').then((module) => ({ default: module.CommandCenterPage })),
)
const RunCenterPage = lazy(() =>
  import('./pages/RunCenterPage').then((module) => ({ default: module.RunCenterPage })),
)
const MetricsOverviewPage = lazy(() =>
  import('./pages/MetricsOverviewPage').then((module) => ({ default: module.MetricsOverviewPage })),
)
const ParameterMatrixPage = lazy(() =>
  import('./pages/ParameterMatrixPage').then((module) => ({ default: module.ParameterMatrixPage })),
)
const WFAPage = lazy(() =>
  import('./pages/WFAPage').then((module) => ({ default: module.WFAPage })),
)
const BacktestsPage = lazy(() =>
  import('./pages/BacktestsPage').then((module) => ({ default: module.BacktestsPage })),
)

function RouteFallback() {
  return <div className="page-loading">Loading page...</div>
}

function lazyPage(element: JSX.Element) {
  return <Suspense fallback={<RouteFallback />}>{element}</Suspense>
}

function formatTitleToken(value: string) {
  return value.replace(/_/g, ' ').replace(/\b\w/g, (char) => char.toUpperCase())
}

function displayDateToken(value: string) {
  return /^(\d{4})(\d{2})(\d{2})$/.test(value)
    ? value.replace(/^(\d{4})(\d{2})(\d{2})$/, '$1-$2-$3')
    : value
}

function displayFactorToken(value: string, language: Language) {
  const normalized = value.trim().toUpperCase()
  if (normalized === 'PRICE') return language === 'zh-Hant' ? '價格' : 'Price'
  return normalized.replace(/-/g, ' + ')
}

function displayAssetToken(value: string, language: Language) {
  const normalized = value.trim().toUpperCase()
  if (['LOCAL', 'DATASET', 'ASSET'].includes(normalized)) return language === 'zh-Hant' ? '資料集' : 'Dataset'
  return value
}

function displayModeToken(value: string, language: Language) {
  const normalized = value.trim().toLowerCase()
  if (normalized === 'windows') return language === 'zh-Hant' ? '前向分析視窗' : 'Rolling Windows'
  if (normalized === 'matrix') return language === 'zh-Hant' ? '參數矩陣' : 'Parameter Matrix'
  if (normalized === 'single') return language === 'zh-Hant' ? '單次回測' : 'Single Backtest'
  if (normalized === 'summary') return language === 'zh-Hant' ? '摘要' : 'Summary'
  return formatTitleToken(value)
}

function basename(value: string) {
  return String(value || '').split(/[\\/]/).filter(Boolean).pop() || ''
}

function runShortId(run: any, rawLabel?: string) {
  const runId = String(run?.run_id || '').trim()
  const fromRunId = runId.includes('_') ? runId.split('_', 2)[1] : runId
  if (fromRunId) return fromRunId.slice(0, 6)
  const fromLabel = String(rawLabel || '').match(/\b(?:batch|run)\s+([a-z0-9]{6,})\b/i)?.[1]
  return fromLabel ? fromLabel.slice(0, 6) : ''
}

function labelFromStructuredName(rawLabel: string, run: any, language: Language) {
  const clean = basename(rawLabel)
    .replace(/\.(user\.)?json$/i, '')
    .replace(/\.parquet$/i, '')
    .replace(/\s+-\s+(?:batch|run)\s+[a-z0-9]+$/i, '')
  const parts = clean.split('_').filter(Boolean)
  const modeIndex = parts.findIndex((part, index) =>
    index >= 4 && ['windows', 'matrix', 'single', 'summary'].includes(part.toLowerCase()),
  )
  if (!parts.length || modeIndex < 0 || !/^\d{8}$/.test(parts[1] || '')) return ''
  const workflow = parts[0].toLowerCase() === 'wfa'
    ? language === 'zh-Hant' ? '前向分析 (WFA)' : 'Walk-Forward'
    : parts[0].toLowerCase() === 'backtest'
    ? language === 'zh-Hant' ? '回測' : 'Backtest'
    : formatTitleToken(parts[0])
  const runId = runShortId(run, rawLabel)
  return [
    workflow,
    displayDateToken(parts[1]),
    displayAssetToken(parts[2], language),
    displayFactorToken(parts[3] || '', language),
    displayModeToken(parts[modeIndex], language),
    runId ? `${language === 'zh-Hant' ? '執行' : 'run'} ${runId}` : '',
  ]
    .filter(Boolean)
    .join(' | ')
}

function legacyLabelFallbackAllowed(run: any) {
  return Boolean(
    run?.is_legacy_result
    || run?.legacy_missing_validation
    || run?.semantic_index_complete === false
  )
}

function formatRunLabel(run: any, language: Language) {
  const display = String(run?.display_label || '').trim()
  const filename = String(run?.config_filename || '').replace(/\.json$/i, '').trim()
  const runIdText = String(run?.run_id || '').trim()
  for (const label of [display, filename]) {
    if (!label) continue
    const structuredLabel = legacyLabelFallbackAllowed(run) ? labelFromStructuredName(label, run, language) : ''
    if (structuredLabel) return structuredLabel
    if (!/\b(?:ma-cross|hold-reset|threshold-hold-reset|batch)\b/i.test(label)) return label
  }
  return runIdText
}

function displayConfigGroupName(value: string) {
  return basename(value)
    .replace(/\.json$/i, '')
    .replace(/^strategy-run-/i, '')
    .replace(/-example$/i, '')
    .replace(/-/g, ' ')
}

function runGroupLabel(run: any, language: Language) {
  const summary = run?.strategy_summary || {}
  const asset = String(summary.asset_label || run?.symbol || '').trim()
  const workflow = String(summary.workflow_label || run?.strategy_mode || '').trim()
  const configName = displayConfigGroupName(String(run?.config_filename || '')).trim()
  if (configName) return asset && workflow ? `${asset} | ${workflow} | ${configName}` : configName
  return asset && workflow ? `${asset} | ${workflow}` : (language === 'zh-Hant' ? '未分類回測' : 'Unclassified Runs')
}

function runSearchText(run: any, label: string, groupLabel: string) {
  return [
    run?.run_id,
    run?.config_filename,
    run?.display_label,
    run?.semantic_label,
    run?.symbol,
    run?.status,
    run?.created_at,
    run?.completed_at,
    run?.strategy_mode,
    label,
    groupLabel,
  ]
    .filter(Boolean)
    .join(' ')
    .toLowerCase()
}

function MetricsLayout() {
  const navigate = useNavigate()
  const pathname = useRouterState({ select: (state) => state.location.pathname })
  const search = useRouterState({ select: (state) => state.location.search }) as Record<string, string | undefined>
  const runId = useAppStore((state) => state.selectedMetricsRunId)
  const setSelectedMetricsRunId = useAppStore((state) => state.setSelectedMetricsRunId)
  const backtestId = useAppStore((state) => state.selectedBacktestId)
  const setSelectedBacktestId = useAppStore((state) => state.setSelectedBacktestId)
  const pinnedMetricsRunIds = useAppStore((state) => state.pinnedMetricsRunIds)
  const archivedMetricsRunIds = useAppStore((state) => state.archivedMetricsRunIds)
  const togglePinnedMetricsRunId = useAppStore((state) => state.togglePinnedMetricsRunId)
  const toggleArchivedMetricsRunId = useAppStore((state) => state.toggleArchivedMetricsRunId)
  const shareMosaicMode = useAppStore((state) => state.shareMosaicMode)
  const language = useAppStore((state) => state.language)
  const t = useCopy(language)
  const [runSearch, setRunSearch] = useState('')
  const [showArchivedRuns, setShowArchivedRuns] = useState(false)
  const [runListLimit, setRunListLimit] = useState(20)
  const [collapsedRunGroups, setCollapsedRunGroups] = useState<string[]>([])
  const runsQuery = useQuery({
    queryKey: ['metrics-runs'],
    queryFn: api.metricsRuns,
    staleTime: 60000,
  })
  const requestedRunId = search.runId || runId || ''
  const availableRunIds = useMemo(
    () => (runsQuery.data || []).map((run: any) => run.run_id),
    [runsQuery.data],
  )
  const resolvedRunId = availableRunIds.includes(String(requestedRunId))
    ? String(requestedRunId)
    : runsQuery.data?.[0]?.run_id || ''
  const selectedMetricsRun = (runsQuery.data || []).find((run: any) => run.run_id === resolvedRunId) || {}
  const pinnedSet = useMemo(() => new Set(pinnedMetricsRunIds), [pinnedMetricsRunIds])
  const archivedSet = useMemo(() => new Set(archivedMetricsRunIds), [archivedMetricsRunIds])
  const collapsedGroupSet = useMemo(() => new Set(collapsedRunGroups), [collapsedRunGroups])
  const runSearchNeedle = runSearch.trim().toLowerCase()
  const selectableMetricRuns = useMemo(() => {
    const rows = (runsQuery.data || []).map((run: any) => {
      const label = formatRunLabel(run, language)
      const groupLabel = runGroupLabel(run, language)
      return {
        run,
        label,
        groupLabel,
        runId: String(run.run_id || ''),
        archived: archivedSet.has(String(run.run_id || '')),
        pinned: pinnedSet.has(String(run.run_id || '')),
        searchText: runSearchText(run, label, groupLabel),
      }
    })
    return rows
      .filter((item) => showArchivedRuns || !item.archived || item.runId === resolvedRunId)
      .filter((item) => (runSearchNeedle ? item.searchText.includes(runSearchNeedle) : true))
      .sort((left, right) => {
        if (left.pinned !== right.pinned) return left.pinned ? -1 : 1
        return String(right.run.created_at || '').localeCompare(String(left.run.created_at || ''))
      })
  }, [archivedSet, language, pinnedSet, resolvedRunId, runSearchNeedle, runsQuery.data, showArchivedRuns])
  const selectedRunOption = selectableMetricRuns.find((item) => item.runId === resolvedRunId)
    || (runsQuery.data || [])
      .map((run: any) => {
        const label = formatRunLabel(run, language)
        return {
          run,
          label,
          groupLabel: runGroupLabel(run, language),
          runId: String(run.run_id || ''),
          archived: archivedSet.has(String(run.run_id || '')),
          pinned: pinnedSet.has(String(run.run_id || '')),
          searchText: '',
        }
      })
      .find((item) => item.runId === resolvedRunId)
  const limitedMetricRuns = useMemo(() => {
    const limited = selectableMetricRuns.slice(0, runListLimit)
    if (selectedRunOption && !limited.some((item) => item.runId === selectedRunOption.runId)) {
      return [selectedRunOption, ...limited]
    }
    return limited
  }, [runListLimit, selectableMetricRuns, selectedRunOption])
  const runSelectOptions = useMemo(() => {
    const groups = new Map<string, typeof limitedMetricRuns>()
    for (const item of limitedMetricRuns) {
      const groupKey = item.groupLabel
      groups.set(groupKey, [...(groups.get(groupKey) || []), item])
    }
    const options: Array<{ value: string; label: string; kind?: 'option' | 'group'; expanded?: boolean }> = []
    for (const [groupLabel, items] of groups) {
      const collapsed = collapsedGroupSet.has(groupLabel)
      options.push({
        value: `group:${groupLabel}`,
        label: `${groupLabel} (${items.length})`,
        kind: 'group',
        expanded: !collapsed,
      })
      if (!collapsed) {
        for (const item of items) {
          const badges = [
            item.pinned ? (language === 'zh-Hant' ? '已釘選' : 'Pinned') : '',
            item.archived ? (language === 'zh-Hant' ? '已封存' : 'Archived') : '',
          ].filter(Boolean)
          options.push({
            value: item.runId,
            label: badges.length ? `${badges.join(' | ')} - ${item.label}` : item.label,
          })
        }
      }
    }
    return options
  }, [collapsedGroupSet, language, limitedMetricRuns])
  const hasMoreRuns = selectableMetricRuns.length > runListLimit
  const isSelectedPinned = pinnedSet.has(resolvedRunId)
  const isSelectedArchived = archivedSet.has(resolvedRunId)
  const isBacktestsPage = pathname === '/metrics/backtests'
  const needsOverviewForLayout = pathname === '/metrics' || pathname === '/metrics/parameter-matrix' || isBacktestsPage
  const overviewQuery = useQuery({
    queryKey: ['metrics-overview', resolvedRunId],
    queryFn: () => api.metricsOverview(resolvedRunId),
    enabled: Boolean(resolvedRunId && needsOverviewForLayout),
    staleTime: 60000,
  })
  const availableBacktestIds = useMemo(
    () => (overviewQuery.data?.rows || []).map((row: any) => row.backtest_id),
    [overviewQuery.data],
  )
  const searchBacktestId = typeof search.backtestId === 'string' ? search.backtestId : ''
  const resolvedBacktestId = availableBacktestIds.includes(String(searchBacktestId))
    ? String(searchBacktestId)
    : availableBacktestIds.includes(String(backtestId))
    ? String(backtestId)
    : overviewQuery.data?.rows?.[0]?.backtest_id || ''
  const overviewStrategySummary = overviewQuery.data?.strategy_summary || {}
  const runStrategySummary = selectedMetricsRun.strategy_summary || {}
  const strategySummary = hasRenderableStrategySummary(overviewStrategySummary)
    ? overviewStrategySummary
    : runStrategySummary
  const strategyLoading = runsQuery.isLoading || (overviewQuery.isLoading && !hasRenderableStrategySummary(strategySummary))

  useEffect(() => {
    if (resolvedRunId && resolvedRunId !== runId) {
      setSelectedMetricsRunId(resolvedRunId)
    }
  }, [resolvedRunId, runId, setSelectedMetricsRunId])

  useEffect(() => {
    if (isBacktestsPage && resolvedBacktestId && resolvedBacktestId !== backtestId) {
      setSelectedBacktestId(resolvedBacktestId)
    }
  }, [backtestId, isBacktestsPage, resolvedBacktestId, setSelectedBacktestId])

  return (
    <div className={`page-stack ${shareMosaicMode ? 'share-mosaic-mode' : ''}`} data-share-capture-root>
      <div className="metrics-header-shell">
        <div className="metrics-header-title">{t('metrics.title')}</div>
        <div className="metrics-header-subtitle">
          {t('metrics.subtitle')}
        </div>
        <div className="metrics-subnav">
          <Link
            className={`subnav-link ${pathname === '/metrics' ? 'active' : ''}`}
            to="/metrics"
            search={resolvedRunId ? { runId: resolvedRunId } : {}}
            activeOptions={{ exact: true }}
          >
            {t('metrics.overview')}
          </Link>
          <Link
            className={`subnav-link ${pathname === '/metrics/parameter-matrix' ? 'active' : ''}`}
            to="/metrics/parameter-matrix"
            search={resolvedRunId ? { runId: resolvedRunId, ...(resolvedBacktestId ? { backtestId: resolvedBacktestId } : {}) } : {}}
            activeOptions={{ exact: true }}
          >
            {t('workflow.parameterMatrix')}
          </Link>
          <Link
            className={`subnav-link ${pathname === '/metrics/backtests' ? 'active' : ''}`}
            to="/metrics/backtests"
            search={resolvedRunId ? { runId: resolvedRunId, ...(resolvedBacktestId ? { backtestId: resolvedBacktestId } : {}) } : {}}
            activeOptions={{ exact: true }}
          >
            {t('workflow.backtests')}
          </Link>
        </div>
        <ShareToolbar filenamePrefix={resolvedRunId ? `lo2cin4bt-${resolvedRunId}` : 'lo2cin4bt-metrics'} />
        <div className="metrics-header-controls">
          <div className="metrics-selector-stack">
            <div className="metrics-header-field">
              <div className="metrics-header-label">{t('metrics.metricsFileSelection')}</div>
              <CustomSelect
                className="metrics-header-select"
                value={resolvedRunId}
                options={runSelectOptions}
                redactValues
                onGroupToggle={(groupValue) => {
                  const groupKey = String(groupValue).replace(/^group:/, '')
                  setCollapsedRunGroups((current) =>
                    current.includes(groupKey)
                      ? current.filter((item) => item !== groupKey)
                      : [...current, groupKey],
                  )
                }}
                onChange={(nextRunId) => {
                  navigate({
                    to:
                      pathname === '/metrics/backtests'
                        ? '/metrics/backtests'
                        : pathname === '/metrics/parameter-matrix'
                        ? '/metrics/parameter-matrix'
                        : '/metrics',
                    search: { runId: nextRunId },
                  })
                }}
              />
              <div className="metrics-run-tools">
                <input
                  className="text-input metrics-run-search"
                  value={runSearch}
                  placeholder={language === 'zh-Hant' ? '搜尋 run id / config / symbol / date' : 'Search run id / config / symbol / date'}
                  onChange={(event) => {
                    setRunSearch(event.target.value)
                    setRunListLimit(20)
                  }}
                />
                <div className="metrics-run-action-row">
                  <button
                    type="button"
                    className={`inline-action-button inline-action-button-compact ${isSelectedPinned ? 'active' : ''}`}
                    disabled={!resolvedRunId}
                    onClick={() => togglePinnedMetricsRunId(resolvedRunId)}
                  >
                    {language === 'zh-Hant' ? '\u91d8\u9078\u7b56\u7565' : isSelectedPinned ? 'Unpin strategy' : 'Pin strategy'}
                  </button>
                  <button
                    type="button"
                    className={`inline-action-button inline-action-button-compact ${isSelectedArchived ? 'active' : ''}`}
                    disabled={!resolvedRunId}
                    onClick={() => toggleArchivedMetricsRunId(resolvedRunId)}
                  >
                    {language === 'zh-Hant' ? '\u5c01\u5b58\u7b56\u7565' : isSelectedArchived ? 'Unarchive strategy' : 'Archive strategy'}
                  </button>
                  <button
                    type="button"
                    className={`inline-action-button inline-action-button-compact ${showArchivedRuns ? 'active' : ''}`}
                    onClick={() => setShowArchivedRuns((current) => !current)}
                  >
                    {language === 'zh-Hant' ? '\u986f\u793a\u5c01\u5b58\u7b56\u7565' : showArchivedRuns ? 'Hide archived strategies' : 'Show archived strategies'}
                  </button>
                  {hasMoreRuns ? (
                    <button
                      type="button"
                      className="inline-action-button inline-action-button-compact"
                      onClick={() => setRunListLimit((current) => current + 20)}
                    >
                      {language === 'zh-Hant' ? `顯示更多 ${runListLimit}/${selectableMetricRuns.length}` : `Show more ${runListLimit}/${selectableMetricRuns.length}`}
                    </button>
                  ) : (
                    <span className="metrics-run-count">
                      {language === 'zh-Hant' ? `${selectableMetricRuns.length} 筆` : `${selectableMetricRuns.length} runs`}
                    </span>
                  )}
                </div>
              </div>
            </div>
            {isBacktestsPage ? (
              <div className="metrics-header-field">
                <div className="metrics-header-label">{t('metrics.backtestSelection')}</div>
                <CustomSelect
                  className="metrics-header-select"
                  value={resolvedBacktestId}
                  options={(overviewQuery.data?.rows || []).map((row: any) => ({
                    value: row.backtest_id,
                    label: row.label,
                  }))}
                  onChange={(nextBacktestId) => {
                    navigate({
                      to: '/metrics/backtests',
                      search: { runId: resolvedRunId, backtestId: nextBacktestId },
                    })
                  }}
                  redactValues
                />
              </div>
            ) : null}
          </div>
          <StrategyRulesPanel summary={strategySummary} loading={strategyLoading} />
        </div>
      </div>
      <Outlet />
    </div>
  )
}

export function RouterProvider() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<AppShell />}>
          <Route index element={lazyPage(<CommandCenterPage />)} />
          <Route path="run-center" element={lazyPage(<RunCenterPage />)} />
          <Route path="wfa" element={lazyPage(<WFAPage />)} />
          <Route path="metrics" element={<MetricsLayout />}>
            <Route index element={lazyPage(<MetricsOverviewPage />)} />
            <Route path="parameter-matrix" element={lazyPage(<ParameterMatrixPage />)} />
            <Route path="backtests" element={lazyPage(<BacktestsPage />)} />
          </Route>
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
