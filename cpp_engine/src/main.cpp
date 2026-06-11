// =============================================================================
// Quantelos AI Trader — C++ Execution Engine (Main Entry Point)
// =============================================================================
// Subscribes to ZMQ IPC signals from Python, validates risk metrics via 
// RiskGatekeeper, executes orders on OANDA API, and logs trades to SQLite.
// =============================================================================
#include "risk_gatekeeper.hpp"
#include "zmq_subscriber.hpp"
#include <nlohmann/json.hpp>
#include <sqlite3.h>

#include <iostream>
#include <string>
#include <chrono>
#include <thread>
#include <fstream>
#include <sstream>
#include <cstdio>
#include <memory>
#include <array>
#include <atomic>
#include <iomanip>

using json = nlohmann::json;

namespace quantelos {

// Simple TOML helper to parse settings.toml
std::string get_toml_key(const std::string& filepath, const std::string& section, const std::string& key) {
    std::ifstream file(filepath);
    if (!file.is_open()) return "";
    std::string line;
    bool in_section = false;
    while (std::getline(file, line)) {
        // Strip inline comments
        auto comment_pos = line.find('#');
        if (comment_pos != std::string::npos) {
            line = line.substr(0, comment_pos);
        }
        // Trim whitespace
        line.erase(0, line.find_first_not_of(" \t\r\n"));
        line.erase(line.find_last_not_of(" \t\r\n") + 1);
        if (line.empty()) continue;
        if (line[0] == '[' && line[line.size() - 1] == ']') {
            std::string sec_name = line.substr(1, line.size() - 2);
            in_section = (sec_name == section);
            continue;
        }
        if (in_section) {
            auto pos = line.find('=');
            if (pos != std::string::npos) {
                std::string k = line.substr(0, pos);
                k.erase(k.find_last_not_of(" \t") + 1);
                if (k == key) {
                    std::string v = line.substr(pos + 1);
                    v.erase(0, v.find_first_not_of(" \t\"'"));
                    v.erase(v.find_last_not_of(" \t\"'") + 1);
                    return v;
                }
            }
        }
    }
    return "";
}

// System Command execution via popen
std::string exec_curl(const std::string& method, const std::string& url, const std::string& token, const std::string& payload = "") {
    std::string cmd = "curl -s -X " + method + " -H \"Authorization: Bearer " + token + "\" -H \"Content-Type: application/json\" ";
    if (!payload.empty()) {
        cmd += "-d '" + payload + "' ";
    }
    cmd += "\"" + url + "\"";

    std::array<char, 512> buffer;
    std::string result;
    std::unique_ptr<FILE, decltype(&pclose)> pipe(popen(cmd.c_str(), "r"), pclose);
    if (!pipe) {
        return "";
    }
    while (fgets(buffer.data(), buffer.size(), pipe.get()) != nullptr) {
        result += buffer.data();
    }
    return result;
}

// Database helper functions
void run_sql(const std::string& db_path, const std::string& sql) {
    sqlite3* db;
    if (sqlite3_open(db_path.c_str(), &db) != SQLITE_OK) {
        std::cerr << "[DB_ERROR] Can't open database: " << sqlite3_errmsg(db) << "\n";
        return;
    }
    char* err_msg = nullptr;
    if (sqlite3_exec(db, sql.c_str(), nullptr, nullptr, &err_msg) != SQLITE_OK) {
        std::cerr << "[DB_ERROR] SQL error: " << err_msg << " | Query: " << sql << "\n";
        sqlite3_free(err_msg);
    }
    sqlite3_close(db);
}

int get_open_positions_count(const std::string& db_path) {
    sqlite3* db;
    if (sqlite3_open(db_path.c_str(), &db) != SQLITE_OK) return 0;
    sqlite3_stmt* stmt;
    std::string sql = "SELECT COUNT(*) FROM active_positions WHERE status = 'OPEN'";
    int count = 0;
    if (sqlite3_prepare_v2(db, sql.c_str(), -1, &stmt, nullptr) == SQLITE_OK) {
        if (sqlite3_step(stmt) == SQLITE_ROW) {
            count = sqlite3_column_int(stmt, 0);
        }
        sqlite3_finalize(stmt);
    }
    sqlite3_close(db);
    return count;
}

std::string format_price(double price, const std::string& pair) {
    int precision = 5; // default for most FX pairs (e.g. EUR_USD)
    if (pair.find("XAU") != std::string::npos) {
        precision = 3;
    } else if (pair.find("JPY") != std::string::npos) {
        precision = 3;
    }
    std::ostringstream ss;
    ss << std::fixed << std::setprecision(precision) << price;
    return ss.str();
}

} // namespace quantelos

