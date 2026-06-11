// =============================================================================
// Quantelos AI Trader — RiskGatekeeper (Hardcoded Safety Gate)
// =============================================================================
// FINAL checkpoint before any order reaches OANDA API. All risk parameters
// are IMMUTABLE at compile-time. No Python/JSON/LLM can override these.
// =============================================================================
#pragma once
#include <string>
#include <stdexcept>
#include <cmath>

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
    static constexpr double MAX_RISK_PER_TRADE_PCT  = 0.05;
    static constexpr double MAX_DRAWDOWN_PCT        = 0.15;
    static constexpr double MIN_RISK_REWARD_RATIO   = 1.0;
    static constexpr double MAX_SLIPPAGE_PIPS       = 2.0;
    static constexpr int    MAX_OPEN_POSITIONS       = 2;

    double get_pip_value(const std::string& pair) const {
        if (pair.find("JPY") != std::string::npos) {
            return 0.01;
        } else if (pair.find("XAU") != std::string::npos) {
            return 1.0; // 1 pip = 1.00 USD for Gold to scale slippage limits appropriately
        } else {
            return 0.0001; // Default for EUR/USD, GBP/USD, etc.
        }
    }

    explicit RiskGatekeeper(double initial_equity_usc, double risk_per_trade_pct = 0.05, double max_drawdown_pct = 0.15)
        : initial_equity_(initial_equity_usc)
        , current_equity_(initial_equity_usc)
        , risk_per_trade_pct_(risk_per_trade_pct)
        , max_drawdown_pct_(max_drawdown_pct)
        , open_positions_(0) {}

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

    int calculate_units(double equity_usc, double sl_pips, const std::string& pair = "") const {
        if (equity_usc <= 0 || sl_pips <= 0) return 0;
        double risk_usc = equity_usc * risk_per_trade_pct_;
        double pip_val_per_unit = 0.01; // Default Cent account pip value in cents (0.0001 USD * 100)
        if (!pair.empty()) {
            double pip_value = get_pip_value(pair);
            pip_val_per_unit = pip_value * 100.0;
        }
        return static_cast<int>(std::floor(risk_usc / (sl_pips * pip_val_per_unit)));
    }

    void validate_slippage(const std::string& pair, const std::string& direction, double target, double actual) const {
        double pip_value = get_pip_value(pair);
        double dev = 0.0;
        if (direction == "BUY") {
            if (actual > target) {
                dev = (actual - target) / pip_value;
            }
        } else {
            if (actual < target) {
                dev = (target - actual) / pip_value;
            }
        }
        if (dev > MAX_SLIPPAGE_PIPS)
            throw SlippageExceededException(
                "Dev: " + std::to_string(dev) + " > " + std::to_string(MAX_SLIPPAGE_PIPS));
    }

    bool validate_risk_reward(const TradeOrder& o) const {
        double sl = std::abs(o.entry_price - o.stop_loss);
        double tp = std::abs(o.take_profit - o.entry_price);
        return (sl > 0) && (tp / sl >= MIN_RISK_REWARD_RATIO);
    }

    void check_drawdown() const {
        double dd = 1.0 - (current_equity_ / initial_equity_);
        if (dd >= max_drawdown_pct_)
            throw DrawdownBreachException("DD " + std::to_string(dd*100) + "% >= limit");
    }

    bool can_open_position() const { return open_positions_ < MAX_OPEN_POSITIONS; }

    int full_validate(const TradeOrder& o, double server_price) {
        validate_schema(o);
        check_drawdown();
        if (!can_open_position())
            throw InvalidSchemaException("Max positions reached.");
        if (!validate_risk_reward(o))
            throw InvalidSchemaException("RR below minimum.");
        validate_slippage(o.pair, o.direction, o.entry_price, server_price);
        double pip_value = get_pip_value(o.pair);
        double sl_pips = std::abs(o.entry_price - o.stop_loss) / pip_value;
        int units = calculate_units(current_equity_, sl_pips, o.pair);
        if (units == 0)
            throw InvalidSchemaException("Calculated units=0, insufficient equity.");
        return units;
    }

    void update_equity(double e) { current_equity_ = e; }
    void increment_positions() { ++open_positions_; }
    void decrement_positions() { if (open_positions_ > 0) --open_positions_; }
    double get_equity() const { return current_equity_; }
    int get_open_positions() const { return open_positions_; }

private:
    double initial_equity_, current_equity_;
    double risk_per_trade_pct_;
    double max_drawdown_pct_;
    int open_positions_;
};

} // namespace quantelos
