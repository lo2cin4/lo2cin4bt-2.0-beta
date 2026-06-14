#[no_mangle]
/// # Safety
///
/// The caller must pass non-null pointers to contiguous buffers with the
/// lengths implied by `n_time`, `n_strategies`, and their product. Output
/// buffers must be writable for the same computed lengths.
pub unsafe extern "C" fn simulate_trades_batch(
    n_time: usize,
    n_strategies: usize,
    entry_ptr: *const f64,
    exit_ptr: *const f64,
    close_ptr: *const f64,
    open_ptr: *const f64,
    transaction_cost: f64,
    slippage: f64,
    trade_price_mode: i32,
    trade_delay: i32,
    holding_period_days: i32,
    nday_exit_long_days_ptr: *const i32,
    nday_exit_short_days_ptr: *const i32,
    has_non_nday_exit_ptr: *const i32,
    nday_combine_mode_ptr: *const i32,
    positions_out: *mut f64,
    returns_out: *mut f64,
    actions_out: *mut f64,
    equity_out: *mut f64,
) -> i32 {
    if entry_ptr.is_null()
        || exit_ptr.is_null()
        || close_ptr.is_null()
        || open_ptr.is_null()
        || nday_exit_long_days_ptr.is_null()
        || nday_exit_short_days_ptr.is_null()
        || has_non_nday_exit_ptr.is_null()
        || nday_combine_mode_ptr.is_null()
        || positions_out.is_null()
        || returns_out.is_null()
        || actions_out.is_null()
        || equity_out.is_null()
    {
        return 1;
    }

    let total = match n_time.checked_mul(n_strategies) {
        Some(v) => v,
        None => return 2,
    };

    let trade_delay = if trade_delay < 0 {
        0usize
    } else {
        trade_delay as usize
    };
    let holding_days = if holding_period_days < 0 {
        0usize
    } else {
        holding_period_days as usize
    };
    let use_open = trade_price_mode == 1;

    let entry = unsafe { std::slice::from_raw_parts(entry_ptr, total) };
    let exit = unsafe { std::slice::from_raw_parts(exit_ptr, total) };
    let close = unsafe { std::slice::from_raw_parts(close_ptr, n_time) };
    let open = unsafe { std::slice::from_raw_parts(open_ptr, n_time) };
    let nday_exit_long_days =
        unsafe { std::slice::from_raw_parts(nday_exit_long_days_ptr, n_strategies) };
    let nday_exit_short_days =
        unsafe { std::slice::from_raw_parts(nday_exit_short_days_ptr, n_strategies) };
    let has_non_nday_exit =
        unsafe { std::slice::from_raw_parts(has_non_nday_exit_ptr, n_strategies) };
    let nday_combine_mode =
        unsafe { std::slice::from_raw_parts(nday_combine_mode_ptr, n_strategies) };

    let positions = unsafe { std::slice::from_raw_parts_mut(positions_out, total) };
    let returns = unsafe { std::slice::from_raw_parts_mut(returns_out, total) };
    let actions = unsafe { std::slice::from_raw_parts_mut(actions_out, total) };
    let equity = unsafe { std::slice::from_raw_parts_mut(equity_out, total) };

    for idx in 0..total {
        positions[idx] = 0.0;
        returns[idx] = 0.0;
        actions[idx] = 0.0;
        equity[idx] = 0.0;
    }

    for s in 0..n_strategies {
        let mut current_state = 0.0f64;
        let mut eq = 1.0f64;
        let mut open_price = 0.0f64;
        let mut open_eq = 1.0f64;
        let mut hold_count = 0usize;
        let nday_long_days = if nday_exit_long_days[s] < 0 {
            0usize
        } else {
            nday_exit_long_days[s] as usize
        };
        let nday_short_days = if nday_exit_short_days[s] < 0 {
            0usize
        } else {
            nday_exit_short_days[s] as usize
        };
        let has_non_exit = has_non_nday_exit[s] != 0;
        let combine_mode = nday_combine_mode[s]; // 0=none,1=timer_only,2=and,3=or

        for t in 0..n_time {
            let flat_idx = t * n_strategies + s;
            let signal_idx = if t >= trade_delay {
                t - trade_delay
            } else {
                usize::MAX
            };
            let entry_sig = if signal_idx < n_time {
                entry[signal_idx * n_strategies + s]
            } else {
                0.0
            };
            let exit_sig = if signal_idx < n_time {
                exit[signal_idx * n_strategies + s]
            } else {
                0.0
            };

            if t > 0 && current_state != 0.0 && open_price > 0.0 {
                let mark = if use_open { open[t] } else { close[t] };
                let unrealized_return = if current_state > 0.0 {
                    (mark - open_price) / open_price
                } else {
                    (open_price - mark) / open_price
                };
                returns[flat_idx] = unrealized_return;
                equity[flat_idx] = open_eq * (1.0 + unrealized_return) * 100.0;
            } else {
                returns[flat_idx] = 0.0;
                equity[flat_idx] = eq * 100.0;
            }

            let mut long_timer_ready = false;
            let mut short_timer_ready = false;
            if t > 0 && current_state != 0.0 && open_price > 0.0 {
                hold_count += 1;
                if holding_days > 0 && hold_count >= holding_days {
                    long_timer_ready = current_state == 1.0;
                    short_timer_ready = current_state == -1.0;
                }
                if nday_long_days > 0 && current_state == 1.0 && hold_count >= nday_long_days {
                    long_timer_ready = true;
                }
                if nday_short_days > 0 && current_state == -1.0 && hold_count >= nday_short_days {
                    short_timer_ready = true;
                }
            }

            if current_state == 0.0 {
                if entry_sig == 1.0 {
                    current_state = 1.0;
                    actions[flat_idx] = 1.0;
                    open_price = if use_open { open[t] } else { close[t] };
                    eq *= (1.0 - slippage) * (1.0 - transaction_cost);
                    open_eq = eq;
                    hold_count = 0;
                } else if entry_sig == -1.0 {
                    current_state = -1.0;
                    actions[flat_idx] = 1.0;
                    open_price = if use_open { open[t] } else { close[t] };
                    eq *= (1.0 - slippage) * (1.0 - transaction_cost);
                    open_eq = eq;
                    hold_count = 0;
                }
            } else if current_state == 1.0 {
                let should_close_long = if nday_long_days > 0 {
                    match combine_mode {
                        2 => exit_sig == -1.0 && long_timer_ready, // and
                        3 => exit_sig == -1.0 || long_timer_ready, // or
                        1 => long_timer_ready,                     // timer_only
                        _ => {
                            if has_non_exit {
                                exit_sig == -1.0 && long_timer_ready
                            } else {
                                long_timer_ready
                            }
                        }
                    }
                } else {
                    exit_sig == -1.0 || long_timer_ready
                };
                if should_close_long {
                    if equity[flat_idx] > 0.0 {
                        eq = equity[flat_idx] / 100.0;
                    }
                    current_state = 0.0;
                    actions[flat_idx] = 4.0;
                    open_price = 0.0;
                    open_eq = 1.0;
                    eq *= (1.0 - slippage) * (1.0 - transaction_cost);
                    hold_count = 0;
                }
            } else {
                let should_close_short = if nday_short_days > 0 {
                    match combine_mode {
                        2 => exit_sig == 1.0 && short_timer_ready, // and
                        3 => exit_sig == 1.0 || short_timer_ready, // or
                        1 => short_timer_ready,                    // timer_only
                        _ => {
                            if has_non_exit {
                                exit_sig == 1.0 && short_timer_ready
                            } else {
                                short_timer_ready
                            }
                        }
                    }
                } else {
                    exit_sig == 1.0 || short_timer_ready
                };
                if should_close_short {
                    if equity[flat_idx] > 0.0 {
                        eq = equity[flat_idx] / 100.0;
                    }
                    current_state = 0.0;
                    actions[flat_idx] = 4.0;
                    open_price = 0.0;
                    open_eq = 1.0;
                    eq *= (1.0 - slippage) * (1.0 - transaction_cost);
                    hold_count = 0;
                }
            }

            positions[flat_idx] = current_state;
            if current_state == 0.0 || actions[flat_idx] == 1.0 {
                equity[flat_idx] = eq * 100.0;
            }
        }
    }

    0
}

