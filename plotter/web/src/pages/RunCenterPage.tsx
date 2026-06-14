import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '../routing'

import { api, createBatchSocket, AppApiError } from '../api'
import { useCopy } from '../i18n'
import { SectionCard } from '../components/SectionCard'
import { StatusBadge } from '../components/StatusBadge'
import { useAppStore } from '../store'
import type { Language } from '../i18n'
import { badgeLabel, jobLogLineLabel, operationErrorLabel } from '../uiVocabulary'

type ConfigItem = {
  label: string
  display_label?: string
  filename?: string
  badges?: string[]
  value: string
  config_mtime?: number
  summary: Record<string, unknown>
}

function configSortDate(item: ConfigItem) {
  const label = String(item.display_label || item.filename || item.label || '')
  const match = label.match(/(20\d{6})/)
  return match ? Number(match[1]) : 0
}

function compareConfigNewestFirst(left: ConfigItem, right: ConfigItem) {
  const dateDelta = configSortDate(right) - configSortDate(left)
  if (dateDelta !== 0) return dateDelta
  const mtimeDelta = Number(right.config_mtime || 0) - Number(left.config_mtime || 0)
  if (mtimeDelta !== 0) return mtimeDelta
  return String(right.display_label || right.filename || right.label || '').localeCompare(
    String(left.display_label || left.filename || left.label || ''),
  )
}

const zhLabelReplacements: Array<[RegExp, string]> = [
  [/\bPredictor Analysis\b/g, '因子分析'],
  [/\bFactor Analysis\b/g, '因子分析'],
  [/\bBacktests\b/g, '回測'],
  [/\bBacktest\b/g, '回測'],
  [/\bWalk-Forward\b/g, '前向分析 (WFA)'],
  [/\bRolling Windows\b/g, '前向分析視窗'],
  [/\bParameter Matrix\b/g, '參數矩陣'],
  [/\bSingle Backtest\b/g, '單次回測'],
  [/\bPortfolio Backtest\b/g, '投資組合回測'],
  [/\bPortfolio\b/g, '投資組合'],
  [/\bAllocation\b/g, '配置'],
  [/\bCalendar\b/g, '日曆'],
  [/\bPrice\b/g, '價格'],
  [/\bFactor\b/g, '因子'],
  [/\bSummary\b/g, '摘要'],
  [/\bProduction\b/g, '正式'],
  [/\bTest\b/g, '測試'],
  [/\bcfg\b/g, '設定'],
  [/\brun\b/g, '執行'],
]
function formatRunCenterLabel(value: string, language: Language) {
  if (language !== 'zh-Hant') {
    return value.replace(/^Predictor Analysis\b/, 'Factor Analysis')
  }
  return zhLabelReplacements.reduce((label, [pattern, replacement]) => {
    return label.replace(pattern, replacement)
  }, value)
}

function formatBadge(value: string, language: Language) {
  return badgeLabel(value, language)
}

function formatModuleName(value: string, t: ReturnType<typeof useCopy>) {
  const normalized = value.toLowerCase()
  if (normalized === 'autorunner') return t('module.autorunner')
  if (normalized === 'wfa') return t('module.wfa')
  if (normalized === 'statanalyser') return t('module.statanalyser')
  return t('module.unknown')
}

function formatJobStage(value: unknown, language: Language) {
  return jobLogLineLabel(value, language)
}

function jobCountLabel(count: number, language: Language) {
  if (language === 'zh-Hant') return `${count} 個子任務`
  return `${count} ${count === 1 ? 'job' : 'jobs'}`
}

function subsetSizeLabel(jobCount: number, totalWeight: number, language: Language) {
  const size = jobCount >= 3 || totalWeight >= 4 ? 'large' : jobCount === 2 ? 'medium' : 'small'
  if (language === 'zh-Hant') {
    if (size === 'large') return '大型子集'
    if (size === 'medium') return '中型子集'
    return '小型子集'
  }
  if (size === 'large') return 'Large subset'
  if (size === 'medium') return 'Medium subset'
  return 'Small subset'
}

