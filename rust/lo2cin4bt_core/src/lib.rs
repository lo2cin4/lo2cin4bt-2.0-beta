//! Deterministic Rust core for lo2cin4bt.
//!
//! The first slice keeps Rust independent from the Python runtime. Python can
//! keep orchestrating data loading and UI payloads while this crate owns typed
//! contracts and the sequential accounting state machine.

pub mod accounting;
pub mod config;

pub use accounting::{
    run_accounting, AccountingConfig, AccountingEvent, AccountingInput, AccountingSummary,
    CheckpointInput,
};
pub use config::{FactorCompositeMethod, FactorPreprocessOp, StrategyModeId, WorkflowId};