#[no_mangle]
/// # Safety
///
/// The caller must pass non-null pointers to readable `left` and `right`
/// buffers and a writable `out` buffer, each with at least `n` elements.
pub unsafe extern "C" fn signal_binary_mask(
    n: usize,
    op_code: i32,
    left_ptr: *const f64,
    right_ptr: *const f64,
    out_ptr: *mut u8,
) -> i32 {
    if left_ptr.is_null() || right_ptr.is_null() || out_ptr.is_null() {
        return 1;
    }
    let left = unsafe { std::slice::from_raw_parts(left_ptr, n) };
    let right = unsafe { std::slice::from_raw_parts(right_ptr, n) };
    let out = unsafe { std::slice::from_raw_parts_mut(out_ptr, n) };

    for item in out.iter_mut().take(n) {
        *item = 0;
    }
    if n == 0 {
        return 0;
    }

    match op_code {
        1 => {
            for i in 0..n {
                out[i] = if left[i] > right[i] { 1 } else { 0 };
            }
        }
        2 => {
            for i in 0..n {
                out[i] = if left[i] < right[i] { 1 } else { 0 };
            }
        }
        3 => {
            for i in 0..n {
                out[i] = if left[i] >= right[i] { 1 } else { 0 };
            }
        }
        4 => {
            for i in 0..n {
                out[i] = if left[i] <= right[i] { 1 } else { 0 };
            }
        }
        5 => {
            for i in 0..n {
                out[i] = if left[i] == right[i] { 1 } else { 0 };
            }
        }
        6 => {
            for i in 0..n {
                out[i] = if left[i] != right[i] { 1 } else { 0 };
            }
        }
        7 => {
            out[0] = 0;
            for i in 1..n {
                out[i] = if left[i] > right[i] && left[i - 1] <= right[i - 1] {
                    1
                } else {
                    0
                };
            }
        }
        8 => {
            out[0] = 0;
            for i in 1..n {
                out[i] = if left[i] < right[i] && left[i - 1] >= right[i - 1] {
                    1
                } else {
                    0
                };
            }
        }
        _ => return 2,
    }
    0
}