function batchSizeClass(jobCount: number, totalWeight: number) {
  if (jobCount >= 3 || totalWeight >= 4) return 'batch-card-large'
  if (jobCount === 2) return 'batch-card-medium'
  return 'batch-card-small'
}

function jobStatusClass(status: unknown) {
  const normalized = String(status || 'unknown').toLowerCase()
  if (normalized === 'completed') return 'job-row-completed'
  if (normalized === 'partial') return 'job-row-partial'
  if (normalized === 'failed') return 'job-row-failed'
  if (normalized === 'running') return 'job-row-running'
  if (normalized === 'queued' || normalized === 'pending') return 'job-row-queued'
  return 'job-row-neutral'
}

function batchProgress(batch: Record<string, any>) {
  const jobs = Array.isArray(batch.jobs) ? batch.jobs : []
  const total = jobs.length
  const done = jobs.filter((job: any) => ['completed', 'partial'].includes(String(job?.status || '').toLowerCase())).length
  const running = jobs.filter((job: any) => String(job?.status || '').toLowerCase() === 'running').length
  const failed = jobs.filter((job: any) => String(job?.status || '').toLowerCase() === 'failed').length
  const percent = total > 0 ? Math.round((done / total) * 100) : 0
  const totalWeight = jobs.reduce((sum: number, job: any) => sum + Number(job?.weight || 1), 0)
  return { total, done, running, failed, percent, totalWeight }
}

function parseTimeMs(value: unknown) {
  const parsed = Date.parse(String(value || ''))
  return Number.isFinite(parsed) ? parsed : null
}

function formatDuration(ms: number, language: Language) {
  const safeMs = Math.max(0, Math.floor(ms))
  const totalSeconds = Math.floor(safeMs / 1000)
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60
  if (hours > 0) return language === 'zh-Hant' ? `${hours}小時 ${minutes}分` : `${hours}h ${minutes}m`
  if (minutes > 0) return language === 'zh-Hant' ? `${minutes}分 ${seconds}秒` : `${minutes}m ${seconds}s`
  return language === 'zh-Hant' ? `${seconds}秒` : `${seconds}s`
}

function formatAge(timeMs: number | null, nowMs: number, language: Language) {
  if (timeMs === null) return language === 'zh-Hant' ? '未有更新時間' : 'no update time'
  const label = formatDuration(nowMs - timeMs, language)
  return language === 'zh-Hant' ? `${label}前更新` : `updated ${label} ago`
}

function stageOrderForModule(module: unknown) {
  const normalized = String(module || '').toLowerCase()
  if (normalized === 'wfa') return ['queued', 'starting', 'config_validation', 'wfanalyser', 'app_export', 'completed']
  return [
    'queued',
    'starting',
    'config_validation',
    'dataloader',
    'backtester',
    'metricstracker',
    'statanalyser',
    'app_export',
    'completed',
  ]
}

function stageProgress(job: Record<string, any>) {
  const order = stageOrderForModule(job.module)
  const status = String(job.status || '').toLowerCase()
  if (['completed', 'partial', 'failed'].includes(status)) {
    return { index: order.length, total: order.length, percent: 100 }
  }
  const stage = String(job.stage || 'queued').toLowerCase()
  const rawIndex = order.indexOf(stage)
  const index = rawIndex >= 0 ? rawIndex + 1 : 1
  return {
    index,
    total: order.length,
    percent: Math.max(4, Math.round((index / order.length) * 100)),
  }
}

function jobTiming(job: Record<string, any>, nowMs: number, language: Language) {
  const status = String(job.status || '').toLowerCase()
  const startedAt = parseTimeMs(job.started_at || job.created_at)
  const updatedAt = parseTimeMs(job.updated_at || job.started_at || job.created_at)
  const completedAt = parseTimeMs(job.completed_at)
  const elapsedEnd = completedAt ?? nowMs
  const elapsed = startedAt === null ? '-' : formatDuration(elapsedEnd - startedAt, language)
  const ageMs = updatedAt === null ? 0 : nowMs - updatedAt
  const stale = ['running', 'queued'].includes(status) && ageMs > 180000
  return {
    elapsed,
    updated: formatAge(updatedAt, nowMs, language),
    stale,
  }
}

