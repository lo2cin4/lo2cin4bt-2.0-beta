import { create } from 'zustand'
import { persist } from 'zustand/middleware'

import { DEFAULT_LANGUAGE, type Language } from './i18n'

const sameIds = (left: string[], right: string[]) =>
  left.length === right.length && left.every((value, index) => value === right[index])

type AppState = {
  selectedMetricsRunId: string
  selectedCategory: string
  selectedBacktestId: string
  selectedWfaRunId: string
  parameterMatrixSearchSource: string
  pinnedMetricsRunIds: string[]
  archivedMetricsRunIds: string[]
  language: Language
  benchmarkVisible: boolean
  shareMosaicMode: boolean
  batchIds: string[]
  setSelectedMetricsRunId: (value: string) => void
  setSelectedCategory: (value: string) => void
  setSelectedBacktestId: (value: string) => void
  setSelectedWfaRunId: (value: string) => void
  setParameterMatrixSearchSource: (value: string) => void
  togglePinnedMetricsRunId: (value: string) => void
  toggleArchivedMetricsRunId: (value: string) => void
  setLanguage: (value: Language) => void
  setBenchmarkVisible: (value: boolean) => void
  setShareMosaicMode: (value: boolean) => void
  addBatchId: (value: string) => void
  removeBatchId: (value: string) => void
  replaceBatchIds: (value: string[]) => void
}

export const useAppStore = create<AppState>()(
  persist(
    (set) => ({
      selectedMetricsRunId: '',
      selectedCategory: 'top_20_sharpe',
      selectedBacktestId: '',
      selectedWfaRunId: '',
      parameterMatrixSearchSource: 'all_existing_results',
      pinnedMetricsRunIds: [],
      archivedMetricsRunIds: [],
      language: DEFAULT_LANGUAGE,
      benchmarkVisible: true,
      shareMosaicMode: false,
      batchIds: [],
      setSelectedMetricsRunId: (value) =>
        set((state) => (state.selectedMetricsRunId === value ? state : { selectedMetricsRunId: value })),
      setSelectedCategory: (value) =>
        set((state) => (state.selectedCategory === value ? state : { selectedCategory: value })),
      setSelectedBacktestId: (value) =>
        set((state) => (state.selectedBacktestId === value ? state : { selectedBacktestId: value })),
      setSelectedWfaRunId: (value) =>
        set((state) => (state.selectedWfaRunId === value ? state : { selectedWfaRunId: value })),
      setParameterMatrixSearchSource: (value) =>
        set((state) => (state.parameterMatrixSearchSource === value ? state : { parameterMatrixSearchSource: value })),
      togglePinnedMetricsRunId: (value) =>
        set((state) => {
          const next = state.pinnedMetricsRunIds.includes(value)
            ? state.pinnedMetricsRunIds.filter((item) => item !== value)
            : [value, ...state.pinnedMetricsRunIds]
          return { pinnedMetricsRunIds: next }
        }),
      toggleArchivedMetricsRunId: (value) =>
        set((state) => {
          const next = state.archivedMetricsRunIds.includes(value)
            ? state.archivedMetricsRunIds.filter((item) => item !== value)
            : [value, ...state.archivedMetricsRunIds]
          return { archivedMetricsRunIds: next }
        }),
      setLanguage: (value) =>
        set((state) => (state.language === value ? state : { language: value })),
      setBenchmarkVisible: (value) =>
        set((state) => (state.benchmarkVisible === value ? state : { benchmarkVisible: value })),
      setShareMosaicMode: (value) =>
        set((state) => (state.shareMosaicMode === value ? state : { shareMosaicMode: value })),
      addBatchId: (value) =>
        set((state) => ({
          batchIds: state.batchIds.includes(value)
            ? state.batchIds
            : [value, ...state.batchIds].slice(0, 8),
        })),
      removeBatchId: (value) =>
        set((state) => ({
          batchIds: state.batchIds.filter((item) => item !== value),
        })),
      replaceBatchIds: (value) =>
        set((state) => {
          const nextBatchIds = Array.from(new Set(value)).slice(0, 8)
          return sameIds(state.batchIds, nextBatchIds) ? state : { batchIds: nextBatchIds }
        }),
    }),
    {
      name: 'lo2cin4bt-app-store-v5',
      version: 9,
      migrate: (persistedState: any, version) => {
        if (!persistedState || version === 9) {
          return persistedState
        }
        return {
          ...persistedState,
          selectedMetricsRunId: persistedState.selectedMetricsRunId || '',
          selectedCategory: persistedState.selectedCategory || 'run-center',
          selectedBacktestId: persistedState.selectedBacktestId || '',
          selectedWfaRunId: persistedState.selectedWfaRunId || '',
          parameterMatrixSearchSource: persistedState.parameterMatrixSearchSource || 'all_existing_results',
          language: persistedState.language || 'zh-Hant',
          benchmarkVisible: typeof persistedState.benchmarkVisible === 'boolean' ? persistedState.benchmarkVisible : true,
          shareMosaicMode: typeof persistedState.shareMosaicMode === 'boolean' ? persistedState.shareMosaicMode : false,
          batchIds: Array.isArray(persistedState.batchIds) ? persistedState.batchIds : [],
          pinnedMetricsRunIds: Array.isArray(persistedState.pinnedMetricsRunIds) ? persistedState.pinnedMetricsRunIds : [],
          archivedMetricsRunIds: Array.isArray(persistedState.archivedMetricsRunIds) ? persistedState.archivedMetricsRunIds : [],
        }
      },
      partialize: (state) => ({
        selectedMetricsRunId: state.selectedMetricsRunId,
        selectedCategory: state.selectedCategory,
        selectedBacktestId: state.selectedBacktestId,
        selectedWfaRunId: state.selectedWfaRunId,
        parameterMatrixSearchSource: state.parameterMatrixSearchSource,
        pinnedMetricsRunIds: state.pinnedMetricsRunIds,
        archivedMetricsRunIds: state.archivedMetricsRunIds,
        language: state.language,
        benchmarkVisible: state.benchmarkVisible,
        shareMosaicMode: state.shareMosaicMode,
        batchIds: state.batchIds,
      }),
    },
  ),
)