#[no_mangle]
/// # Safety
///
/// The caller must pass non-null pointers to readable input buffers sized by
/// `total_len` or `n_groups` as appropriate. Output buffers must be writable
/// for `n_groups` elements.
pub unsafe extern "C" fn compute_trade_stats_batch(
    total_len: usize,
    n_groups: usize,
    trade_actions_ptr: *const f64,
    trade_returns_ptr: *const f64,
    position_size_ptr: *const f64,
    group_start_ptr: *const usize,
    group_end_ptr: *const usize,
    trade_count_out: *mut f64,
    win_rate_out: *mut f64,
    profit_factor_out: *mut f64,
    avg_trade_return_out: *mut f64,
    max_consecutive_losses_out: *mut f64,
    exposure_time_out: *mut f64,
    max_holding_ratio_out: *mut f64,
) -> i32 {
    if trade_actions_ptr.is_null()
        || trade_returns_ptr.is_null()
        || position_size_ptr.is_null()
        || group_start_ptr.is_null()
        || group_end_ptr.is_null()
        || trade_count_out.is_null()
        || win_rate_out.is_null()
        || profit_factor_out.is_null()
        || avg_trade_return_out.is_null()
        || max_consecutive_losses_out.is_null()
        || exposure_time_out.is_null()
        || max_holding_ratio_out.is_null()
    {
        return 1;
    }

    let trade_actions = unsafe { std::slice::from_raw_parts(trade_actions_ptr, total_len) };
    let trade_returns = unsafe { std::slice::from_raw_parts(trade_returns_ptr, total_len) };
    let position_size = unsafe { std::slice::from_raw_parts(position_size_ptr, total_len) };
    let group_start = unsafe { std::slice::from_raw_parts(group_start_ptr, n_groups) };
    let group_end = unsafe { std::slice::from_raw_parts(group_end_ptr, n_groups) };

    let trade_count = unsafe { std::slice::from_raw_parts_mut(trade_count_out, n_groups) };
    let win_rate = unsafe { std::slice::from_raw_parts_mut(win_rate_out, n_groups) };
    let profit_factor = unsafe { std::slice::from_raw_parts_mut(profit_factor_out, n_groups) };
    let avg_trade_return =
        unsafe { std::slice::from_raw_parts_mut(avg_trade_return_out, n_groups) };
    let max_consecutive_losses =
        unsafe { std::slice::from_raw_parts_mut(max_consecutive_losses_out, n_groups) };
    let exposure_time = unsafe { std::slice::from_raw_parts_mut(exposure_time_out, n_groups) };
    let max_holding_ratio =
        unsafe { std::slice::from_raw_parts_mut(max_holding_ratio_out, n_groups) };

    for g in 0..n_groups {
        let start = group_start[g];
        let end = group_end[g];
        if start >= end || end > total_len {
            return 2;
        }

        let mut trade_count_local = 0.0f64;
        let mut closed_count = 0.0f64;
        let mut wins = 0.0f64;
        let mut profit_sum = 0.0f64;
        let mut loss_sum = 0.0f64;
        let mut trade_sum = 0.0f64;
        let mut consecutive_losses = 0.0f64;
        let mut max_losses = 0.0f64;
        let mut exposure_count = 0.0f64;
        let mut holding_run = 0.0f64;
        let mut max_holding_run = 0.0f64;

        for i in start..end {
            let action = trade_actions[i];
            let ret = trade_returns[i];
            let pos = position_size[i];

            if action == 1.0 {
                trade_count_local += 1.0;
            }

            if !ret.is_nan() {
                trade_sum += ret;
                if ret > 0.0 {
                    profit_sum += ret;
                } else if ret < 0.0 {
                    loss_sum += ret;
                }
            }

            if action == 4.0 && !ret.is_nan() {
                closed_count += 1.0;
                if ret > 0.0 {
                    wins += 1.0;
                    consecutive_losses = 0.0;
                } else if ret < 0.0 {
                    consecutive_losses += 1.0;
                    if consecutive_losses > max_losses {
                        max_losses = consecutive_losses;
                    }
                } else {
                    consecutive_losses = 0.0;
                }
            }

            if !pos.is_nan() && pos != 0.0 {
                exposure_count += 1.0;
                holding_run += 1.0;
                if holding_run > max_holding_run {
                    max_holding_run = holding_run;
                }
            } else {
                holding_run = 0.0;
            }
        }

        let length = (end - start) as f64;
        trade_count[g] = trade_count_local;
        win_rate[g] = if closed_count > 0.0 {
            wins / closed_count
        } else {
            f64::NAN
        };
        profit_factor[g] = if loss_sum < 0.0 {
            profit_sum / loss_sum.abs()
        } else {
            f64::NAN
        };
        avg_trade_return[g] = if length > 0.0 {
            trade_sum / length
        } else {
            f64::NAN
        };
        max_consecutive_losses[g] = max_losses;
        exposure_time[g] = if length > 0.0 {
            (exposure_count / length) * 100.0
        } else {
            f64::NAN
        };
        max_holding_ratio[g] = if length > 0.0 {
            max_holding_run / length
        } else {
            f64::NAN
        };
    }

    0
}