function debugErrorPayload(error: unknown) {
  if (error instanceof AppApiError) {
    return {
      name: error.name,
      message: error.message,
      status: error.status,
      path: error.path,
      detail: error.detail,
    }
  }
  if (error instanceof Error) {
    return {
      name: error.name,
      message: error.message,
      stack: error.stack,
    }
  }
  return { value: String(error ?? '') }
}

function batchDebugPayload(batch: Record<string, any>) {
  return {
    batch_id: batch.batch_id,
    module: batch.module,
    status: batch.status,
    jobs: (Array.isArray(batch.jobs) ? batch.jobs : []).map((job: any) => ({
      job_id: job.job_id,
      module: job.module,
      label: job.label,
      display_label: job.display_label,
      config_path: job.config_path,
      status: job.status,
      stage: job.stage,
      stage_message: job.stage_message,
      error: job.error,
      run_id: job.run_id,
      result_refs: job.result_refs,
      created_at: job.created_at,
      started_at: job.started_at,
      updated_at: job.updated_at,
      completed_at: job.completed_at,
      logs: job.logs,
    })),
  }
}

function ConfigSelector({
  title,
  items,
  selected,
  onChange,
  t,
  formatLabel = (value: string) => value,
  disabled = false,
  disabledMessage,
  workspaceTarget,
  outputTarget,
  folderBusy = false,
  onOpenFolder,
}: {
  title: string
  items: ConfigItem[]
  selected: string[]
  onChange: (value: string[]) => void
  t: ReturnType<typeof useCopy>
  formatLabel?: (value: string) => string
  disabled?: boolean
  disabledMessage?: string
  workspaceTarget?: string
  outputTarget?: string
  folderBusy?: boolean
  onOpenFolder?: (target: string) => void
}) {
  const [search, setSearch] = useState('')
  const safeItems = useMemo(
    () => (Array.isArray(items) ? [...items].sort(compareConfigNewestFirst) : []),
    [items],
  )
  const filtered = useMemo(
    () =>
      safeItems.filter((item) =>
        String(item?.display_label || item?.filename || item?.label || '')
          .toLowerCase()
          .includes(search.trim().toLowerCase()),
      ),
    [safeItems, search],
  )
  return (
    <div className={`selector-card${disabled ? ' selector-card-disabled' : ''}`}>
      <div className="selector-header">
        <div className="selector-title">{title}</div>
        {onOpenFolder && (workspaceTarget || outputTarget) ? (
          <div className="selector-header-actions">
            {workspaceTarget ? (
              <button
                type="button"
                className="ghost-button selector-open-folder-button"
                disabled={folderBusy}
                onClick={() => onOpenFolder(workspaceTarget)}
              >
                {t('runCenter.openWorkspace')}
              </button>
            ) : null}
            {outputTarget ? (
              <button
                type="button"
                className="ghost-button selector-open-folder-button"
                disabled={folderBusy}
                onClick={() => onOpenFolder(outputTarget)}
              >
                {t('runCenter.openOutput')}
              </button>
            ) : null}
          </div>
        ) : null}
      </div>
      {disabledMessage ? <div className="selector-disabled-note">{disabledMessage}</div> : null}
      <input
        className="text-input"
        placeholder={t('runCenter.searchConfig')}
        value={search}
        disabled={disabled}
        onChange={(event) => setSearch(event.target.value)}
      />
      <div className="selector-actions">
        <button
          className="ghost-button"
          disabled={disabled}
          onClick={() => onChange(filtered.map((item) => item.value))}
        >
          {t('common.selectAll')}
        </button>
        <button className="ghost-button" disabled={disabled} onClick={() => onChange([])}>
          {t('common.clear')}
        </button>
      </div>
      <div className="checkbox-list">
        {filtered.map((item) => (
          <label key={item.value} className="checkbox-row">
            <input
              type="checkbox"
              checked={selected.includes(item.value)}
              disabled={disabled}
              onChange={(event) =>
                onChange(
                  event.target.checked
                    ? [...selected, item.value]
                    : selected.filter((value) => value !== item.value),
                )
              }
            />
              <span className="checkbox-label-block">
                <span className="checkbox-label-main">
                {formatLabel(String(item.display_label || item.filename || item.label || ''))}
                {(item.badges || []).map((badge) => (
                  <span key={`${item.value}-${badge}`} className="mini-badge">
                    {formatLabel(badge)}
                  </span>
                ))}
              </span>
            </span>
          </label>
        ))}
      </div>
    </div>
  )
}

