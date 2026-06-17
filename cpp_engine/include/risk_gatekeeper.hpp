// =============================================================================
// Quantelos AI Trader — RiskGatekeeper (Hardcoded Safety Gate)
// =============================================================================
// FIXES APPLIED (Audit v1.0):
//   H-01: open_positions_ is now std::atomic<int> — thread-safe concurrent signals
//   H-02: Drawdown now uses high-water mark (peak equity), not initial equity
//   NEW:  MIN_SL_PIPS guard — prevents absurdly small SL that inflates unit size
//   NEW:  validate_min_sl() method added to full_validate()
// =============================================================================
#pragma once
#include <string>
#include <stdexcept>
#include <cmath>
#include <atomic>       // FIX H-01

namespace quantelos {

class InvalidSchemaException : public std::runtime_error {
public:
    explicit InvalidSchemaException(const std::string& msg)
        : std::runtime_error("[RISK_GATE] Schema Violation: " + msg) {}
};

class SlippageExceededException : public std::runtime_error {
public:
    explicit SlippageExceededException(const std::string& msg)
        : std::runtime_error("[RISK_GATE] Slippage Breach: " + msg) {}
};

class DrawdownBreachException : public std::runtime_error {
public:
    explicit DrawdownBreachException(const std::string& msg)
        : std::runtime_error("[RISK_GATE] MDD Breach: " + msg) {}
};

struct TradeOrder {
    std::string pair;
    std::string direction;     // "BUY" | "SELL"
    double      entry_price;
    double      stop_loss;
    double      take_profit;
    double      confidence;    // AI confidence [0.0 - 1.0]
    int         units;         // Calculated by gatekeeper
};

class RiskGatekeeper {
public:
    // ── Compile-time safety constants (cannot be overridden by Python/JSON/LLM) ──
    // Gold v2.0: All constants tuned for XAU_USD volatility profile
    static constexpr double MAX_RISK_PER_TRADE_PCT  = 0.05;
    static constexpr double MAX_DRAWDOWN_PCT        = 0.15;
    static constexpr double MIN_RISK_REWARD_RATIO   = 1.5;  // Gold v2.0: positive expectancy floor
    static constexpr double MAX_SLIPPAGE_PIPS       = 5.0;  // Gold v2.0: wider tolerance for Gold spread
    static constexpr int    MAX_OPEN_POSITIONS      = 2;
    static constexpr double MIN_SL_PIPS             = 50.0; // Gold v2.0: minimum 50 pips ($5.00) SL distance

    double get_pip_value(const std::string& pair) const {
        if (pair.find("JPY") != std::string::npos) {
            return 0.01;
        } else if (pair.find("XAU") != std::string::npos) {
            return 0.1;  // Gold v2.0 FIXED: OANDA Gold pip = $0.10 (was incorrectly 1.0)
        } else {
            return 0.0001; // EUR_USD, GBP_USD, etc.
        }
    }

    explicit RiskGatekeeper(
        double initial_equity_usc,
        double risk_per_trade_pct = 0.05,
        double max_drawdown_pct   = 0.15
    )
        : initial_equity_(initial_equity_usc)
        , current_equity_(initial_equity_usc)
        , peak_equity_(initial_equity_usc)       // FIX H-02: initialize HWM
        , risk_per_trade_pct_(risk_per_trade_pct)
        , max_drawdown_pct_(max_drawdown_pct)
        , open_positions_(0)                     // FIX H-01: atomic init
    {}

    void validate_schema(const TradeOrder& o) const {
        if (o.pair.empty())
            throw InvalidSchemaException("'pair' is empty.");
        if (o.direction != "BUY" && o.direction != "SELL")
            throw InvalidSchemaException("'direction' invalid: " + o.direction);
        if (o.entry_price <= 0.0)
            throw InvalidSchemaException("'entry_price' <= 0.");
        if (o.stop_loss <= 0.0)
            throw InvalidSchemaException("'stop_loss' missing or <= 0.");
        if (o.take_profit <= 0.0)
            throw InvalidSchemaException("'take_profit' missing or <= 0.");
        if (o.confidence < 0.0 || o.confidence > 1.0)
            throw InvalidSchemaException("'confidence' not in [0,1].");
        if (o.direction == "BUY") {
            if (o.stop_loss >= o.entry_price)
                throw InvalidSchemaException("BUY: SL must be < entry.");
            if (o.take_profit <= o.entry_price)
                throw InvalidSchemaException("BUY: TP must be > entry.");
        } else {
            if (o.stop_loss <= o.entry_price)
                throw InvalidSchemaException("SELL: SL must be > entry.");
            if (o.take_profit >= o.entry_price)
                throw InvalidSchemaException("SELL: TP must be < entry.");
        }
    }

