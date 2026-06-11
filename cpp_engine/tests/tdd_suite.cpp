// =============================================================================
// Quantelos AI Trader — TDD Test Suite (3 Fatal Scenarios)
// =============================================================================
// These tests MUST PASS before C++ connects to any OANDA server.
// Compile: g++ -std=c++17 -I../include tests/tdd_suite.cpp -o tdd_runner
// =============================================================================
#include "risk_gatekeeper.hpp"
#include <iostream>
#include <cassert>
#include <string>

using namespace quantelos;

static int passed = 0, failed = 0;

#define TEST(name) void name()
#define RUN(name) do { \
    std::cout << "  [RUN ] " << #name << std::flush; \
    try { name(); ++passed; std::cout << " ✓ PASS\n"; } \
    catch (const std::exception& e) { ++failed; std::cout << " ✗ FAIL: " << e.what() << "\n"; } \
} while(0)

// ─── TDD_01: Math Constraint ────────────────────────────────────────────────
// Input : Equity=1200 USC, Risk=5%, SL=15 pips
// PASS  : calculate_units returns a reasonable small value (NOT 1.0 lot)
TEST(tdd01_math_constraint) {
    RiskGatekeeper gate(1200.0);
    int units = gate.calculate_units(1200.0, 15.0);
    // risk_amount = 1200 * 0.05 = 60 USC
    // units = 60 / (15 * 0.01) = 60 / 0.15 = 400
    assert(units == 400 && "TDD_01: Expected 400 units for 1200 USC, 5%, 15 pip SL");
    assert(units != 10000 && "TDD_01: Must NOT return a standard lot equivalent");
}

// ─── TDD_02: Hallucination Trap ──────────────────────────────────────────────
// Input : Corrupted JSON — no SL/TP, wrong confidence type
// PASS  : Throws InvalidSchemaException safely (no segfault)
TEST(tdd02_hallucination_trap) {
    RiskGatekeeper gate(1200.0);
    TradeOrder bad_order;
    bad_order.pair = "EUR_USD";
    bad_order.direction = "EXECUTE_TRADE";  // Invalid direction
    bad_order.entry_price = 1.08500;
    bad_order.stop_loss = 0.0;              // Missing SL
    bad_order.take_profit = 0.0;            // Missing TP
    bad_order.confidence = 999.0;           // Out of range
    bad_order.units = 0;

    bool caught = false;
    try {
        gate.validate_schema(bad_order);
    } catch (const InvalidSchemaException&) {
        caught = true;
    }
    assert(caught && "TDD_02: Must throw InvalidSchemaException for corrupted payload");
}

TEST(tdd02b_missing_sl) {
    RiskGatekeeper gate(1200.0);
    TradeOrder order;
    order.pair = "EUR_USD";
    order.direction = "BUY";
    order.entry_price = 1.08500;
    order.stop_loss = 0.0;       // Missing!
    order.take_profit = 1.08800;
    order.confidence = 0.85;
    order.units = 0;

    bool caught = false;
    try { gate.validate_schema(order); }
    catch (const InvalidSchemaException&) { caught = true; }
    assert(caught && "TDD_02b: Missing SL must trigger exception");
}

// ─── TDD_03: Slippage Guard ─────────────────────────────────────────────────
// Input : Target=1.08500, Server=1.08550 (5 pip deviation > 2 pip limit)
// PASS  : Throws SlippageExceededException
TEST(tdd03_slippage_guard) {
    RiskGatekeeper gate(1200.0);
    bool caught = false;
    try {
        gate.validate_slippage("EUR_USD", "BUY", 1.08500, 1.08550);  // 5 pip deviation (negative slippage)
    } catch (const SlippageExceededException&) {
        caught = true;
    }
    assert(caught && "TDD_03: 5-pip slippage must trigger exception");
}

TEST(tdd03b_acceptable_slippage) {
    RiskGatekeeper gate(1200.0);
    bool caught = false;
    try {
        gate.validate_slippage("EUR_USD", "BUY", 1.08500, 1.08510);  // 1 pip — acceptable
    } catch (const SlippageExceededException&) {
        caught = true;
    }
    assert(!caught && "TDD_03b: 1-pip slippage should be accepted");
}

// ─── Additional Safety Tests ─────────────────────────────────────────────────
TEST(tdd04_drawdown_breach) {
    RiskGatekeeper gate(1200.0);
    gate.update_equity(1000.0);  // 16.7% drawdown > 15% limit
    bool caught = false;
    try { gate.check_drawdown(); }
    catch (const DrawdownBreachException&) { caught = true; }
    assert(caught && "TDD_04: Drawdown > 15% must trigger emergency halt");
}

TEST(tdd05_risk_reward_validation) {
    RiskGatekeeper gate(1200.0);
    TradeOrder order;
    order.pair = "EUR_USD";
    order.direction = "BUY";
    order.entry_price = 1.08500;
    order.stop_loss = 1.08400;    // 10 pip SL
    order.take_profit = 1.08450;  // 5 pip TP — RR 1:0.5 (below minimum 1:1)
    order.confidence = 0.8;
    assert(!gate.validate_risk_reward(order) && "TDD_05: RR 1:0.5 must be rejected");

    order.take_profit = 1.08600;  // 10 pip TP — RR 1:1 (acceptable)
    assert(gate.validate_risk_reward(order) && "TDD_05: RR 1:1 must be accepted");
}

TEST(tdd06_max_positions) {
    RiskGatekeeper gate(1200.0);
    assert(gate.can_open_position());
    gate.increment_positions();
    gate.increment_positions();
    assert(!gate.can_open_position() && "TDD_06: Max 2 positions enforced");
    gate.decrement_positions();
    assert(gate.can_open_position());
}

TEST(tdd07_full_pipeline_valid) {
    RiskGatekeeper gate(1200.0);
    TradeOrder order;
    order.pair = "EUR_USD";
    order.direction = "BUY";
    order.entry_price = 1.08500;
    order.stop_loss = 1.08350;    // 15 pip SL
    order.take_profit = 1.08800;  // 30 pip TP — RR 1:2
    order.confidence = 0.9;
    int units = gate.full_validate(order, 1.08505);  // 0.5 pip slip (ok)
    assert(units > 0 && "TDD_07: Valid order must return positive units");
}

// ─── Main Runner ─────────────────────────────────────────────────────────────
int main() {
    std::cout << "═══════════════════════════════════════════════\n";
    std::cout << "  Quantelos RiskGatekeeper — TDD Suite\n";
    std::cout << "═══════════════════════════════════════════════\n";

    RUN(tdd01_math_constraint);
    RUN(tdd02_hallucination_trap);
    RUN(tdd02b_missing_sl);
    RUN(tdd03_slippage_guard);
    RUN(tdd03b_acceptable_slippage);
    RUN(tdd04_drawdown_breach);
    RUN(tdd05_risk_reward_validation);
    RUN(tdd06_max_positions);
    RUN(tdd07_full_pipeline_valid);

    std::cout << "═══════════════════════════════════════════════\n";
    std::cout << "  Results: " << passed << " passed, " << failed << " failed\n";
    std::cout << "═══════════════════════════════════════════════\n";
    return failed > 0 ? 1 : 0;
}