int main() {
    using namespace quantelos;
    std::setvbuf(stdout, NULL, _IONBF, 0);

    std::cout << "═══════════════════════════════════════════════\n";
    std::cout << "  Quantelos C++ Execution Engine v1.0\n";
    std::cout << "  Status: Autonomous Mode | Loading settings...\n";
    std::cout << "═══════════════════════════════════════════════\n";

    // Load configurations from settings.toml
    std::string config_path = "./config/settings.toml";
    std::string db_path = get_toml_key(config_path, "database", "path");
    std::string api_url = get_toml_key(config_path, "oanda", "api_url");
    std::string account_id = get_toml_key(config_path, "oanda", "account_id");
    std::string api_token = get_toml_key(config_path, "oanda", "api_token");
    std::string ipc_path = get_toml_key(config_path, "zmq", "ipc_path");

    if (db_path.empty() || api_url.empty() || account_id.empty() || api_token.empty()) {
        std::cerr << "[ERROR] Configuration missing in " << config_path << "\n";
        return 1;
    }

    // Load risk settings dynamically
    double risk_per_trade_pct = 0.05;
    double max_drawdown_pct = 0.15;
    try {
        std::string risk_str = get_toml_key(config_path, "risk", "risk_per_trade_pct");
        if (!risk_str.empty()) risk_per_trade_pct = std::stod(risk_str);
        std::string dd_str = get_toml_key(config_path, "risk", "max_drawdown_pct");
        if (!dd_str.empty()) max_drawdown_pct = std::stod(dd_str);
        std::cout << "[CONFIG] Loaded Risk Per Trade: " << risk_per_trade_pct * 100.0 << "% | Max Drawdown: " << max_drawdown_pct * 100.0 << "%\n";
    } catch (...) {
        std::cout << "[CONFIG] Warning: Could not parse risk parameters. Using defaults (5% / 15%).\n";
    }

    // Set initial equity dynamically by calling OANDA summary
    double initial_equity = 100000.0; // Fallback
    std::string summary_url = api_url + "/v3/accounts/" + account_id + "/summary";
    std::string summary_res = exec_curl("GET", summary_url, api_token);
    try {
        auto summary_json = json::parse(summary_res);
        if (summary_json.contains("account")) {
            std::string bal_str = summary_json["account"]["balance"];
            // Convert OANDA standard balance (e.g. 12.00 USD) to Cents (1200.0 USC)
            initial_equity = std::stod(bal_str) * 100.0;
            std::cout << "[OANDA] Initial Account Balance: " << std::stod(bal_str) << " (scaled to " << initial_equity << " USC)\n";
        }
    } catch (...) {
        std::cout << "[OANDA] Warning: Could not fetch initial balance. Using default: " << initial_equity << " USC\n";
    }

    RiskGatekeeper gate(initial_equity, risk_per_trade_pct, max_drawdown_pct);
    std::atomic<bool> running{true};

    // Start background Heartbeat Thread
    std::thread heartbeat_thread([&running, db_path]() {
        while (running) {
            std::string timestamp = std::to_string(std::chrono::system_clock::to_time_t(std::chrono::system_clock::now()));
            std::string sql = "INSERT INTO heartbeat_log (node_name, status, ram_mb, cpu_pct) VALUES ('cpp_executor', 'ALIVE', 4.5, 0.05);";
            run_sql(db_path, sql);
            std::this_thread::sleep_for(std::chrono::seconds(60));
        }
    });

    // Initialize ZMQ Subscriber
    std::string zmq_address = "ipc://" + ipc_path;
    ZMQSubscriber sub(zmq_address);

    // Message handler callback
    auto on_message = [&](const std::string& payload) {
        TradeOrder order;
        try {
            auto signal = json::parse(payload);

            // Handle Heartbeats
            if (signal.contains("decision") && signal["decision"] == "HEARTBEAT") {
                std::cout << "[HEARTBEAT] Python logic node alive.\n";
                return;
            }

            std::cout << "\n[RECV] Processing Trade Signal...\n";

            order.pair = signal["pair"].get<std::string>();
            order.direction = signal["direction"].get<std::string>();
            order.entry_price = signal["entry_price"].get<double>();
            order.stop_loss = signal["stop_loss"].get<double>();
            order.take_profit = signal["take_profit"].get<double>();
            order.confidence = signal["confidence"].get<double>();

            // Query live price from OANDA for slippage validation
            double server_price = order.entry_price;
            std::string pricing_url = api_url + "/v3/accounts/" + account_id + "/pricing?instruments=" + order.pair;
            std::string price_res = exec_curl("GET", pricing_url, api_token);
            try {
                auto price_json = json::parse(price_res);
                if (price_json.contains("prices") && !price_json["prices"].empty()) {
                    auto price_obj = price_json["prices"][0];
                    double bid = std::stod(price_obj["bids"][0]["price"].get<std::string>());
                    double ask = std::stod(price_obj["asks"][0]["price"].get<std::string>());
                    server_price = (bid + ask) / 2.0;
                    std::cout << "[OANDA] Live Midpoint Price: " << server_price << "\n";
                }
            } catch (...) {
                std::cout << "[OANDA] Warning: Could not fetch live price. Using entry price for slippage validation.\n";
            }

            // Query OANDA account summary to get latest equity
            try {
                std::string summary_res = exec_curl("GET", summary_url, api_token);
                auto summary_json = json::parse(summary_res);
                if (summary_json.contains("account")) {
                    std::string nav_str = summary_json["account"]["NAV"];
                    // Convert OANDA NAV standard currency amount to Cents
                    double current_equity = std::stod(nav_str) * 100.0;
                    gate.update_equity(current_equity);
                    std::cout << "[OANDA] Updated Current Equity: " << std::stod(nav_str) << " (scaled to " << current_equity << " USC)\n";
                }
            } catch (...) {
                std::cout << "[OANDA] Warning: Could not fetch current equity. Using last known: " << gate.get_equity() << " USC\n";
            }

            // Sync current active position count with Database
            int current_open_count = get_open_positions_count(db_path);
            while (gate.get_open_positions() < current_open_count) {
                gate.increment_positions();
            }
            while (gate.get_open_positions() > current_open_count) {
                gate.decrement_positions();
            }

            // Validate using RiskGatekeeper
            int units = gate.full_validate(order, server_price);
            int signed_units = (order.direction == "BUY") ? units : -units;

            std::cout << "[✓ RISK_GATE] Signal Validated successfully!\n";
            std::cout << "  Pair      : " << order.pair << "\n";
            std::cout << "  Direction : " << order.direction << " (" << signed_units << " units)\n";
            std::cout << "  SL / TP   : " << order.stop_loss << " / " << order.take_profit << "\n";

            // Execute Trade on OANDA
            std::cout << "[→ OANDA] Sending order to OANDA API...\n";
            json order_payload = {
                {"order", {
                    {"units", std::to_string(signed_units)},
                    {"instrument", order.pair},
                    {"timeInForce", "FOK"},
                    {"type", "MARKET"},
                    {"positionFill", "DEFAULT"},
                    {"stopLossOnFill", {{"price", format_price(order.stop_loss, order.pair)}}},
                    {"takeProfitOnFill", {{"price", format_price(order.take_profit, order.pair)}}}
                }}
            };

            std::string order_url = api_url + "/v3/accounts/" + account_id + "/orders";
            std::string order_res = exec_curl("POST", order_url, api_token, order_payload.dump());
            
            std::cout << "[OANDA RESPONSE] " << order_res << "\n";

            auto res_json = json::parse(order_res);
            if (res_json.contains("orderFillTransaction")) {
                std::string trade_id = res_json["orderFillTransaction"]["id"].get<std::string>();
                std::string fill_price_str = res_json["orderFillTransaction"]["price"].get<std::string>();
                double fill_price = std::stod(fill_price_str);

                std::cout << "[✓ EXECUTED] Trade Fill Success! ID: " << trade_id << " at " << fill_price << "\n";

                // Save to SQLite
                double lot_size = static_cast<double>(units) / 1000.0; // USC micro-lots
                std::ostringstream sql_stream;
                sql_stream << "INSERT INTO active_positions (trade_id, pair, direction, lot_size, units, entry_price, stop_loss, take_profit, status) "
                           << "VALUES ('" << trade_id << "', '" << order.pair << "', '" << order.direction << "', " 
                           << lot_size << ", " << units << ", " << fill_price << ", " << order.stop_loss << ", " 
                           << order.take_profit << ", 'OPEN');";
                run_sql(db_path, sql_stream.str());
                std::cout << "[DB] Saved position to active_positions.\n";
            } else {
                std::string err_msg = "Order failed or was rejected by broker.";
                if (res_json.contains("errorMessage")) {
                    err_msg = res_json["errorMessage"].get<std::string>();
                }
                std::cerr << "[✗ FAILED] OANDA rejected order: " << err_msg << "\n";
            }

        } catch (const DrawdownBreachException& e) {
            std::cerr << "[🔴 EMERGENCY HALT] Drawdown Breach Detected! " << e.what() << "\n";
            
            // 1. Write emergency_halt to system_state SQLite
            std::string sql_halt = "UPDATE system_state SET config_value = 'TRUE' WHERE config_key = 'emergency_halt';";
            run_sql(db_path, sql_halt);
            
            // 2. Send Discord notification using curl
            std::string discord_webhook = get_toml_key(config_path, "notifications", "discord_webhook");
            if (!discord_webhook.empty()) {
                std::string payload = "{\"embeds\": [{\"title\": \"🔴 EMERGENCY HALT - Drawdown Breach\", \"description\": \"" + std::string(e.what()) + "\", \"color\": 10038562}]}";
                exec_curl("POST", discord_webhook, "", payload);
            }
            
            // 3. Cancel/Close all positions via OANDA API
            std::string close_url = api_url + "/v3/accounts/" + account_id + "/positions/" + order.pair + "/close";
            std::string close_payload_long = "{\"longUnits\": \"ALL\"}";
            std::string close_payload_short = "{\"shortUnits\": \"ALL\"}";
            std::cout << "[EMERGENCY] Closing all open positions for " << order.pair << " on OANDA...\n";
            exec_curl("PUT", close_url, api_token, close_payload_long);
            exec_curl("PUT", close_url, api_token, close_payload_short);
            
            // 4. Set active_positions status to CLOSED in SQLite
            std::string sql_close = "UPDATE active_positions SET status = 'CLOSED', closed_at = datetime('now') WHERE status = 'OPEN';";
            run_sql(db_path, sql_close);

        } catch (const std::exception& e) {
            std::cerr << "[✗ REJECTED] Risk or Execution exception: " << e.what() << "\n";
        }
    };

    // Start listening
    sub.listen(on_message);

    // Shutdown
    running = false;
    if (heartbeat_thread.joinable()) {
        heartbeat_thread.join();
    }
    std::cout << "Quantelos C++ Execution Engine shut down gracefully.\n";
    return 0;
}