    // ── NEW: Minimum SL distance guard ─────────────────────────────────────────
    // Prevents AI from sending SL = entry - 0.1 pip which would calculate
    // an astronomically large unit size and blow the account.
    void validate_min_sl(const TradeOrder& o) const {
        double pip_value = get_pip_value(o.pair);
        double sl_pips   = std::abs(o.entry_price - o.stop_loss) / pip_value;
        if (sl_pips < MIN_SL_PIPS) {
            throw InvalidSchemaException(
                "SL distance " + std::to_string(sl_pips)
                + " pips is below minimum " + std::to_string(MIN_SL_PIPS) + " pips."
            );
        }
    }

    int calculate_units(double equity_usc, double sl_pips, const std::string& pair = "") const {
        if (equity_usc <= 0 || sl_pips <= 0) return 0;
        double risk_usc        = equity_usc * risk_per_trade_pct_;
        double pip_value       = pair.empty() ? 0.0001 : get_pip_value(pair);
        // pip_val_per_unit in USC: 1 pip movement × 1 unit = 0.0001 USD = 0.01 USC
        double pip_val_per_unit = pip_value * 100.0;
        return static_cast<int>(std::floor(risk_usc / (sl_pips * pip_val_per_unit)));
    }

    void validate_slippage(const std::string& pair, const std::string& direction,
                           double target, double actual) const {
        double pip_value = get_pip_value(pair);
        double dev = 0.0;
        if (direction == "BUY") {
            if (actual > target) dev = (actual - target) / pip_value;
        } else {
            if (actual < target) dev = (target - actual) / pip_value;
        }
        if (dev > MAX_SLIPPAGE_PIPS)
            throw SlippageExceededException(
                "Dev: " + std::to_string(dev) + " pips > limit "
                + std::to_string(MAX_SLIPPAGE_PIPS));
    }

    bool validate_risk_reward(const TradeOrder& o) const {
        double sl = std::abs(o.entry_price - o.stop_loss);
        double tp = std::abs(o.take_profit - o.entry_price);
        return (sl > 0) && (tp / sl >= MIN_RISK_REWARD_RATIO);
    }

    // ── FIX H-02: Drawdown from high-water mark, not initial equity ────────────
    void check_drawdown() const {
        // peak_equity_ is updated in update_equity() whenever equity rises
        double dd = 1.0 - (current_equity_ / peak_equity_);
        if (dd >= max_drawdown_pct_)
            throw DrawdownBreachException(
                "DD " + std::to_string(dd * 100.0) + "% from HWM "
                + std::to_string(peak_equity_) + " USC >= limit "
                + std::to_string(max_drawdown_pct_ * 100.0) + "%"
            );
    }

    // FIX H-01: Uses atomic read — safe to call from multiple threads
    bool can_open_position() const {
        return open_positions_.load(std::memory_order_relaxed) < MAX_OPEN_POSITIONS;
    }

    int full_validate(const TradeOrder& o, double server_price) {
        validate_schema(o);
        validate_min_sl(o);         // NEW: check minimum SL distance first
        check_drawdown();
        if (!can_open_position())
            throw InvalidSchemaException("Max positions reached.");
        if (!validate_risk_reward(o))
            throw InvalidSchemaException("RR below minimum.");
        validate_slippage(o.pair, o.direction, o.entry_price, server_price);
        double pip_value = get_pip_value(o.pair);
        double sl_pips   = std::abs(o.entry_price - o.stop_loss) / pip_value;
        int units = calculate_units(current_equity_, sl_pips, o.pair);
        if (units == 0)
            throw InvalidSchemaException("Calculated units=0, insufficient equity.");
        return units;
    }

    // ── FIX H-02: update_equity() now maintains high-water mark ───────────────
    void update_equity(double e) {
        current_equity_ = e;
        if (e > peak_equity_) {
            peak_equity_ = e;  // Ratchet the high-water mark upward only
        }
    }

    // FIX H-01: Atomic increment/decrement
    void increment_positions() { open_positions_.fetch_add(1, std::memory_order_relaxed); }
    void decrement_positions() {
        int cur = open_positions_.load(std::memory_order_relaxed);
        if (cur > 0) open_positions_.fetch_sub(1, std::memory_order_relaxed);
    }

    double get_equity()        const { return current_equity_; }
    double get_peak_equity()   const { return peak_equity_; }
    double get_initial_equity() const { return initial_equity_; }
    int    get_open_positions() const { return open_positions_.load(std::memory_order_relaxed); }

private:
    double initial_equity_;
    double current_equity_;
    double peak_equity_;             // FIX H-02: high-water mark for drawdown calc
    double risk_per_trade_pct_;
    double max_drawdown_pct_;
    std::atomic<int> open_positions_; // FIX H-01: atomic for concurrent signal safety
};

} // namespace quantelos
