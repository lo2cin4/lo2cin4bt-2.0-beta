import { describe, expect, it } from 'vitest'

import {
  isUnsafeStrategyRuleValue,
  localizeStrategyRuleValue,
  localizeStrategySummaryValue,
  safeStrategyRuleValue,
} from './StrategyRulesPanel'
import { benchmarkDisplayLabel, formatStrategyParams, strategyCandidateDisplayLabel } from '../uiVocabulary'

describe('strategy rule display safety', () => {
  it('suppresses raw backend placeholders and generated frame names', () => {
    const unsafeValues = [
      'rank_by=momentum; top_n=-; position_limit=-',
      'select top - by -',
      'target_frame: target_weight_th10_hold50, target_weight_th10_hold100',
      '{"op":"lt","left":"close"}',
      'frame: { param_ref: target_frame }',
    ]

    for (const value of unsafeValues) {
      expect(isUnsafeStrategyRuleValue(value)).toBe(true)
      expect(safeStrategyRuleValue(value, 'Configured strategy')).toBe('Configured strategy')
    }
  })

  it('keeps readable strategy rules and compact parameter ranges', () => {
    expect(isUnsafeStrategyRuleValue('short_ma crosses above long_ma')).toBe(false)
    expect(safeStrategyRuleValue('n: 10 to 15 step 1; m: 50 to 250 step 50')).toBe(
      'n: 10 to 15 step 1; m: 50 to 250 step 50',
    )
    expect(
      localizeStrategyRuleValue(
        'parameter_domain_label',
        'target_frame: target_weight_th10_hold50, target_weight_th10_hold100',
        'en',
        'Configured strategy',
      ),
    ).toBe('Configured strategy')
  })

  it('localizes MMFI parameter-matrix rules without translating tickers', () => {
    expect(
      localizeStrategyRuleValue(
        'entry_rule',
        'When MMFI close is below the selected threshold, switch from QQQ to TQQQ on the next trading day open.',
        'zh-Hant',
      ),
    ).toBe('當 MMFI 收盤值低於所選閾值，下一個交易日開盤由 QQQ 切換至 TQQQ。')
    expect(
      localizeStrategyRuleValue(
        'exit_rule',
        'Hold TQQQ for the selected number of trading days; if another signal appears before exit, extend the TQQQ window by the selected holding period from the new next-open entry date.',
        'zh-Hant',
      ),
    ).toBe('持有 TQQQ 所選交易日數；若平倉前再次出現訊號，則由新的下一開盤入場日起重新延長 TQQQ 持有期。')
    expect(
      localizeStrategyRuleValue(
        'parameter_domain_label',
        'mmfi_threshold: 10 to 15 step 1; hold_days: 50, 100, 150, 200, 250',
        'zh-Hant',
      ),
    ).toBe('MMFI 閾值：10 至 15 每次 1； 持有交易日：50, 100, 150, 200, 250')
  })

  it('localizes benchmark and selected candidate labels for zh-Hant pages', () => {
    expect(benchmarkDisplayLabel('QQQ Buy & Hold (adjusted open-to-open)', 'Benchmark', 'zh-Hant')).toBe(
      'QQQ 買入並持有（調整後開盤到開盤）',
    )
    expect(strategyCandidateDisplayLabel('QQQ-TQQQ | Mmfi Open Reset Parameter | mmfi_threshold=12 | hold_days=200', 'zh-Hant')).toBe(
      'QQQ-TQQQ | MMFI 開盤切換參數 | MMFI 閾值=12 | 持有交易日=200',
    )
    expect(formatStrategyParams({ hold_days: 200, mmfi_threshold: 12 }, 'zh-Hant')).toBe(
      '持有交易日=200 | MMFI 閾值=12',
    )
  })

  it('does not infer execution wording from the entry rule', () => {
    const expected = localizeStrategyRuleValue('execution_label', 'signal bar at open', 'zh-Hant')
    expect(
      localizeStrategySummaryValue(
        {
          execution_label: 'signal bar at open',
          entry_rule: 'When MMFI close is below the selected threshold, switch from QQQ to TQQQ on the next trading day open.',
        },
        'execution_label',
        'zh-Hant',
      ),
    ).toBe(expected)
  })

  it('uses backend-provided display fields before frontend fallback text', () => {
    expect(
      localizeStrategySummaryValue(
        {
          execution_label: 'signal bar at open',
          display: {
            execution: {
              en: 'backend open/close label',
              zh_Hant: '後端指定執行方式',
            },
          },
        },
        'execution_label',
        'zh-Hant',
      ),
    ).toBe('後端指定執行方式')
  })
})
