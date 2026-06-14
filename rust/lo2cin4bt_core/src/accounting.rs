use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};
use thiserror::Error;

#[derive(Debug, Error, PartialEq)]
pub enum AccountingError {
    #[error("starting equity must be positive")]
    InvalidStartingEquity,
    #[error("cost rate cannot be negative")]
    InvalidCostRate,
    #[error("max gross exposure must be positive")]
    InvalidMaxGrossExposure,
    #[error("non-finite value in {field} for {asset}")]
    NonFiniteValue { field: &'static str, asset: String },
    #[error("negative target weight for {0} is not allowed in long-only accounting")]
    NegativeWeightLongOnly(String),
    #[error("target gross exposure {actual:.6} exceeds configured max {limit:.6}")]
    GrossExposureExceeded { actual: f64, limit: f64 },
    #[error("accounting input requires at least one checkpoint")]
    EmptyCheckpoints,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AccountingConfig {
    pub starting_equity: f64,
    pub cost_rate: f64,
    pub max_gross_exposure: f64,
    pub allow_short: bool,
}

impl Default for AccountingConfig {
    fn default() -> Self {
        Self {
            starting_equity: 100.0,
            cost_rate: 0.0,
            max_gross_exposure: 1.0,
            allow_short: false,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CheckpointInput {
    pub time: String,
    #[serde(default = "default_rebalance")]
    pub rebalance: bool,
    pub returns: BTreeMap<String, f64>,
    pub target_weights: BTreeMap<String, f64>,
}

fn default_rebalance() -> bool {
    true
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AccountingInput {
    pub config: AccountingConfig,
    pub checkpoints: Vec<CheckpointInput>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AccountingEvent {
    pub time: String,
    pub equity_before_trade: f64,
    pub equity_after_trade: f64,
    pub portfolio_return: f64,
    pub turnover: f64,
    pub cost_drag: f64,
    pub cash_weight: f64,
    pub gross_exposure: f64,
    pub active_positions: usize,
    pub target_weights: BTreeMap<String, f64>,
    pub drift_weights: BTreeMap<String, f64>,
    pub contribution: BTreeMap<String, f64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AccountingSummary {
    pub start_equity: f64,
    pub final_equity: f64,
    pub total_return: f64,
    pub checkpoints: usize,
    pub active_rebalances: usize,
    pub average_turnover: f64,
    pub average_gross_exposure: f64,
    pub events: Vec<AccountingEvent>,
}

pub fn run_accounting(input: AccountingInput) -> Result<AccountingSummary, AccountingError> {
    validate_config(&input.config)?;
    if input.checkpoints.is_empty() {
        return Err(AccountingError::EmptyCheckpoints);
    }

    let mut equity = input.config.starting_equity;
    let start_equity = equity;
    let mut previous_weights: BTreeMap<String, f64> = BTreeMap::new();
    let mut previous_cash_weight = 1.0;
    let mut events = Vec::with_capacity(input.checkpoints.len());
    let mut active_rebalances = 0usize;
    let mut turnover_sum = 0.0;
    let mut gross_sum = 0.0;

    for checkpoint in input.checkpoints {
        validate_checkpoint(&checkpoint, &input.config)?;
        let assets = asset_union(
            &previous_weights,
            &checkpoint.returns,
            &checkpoint.target_weights,
        );

        let equity_before_return = equity;
        let mut asset_values: BTreeMap<String, f64> = BTreeMap::new();
        let mut contribution = BTreeMap::new();
        let mut pre_trade_equity = equity_before_return * previous_cash_weight;
        for asset in &assets {
            let previous_weight = *previous_weights.get(asset).unwrap_or(&0.0);
            let asset_return = *checkpoint.returns.get(asset).unwrap_or(&0.0);
            let value_before = equity_before_return * previous_weight;
            let value_after = value_before * (1.0 + asset_return);
            asset_values.insert(asset.clone(), value_after);
            contribution.insert(asset.clone(), previous_weight * asset_return);
            pre_trade_equity += value_after;
        }

        let portfolio_return = if equity_before_return > 0.0 {
            pre_trade_equity / equity_before_return - 1.0
        } else {
            0.0
        };

        let drift_weights = if pre_trade_equity > 0.0 {
            asset_values
                .iter()
                .map(|(asset, value)| (asset.clone(), value / pre_trade_equity))
                .collect::<BTreeMap<_, _>>()
        } else {
            BTreeMap::new()
        };

        let target_weights = if checkpoint.rebalance {
            normalized_target_weights(&checkpoint.target_weights, &input.config)?
        } else {
            drift_weights
                .iter()
                .filter_map(|(asset, value)| {
                    if value.abs() > 1e-12 {
                        Some((asset.clone(), *value))
                    } else {
                        None
                    }
                })
                .collect::<BTreeMap<_, _>>()
        };
        let turnover = turnover_between(&drift_weights, &target_weights, &assets);
        let cost_drag = turnover * input.config.cost_rate;
        let equity_after_trade = pre_trade_equity * (1.0 - cost_drag);
        let gross_exposure = target_weights
            .values()
            .map(|value| value.abs())
            .sum::<f64>();
        let cash_weight = 1.0 - target_weights.values().sum::<f64>();
        let active_positions = target_weights
            .values()
            .filter(|value| value.abs() > 1e-12)
            .count();

        if turnover > 1e-12 {
            active_rebalances += 1;
        }
        turnover_sum += turnover;
        gross_sum += gross_exposure;

        events.push(AccountingEvent {
            time: checkpoint.time,
            equity_before_trade: pre_trade_equity,
            equity_after_trade,
            portfolio_return,
            turnover,
            cost_drag,
            cash_weight,
            gross_exposure,
            active_positions,
            target_weights: target_weights.clone(),
            drift_weights,
            contribution,
        });

        equity = equity_after_trade;
        previous_cash_weight = cash_weight;
        previous_weights = target_weights;
    }

    let checkpoints = events.len();
    Ok(AccountingSummary {
        start_equity,
        final_equity: equity,
        total_return: equity / start_equity - 1.0,
        checkpoints,
        active_rebalances,
        average_turnover: turnover_sum / checkpoints as f64,
        average_gross_exposure: gross_sum / checkpoints as f64,
        events,
    })
}

fn validate_config(config: &AccountingConfig) -> Result<(), AccountingError> {
    if !config.starting_equity.is_finite() || config.starting_equity <= 0.0 {
        return Err(AccountingError::InvalidStartingEquity);
    }
    if !config.cost_rate.is_finite() || config.cost_rate < 0.0 {
        return Err(AccountingError::InvalidCostRate);
    }
    if !config.max_gross_exposure.is_finite() || config.max_gross_exposure <= 0.0 {
        return Err(AccountingError::InvalidMaxGrossExposure);
    }
    Ok(())
}

fn validate_checkpoint(
    checkpoint: &CheckpointInput,
    config: &AccountingConfig,
) -> Result<(), AccountingError> {
    for (asset, value) in &checkpoint.returns {
        if !value.is_finite() {
            return Err(AccountingError::NonFiniteValue {
                field: "returns",
                asset: asset.clone(),
            });
        }
    }
    for (asset, value) in &checkpoint.target_weights {
        if !value.is_finite() {
            return Err(AccountingError::NonFiniteValue {
                field: "target_weights",
                asset: asset.clone(),
            });
        }
        if !config.allow_short && *value < -1e-12 {
            return Err(AccountingError::NegativeWeightLongOnly(asset.clone()));
        }
    }
    Ok(())
}

fn normalized_target_weights(
    target_weights: &BTreeMap<String, f64>,
    config: &AccountingConfig,
) -> Result<BTreeMap<String, f64>, AccountingError> {
    let mut out = BTreeMap::new();
    for (asset, value) in target_weights {
        if value.abs() > 1e-12 {
            out.insert(asset.clone(), *value);
        }
    }
    let gross = out.values().map(|value| value.abs()).sum::<f64>();
    if gross > config.max_gross_exposure + 1e-10 {
        return Err(AccountingError::GrossExposureExceeded {
            actual: gross,
            limit: config.max_gross_exposure,
        });
    }
    Ok(out)
}

fn asset_union(
    previous_weights: &BTreeMap<String, f64>,
    returns: &BTreeMap<String, f64>,
    target_weights: &BTreeMap<String, f64>,
) -> BTreeSet<String> {
    previous_weights
        .keys()
        .chain(returns.keys())
        .chain(target_weights.keys())
        .cloned()
        .collect()
}

fn turnover_between(
    drift_weights: &BTreeMap<String, f64>,
    target_weights: &BTreeMap<String, f64>,
    assets: &BTreeSet<String>,
) -> f64 {
    assets
        .iter()
        .map(|asset| {
            let drift = *drift_weights.get(asset).unwrap_or(&0.0);
            let target = *target_weights.get(asset).unwrap_or(&0.0);
            (target - drift).abs()
        })
        .sum()
}

#[cfg(test)]
mod tests {
    use super::*;
    use approx::assert_abs_diff_eq;

    fn weights(rows: &[(&str, f64)]) -> BTreeMap<String, f64> {
        rows.iter()
            .map(|(key, value)| ((*key).to_string(), *value))
            .collect()
    }

    #[test]
    fn fixed_weight_rebalance_computes_drift_turnover() {
        let input = AccountingInput {
            config: AccountingConfig {
                starting_equity: 100.0,
                cost_rate: 0.0,
                max_gross_exposure: 1.0,
                allow_short: false,
            },
            checkpoints: vec![
                CheckpointInput {
                    time: "2024-01-02".to_string(),
                    rebalance: true,
                    returns: BTreeMap::new(),
                    target_weights: weights(&[("AAA", 0.6), ("BBB", 0.4)]),
                },
                CheckpointInput {
                    time: "2025-01-02".to_string(),
                    rebalance: true,
                    returns: weights(&[("AAA", 0.20), ("BBB", 0.0)]),
                    target_weights: weights(&[("AAA", 0.6), ("BBB", 0.4)]),
                },
            ],
        };

        let summary = run_accounting(input).unwrap();

        assert_abs_diff_eq!(summary.events[0].turnover, 1.0, epsilon = 1e-12);
        assert!(summary.events[1].turnover > 0.08);
        assert!(summary.final_equity > 100.0);
        assert_eq!(summary.active_rebalances, 2);
    }

    #[test]
    fn rotation_from_one_full_position_to_another_has_two_way_turnover() {
        let input = AccountingInput {
            config: AccountingConfig::default(),
            checkpoints: vec![
                CheckpointInput {
                    time: "2024-01-02".to_string(),
                    rebalance: true,
                    returns: BTreeMap::new(),
                    target_weights: weights(&[("VOO", 1.0)]),
                },
                CheckpointInput {
                    time: "2024-02-01".to_string(),
                    rebalance: true,
                    returns: weights(&[("VOO", 0.10), ("GLD", 0.0)]),
                    target_weights: weights(&[("GLD", 1.0)]),
                },
            ],
        };

        let summary = run_accounting(input).unwrap();

        assert_abs_diff_eq!(summary.events[0].turnover, 1.0, epsilon = 1e-12);
        assert_abs_diff_eq!(summary.events[1].turnover, 2.0, epsilon = 1e-12);
        assert_abs_diff_eq!(summary.events[1].cash_weight, 0.0, epsilon = 1e-12);
    }

    #[test]
    fn long_only_accounting_rejects_short_weight() {
        let input = AccountingInput {
            config: AccountingConfig::default(),
            checkpoints: vec![CheckpointInput {
                time: "2024-01-02".to_string(),
                rebalance: true,
                returns: BTreeMap::new(),
                target_weights: weights(&[("QQQ", -1.0)]),
            }],
        };

        assert!(matches!(
            run_accounting(input),
            Err(AccountingError::NegativeWeightLongOnly(asset)) if asset == "QQQ"
        ));
    }

    #[test]
    fn gross_exposure_is_fail_fast() {
        let input = AccountingInput {
            config: AccountingConfig::default(),
            checkpoints: vec![CheckpointInput {
                time: "2024-01-02".to_string(),
                rebalance: true,
                returns: BTreeMap::new(),
                target_weights: weights(&[("AAA", 0.7), ("BBB", 0.7)]),
            }],
        };

        assert!(matches!(
            run_accounting(input),
            Err(AccountingError::GrossExposureExceeded { .. })
        ));
    }

    #[test]
    fn non_rebalance_checkpoint_drifts_without_turnover() {
        let input = AccountingInput {
            config: AccountingConfig::default(),
            checkpoints: vec![
                CheckpointInput {
                    time: "2024-01-02".to_string(),
                    rebalance: true,
                    returns: BTreeMap::new(),
                    target_weights: weights(&[("AAA", 0.6), ("BBB", 0.4)]),
                },
                CheckpointInput {
                    time: "2024-01-03".to_string(),
                    rebalance: false,
                    returns: weights(&[("AAA", 0.20), ("BBB", 0.0)]),
                    target_weights: BTreeMap::new(),
                },
            ],
        };

        let summary = run_accounting(input).unwrap();

        assert_abs_diff_eq!(summary.events[1].turnover, 0.0, epsilon = 1e-12);
        assert!(summary.events[1].target_weights["AAA"] > 0.6);
        assert_abs_diff_eq!(summary.events[1].cash_weight, 0.0, epsilon = 1e-12);
        assert_eq!(summary.active_rebalances, 1);
    }
}