export function RunCenterPage() {
  const queryClient = useQueryClient()
  const addBatchId = useAppStore((state) => state.addBatchId)
  const removeBatchId = useAppStore((state) => state.removeBatchId)
  const replaceBatchIds = useAppStore((state) => state.replaceBatchIds)
  const trackedBatchIds = useAppStore((state) => state.batchIds)
  const language = useAppStore((state) => state.language)
  const t = useCopy(language)
  const [selectedAutorunner, setSelectedAutorunner] = useState<string[]>([])
  const [selectedWfa, setSelectedWfa] = useState<string[]>([])
  const [liveBatches, setLiveBatches] = useState<Record<string, any>>({})
  const [nowMs, setNowMs] = useState(() => Date.now())
  const [runCenterFeedback, setRunCenterFeedback] = useState('')
  const [lastDebugError, setLastDebugError] = useState<Record<string, unknown> | null>(null)
  const socketsRef = useRef<Record<string, WebSocket>>({})

  const configsQuery = useQuery({
    queryKey: ['run-center-configs'],
    queryFn: api.runCenterConfigs,
    staleTime: 15000,
  })
  const commandCenterQuery = useQuery({
    queryKey: ['command-center'],
    queryFn: api.commandCenter,
    staleTime: 1000,
    refetchInterval: 1500,
  })
  const batchMutation = useMutation({
    mutationFn: (payload: { module: string; config_paths: string[] }) =>
      api.createBatch(payload),
    onSuccess: (data) => {
      addBatchId(data.batch_id)
      setLiveBatches((current) => ({ ...current, [data.batch_id]: data }))
      setRunCenterFeedback('')
      setLastDebugError(null)
      queryClient.invalidateQueries({ queryKey: ['command-center'] })
    },
    onError: (error: unknown) => {
      setRunCenterFeedback(operationErrorLabel('run_center_batch_submit_failed', language))
      setLastDebugError({
        operation: 'run_center_batch_submit_failed',
        timestamp: new Date().toISOString(),
        error: debugErrorPayload(error),
      })
    },
  })
  const folderMutation = useMutation({
    mutationFn: (target: string) => api.openFolder({ target }),
    onSuccess: (data) => {
      const openedPath = String(data?.path || '')
      setRunCenterFeedback(
        language === 'zh-Hant'
          ? `已開啟資料夾：${openedPath}`
          : `Opened folder: ${openedPath}`,
      )
      setLastDebugError(null)
    },
    onError: (error: unknown) => {
      setRunCenterFeedback(
        language === 'zh-Hant'
          ? '無法開啟資料夾。'
          : 'Unable to open the folder.',
      )
      setLastDebugError({
        operation: 'workspace_open_failed',
        timestamp: new Date().toISOString(),
        error: debugErrorPayload(error),
      })
    },
  })

  const sameIds = (left: string[], right: string[]) =>
    left.length === right.length && left.every((value, index) => value === right[index])

  useEffect(() => {
    const timer = window.setInterval(() => setNowMs(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [])

  useEffect(() => {
    if (!commandCenterQuery.data) {
      return
    }
    const activeIds = (commandCenterQuery.data.active_batches || [])
      .map((batch: any) => String(batch?.batch_id || ''))
      .filter(Boolean)
    const liveIds = trackedBatchIds.filter((batchId) => Boolean(liveBatches[batchId]))
    const nextBatchIds = Array.from(new Set([...activeIds, ...liveIds])).slice(0, 8)
    if (!sameIds(trackedBatchIds, nextBatchIds)) {
      replaceBatchIds(nextBatchIds)
    }
  }, [commandCenterQuery.data, liveBatches, replaceBatchIds, trackedBatchIds])

  useEffect(() => {
    const trackedSet = new Set(trackedBatchIds)

    Object.entries(socketsRef.current).forEach(([batchId, socket]) => {
      if (trackedSet.has(batchId)) {
        return
      }
      try {
        socket.close()
      } catch {
        // Ignore cleanup close failures.
      }
      delete socketsRef.current[batchId]
    })

    trackedBatchIds.forEach((batchId) => {
      if (socketsRef.current[batchId]) {
        return
      }
      const socket = createBatchSocket(batchId)
      socketsRef.current[batchId] = socket

      api.getBatch(batchId)
        .then((batch) => {
          setLiveBatches((current) => ({ ...current, [batchId]: batch }))
        })
        .catch(() => {
          removeBatchId(batchId)
          setLiveBatches((current) => {
            const next = { ...current }
            delete next[batchId]
            return next
          })
        })

      socket.onmessage = async () => {
        try {
          const batch = await api.getBatch(batchId)
          setLiveBatches((current) => ({ ...current, [batchId]: batch }))
        } catch {
          removeBatchId(batchId)
          setLiveBatches((current) => {
            const next = { ...current }
            delete next[batchId]
            return next
          })
        }
      }

      socket.onclose = () => {
        delete socketsRef.current[batchId]
      }
    })

  }, [trackedBatchIds, removeBatchId])

  useEffect(
    () => () => {
      Object.values(socketsRef.current).forEach((socket) => {
        try {
          socket.close()
        } catch {
          // Ignore cleanup close failures.
        }
      })
      socketsRef.current = {}
    },
    [],
  )

  if (configsQuery.isLoading) {
    return <div className="page-loading">{t('common.loading.runCenter')}</div>
  }
  if (configsQuery.error || !configsQuery.data) {
    return <div className="page-error">{t('runCenter.unableConfigs')}</div>
  }
  const autorunnerItems = Array.isArray(configsQuery.data.autorunner) ? configsQuery.data.autorunner : []
  const wfaItems = Array.isArray(configsQuery.data.wfa) ? configsQuery.data.wfa : []
  const statanalyserItems = Array.isArray(configsQuery.data.statanalyser) ? configsQuery.data.statanalyser : []
  const formatDisplayLabel = (value: string) => formatRunCenterLabel(value, language)

  const submitBatch = (module: string, configPaths: string[]) => {
    if (configPaths.length === 0) {
      return
    }
    batchMutation.mutate({ module, config_paths: configPaths })
  }
  const openFolder = (target: string) => {
    folderMutation.mutate(target)
  }

  const activeBatches = Array.isArray(commandCenterQuery.data?.active_batches)
    ? commandCenterQuery.data.active_batches
    : []
  const renderedBatches = [
    ...activeBatches,
    ...trackedBatchIds
      .map((batchId) => liveBatches[batchId])
      .filter((batch): batch is Record<string, any> => Boolean(batch) && typeof batch === 'object')
      .filter((batch) => !activeBatches.some((active: any) => active?.batch_id === batch?.batch_id)),
  ]
  const safeBatches = renderedBatches
    .filter((batch): batch is Record<string, any> => Boolean(batch) && typeof batch === 'object')
    .map((batch) => ({
      ...batch,
      batch_id: String(batch?.batch_id || 'unknown-batch'),
      module: String(batch?.module || 'unknown'),
      status: String(batch?.status || 'unknown'),
      jobs: Array.isArray(batch?.jobs) ? batch.jobs : [],
    }))

  return (
    <div className="page-stack">
      <SectionCard
        title={t('runCenter.title')}
        subtitle={t('runCenter.subtitle')}
      >
        <div className="run-center-grid">
          <ConfigSelector
            title={t('workflow.factorAnalysis')}
            items={statanalyserItems}
            selected={[]}
            onChange={() => undefined}
            t={t}
            formatLabel={formatDisplayLabel}
            disabled
            disabledMessage={
              language === 'zh-Hant'
                ? '因子分析之後仍會在執行中心啟動；目前因下一階段才開發執行代碼，暫時未開放。'
                : 'Factor Analysis will still launch from Run Center; it is temporarily unavailable until its execution code is built in the next phase.'
            }
          />
          <ConfigSelector
            title={t('workflow.backtests')}
            items={autorunnerItems}
            selected={selectedAutorunner}
            onChange={setSelectedAutorunner}
            t={t}
            formatLabel={formatDisplayLabel}
            workspaceTarget="autorunner"
            outputTarget="autorunner-output"
            folderBusy={folderMutation.isPending}
            onOpenFolder={openFolder}
          />
          <ConfigSelector
            title={t('workflow.walkForward')}
            items={wfaItems}
            selected={selectedWfa}
            onChange={setSelectedWfa}
            t={t}
            formatLabel={formatDisplayLabel}
            workspaceTarget="wfa"
            outputTarget="wfa-output"
            folderBusy={folderMutation.isPending}
            onOpenFolder={openFolder}
          />
        </div>
        <div className="run-submit-row">
          <button
            className="run-submit-button"
            disabled
          >
            {t('runCenter.runFactorBatch')}
          </button>
          <button
            className="run-submit-button"
            onClick={() => submitBatch('autorunner', selectedAutorunner)}
            disabled={batchMutation.isPending || selectedAutorunner.length === 0}
          >
            {t('runCenter.runBacktestBatch')}
          </button>
          <button
            className="run-submit-button"
            onClick={() => submitBatch('wfa', selectedWfa)}
            disabled={batchMutation.isPending || selectedWfa.length === 0}
          >
            {t('runCenter.runWfaBatch')}
          </button>
        </div>
        {runCenterFeedback ? (
          <div className="research-feedback-banner" style={{ marginTop: '1rem' }}>
            {runCenterFeedback}
          </div>
        ) : null}
      </SectionCard>

      <div id="run-center-debug-panel">
        <SectionCard
          title={language === 'zh-Hant' ? 'DEBUG 中心' : 'Debug Panel'}
          subtitle={
            language === 'zh-Hant'
              ? '原始錯誤、job stage、config path 與最後日誌集中在這裡，平時保持摺疊。'
              : 'Raw errors, job stages, config paths, and recent logs are collected here and kept collapsed by default.'
          }
        >
          <details className="diagnostic-details">
            <summary>
              {language === 'zh-Hant'
                ? `目前追蹤 ${safeBatches.length} 個批次`
                : `${safeBatches.length} tracked ${safeBatches.length === 1 ? 'batch' : 'batches'}`}
            </summary>
            <pre className="log-view">
              {JSON.stringify(
                {
                  last_error: lastDebugError,
                  batches: safeBatches.map(batchDebugPayload),
                },
                null,
                2,
              )}
            </pre>
          </details>
        </SectionCard>
      </div>

      <SectionCard title={t('runCenter.runningJobs')}>
        {safeBatches.length === 0 ? (
          <div className="muted">{t('runCenter.noTrackedBatches')}</div>
        ) : (
          <div className="batch-list">
            {safeBatches.map((batch) => {
              const progress = batchProgress(batch)
              return (
              <div
                key={batch.batch_id}
                className={`batch-card ${batchSizeClass(progress.total, progress.totalWeight)} batch-card-${batch.module}`}
              >
                <div className="batch-card-header">
                  <div className="batch-heading">
                    <div className="batch-title-row">
                      <div className="run-card-title">{batch.batch_id}</div>
                      <span className="subset-pill">
                        {subsetSizeLabel(progress.total, progress.totalWeight, language)}
                      </span>
                      <span className="subset-count-pill">{jobCountLabel(progress.total, language)}</span>
                    </div>
                    <div className="run-card-subtitle">{formatModuleName(batch.module, t)}</div>
                  </div>
                  <StatusBadge status={batch.status} />
                </div>
                <div className="batch-progress-shell" aria-label={`${progress.percent}%`}>
                  <div className="batch-progress-track">
                    <div className="batch-progress-fill" style={{ width: `${progress.percent}%` }} />
                  </div>
                  <div className="batch-progress-meta">
                    <span>
                      {language === 'zh-Hant'
                        ? `完成 ${progress.done}/${progress.total}`
                        : `${progress.done}/${progress.total} done`}
                    </span>
                    <span>
                      {language === 'zh-Hant'
                        ? `執行中 ${progress.running} · 失敗 ${progress.failed}`
                        : `${progress.running} running · ${progress.failed} failed`}
                    </span>
                  </div>
                </div>
                <div className="job-list">
                  {batch.jobs.map((job: any) => {
                    const stage = stageProgress(job)
                    const timing = jobTiming(job, nowMs, language)
                    return (
                    <div key={job.job_id} className={`job-row ${jobStatusClass(job.status)}`}>
                      <span className="job-rail-marker" aria-hidden="true" />
                      <div className="job-main">
                        <div className="job-title-line">
                          <span>
                            {formatDisplayLabel(String(job.display_label || job.label || ''))}
                          </span>
                          {(job.label_badges || []).map((badge: string) => (
                            <span key={`${job.job_id}-${badge}`} className="mini-badge">
                              {formatBadge(badge, language)}
                            </span>
                          ))}
                        </div>
                        <div className="job-stage-line">
                          <span>{formatJobStage(job.stage, language)}</span>
                          <span>
                            {language === 'zh-Hant'
                              ? `階段 ${stage.index}/${stage.total}`
                              : `stage ${stage.index}/${stage.total}`}
                          </span>
                        </div>
                        <div className="job-stage-meter" aria-label={`${stage.percent}%`}>
                          <div className="job-stage-meter-fill" style={{ width: `${stage.percent}%` }} />
                        </div>
                        <div className={`job-timing${timing.stale ? ' job-timing-stale' : ''}`}>
                          <span>
                            {language === 'zh-Hant' ? `已用時 ${timing.elapsed}` : `elapsed ${timing.elapsed}`}
                          </span>
                          <span>{timing.updated}</span>
                          {timing.stale ? (
                            <span>{language === 'zh-Hant' ? '超過 3 分鐘無更新，可能卡住或仍在長算' : 'no update for 3m; may be stuck or still computing'}</span>
                          ) : null}
                        </div>
                      </div>
                      <StatusBadge status={job.status} />
                    </div>
                    )
                  })}
                </div>
                <pre className="log-view">
                  {batch.jobs
                    .flatMap((job: any) => (Array.isArray(job?.logs) ? job.logs.slice(-4) : []))
                    .map((line: unknown) => formatJobStage(line, language))
                    .join('\n') || t('runCenter.noLogsYet')}
                </pre>
              </div>
              )
            })}
          </div>
        )}
      </SectionCard>

      <SectionCard title={t('runCenter.batchResults')}>
        <div className="run-card-list">
          {safeBatches.flatMap((batch) =>
            batch.jobs
              .filter((job: any) => ['completed', 'partial'].includes(job.status) && job.run_id)
              .map((job: any) => (
                <div key={job.job_id} className="run-card">
                  <div className="run-card-header">
                    <div>
                      <div className="run-card-title">
                        {formatDisplayLabel(String(job.display_label || job.label || ''))}
                      </div>
                      <div className="run-card-subtitle">{job.run_id}</div>
                    </div>
                    <StatusBadge status={job.status} />
                  </div>
                  <div className="run-card-actions">
                    {job.module === 'autorunner' ? (
                      <Link
                        to="/metrics"
                        search={{ runId: job.run_id }}
                        className="inline-action"
                      >
                        {t('runCenter.openMetrics')}
                      </Link>
                    ) : null}
                    {job.module === 'wfa' ? (
                      <Link
                        to="/wfa"
                        search={{ runId: job.run_id }}
                        className="inline-action"
                      >
                        {t('runCenter.openWalkForward')}
                      </Link>
                    ) : null}
                  </div>
                </div>
              )),
          )}
        </div>
      </SectionCard>
    </div>
  )
}
