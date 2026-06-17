#include "risk_gatekeeper.hpp"
#include "zmq_subscriber.hpp"
#include <nlohmann/json.hpp>
#include <sqlite3.h>
#include <curl/curl.h>

#include <iostream>
#include <string>
#include <chrono>
#include <thread>
#include <fstream>
#include <sstream>
#include <memory>
#include <array>
#include <atomic>
#include <iomanip>
#include <csignal>

using json = nlohmann::json;

namespace quantelos {

std::atomic<bool> g_running{true};

void signal_handler(int signal) {
    if (signal == SIGINT || signal == SIGTERM) {
        std::cout << "\n[SIGNAL] Caught shutdown signal " << signal << ". Shutting down gracefully...\n";
        g_running = false;
    }
}

// Simple TOML helper to parse settings.toml
std::string get_toml_key(const std::string& filepath, const std::string& section, const std::string& key) {
    std::ifstream file(filepath);
    if (!file.is_open()) return "";
    std::string line;
    bool in_section = false;
    while (std::getline(file, line)) {
        auto comment_pos = line.find('#');
        if (comment_pos != std::string::npos) line = line.substr(0, comment_pos);
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

// FIX C-03: Use libcurl instead of popen(curl)
size_t WriteCallback(void* contents, size_t size, size_t nmemb, std::string* s) {
    size_t newLength = size * nmemb;
    try {
        s->append((char*)contents, newLength);
    } catch(std::bad_alloc& e) {
        return 0;
    }
    return newLength;
}

std::string exec_curl(const std::string& method, const std::string& url, const std::string& token, const std::string& payload = "") {
    CURL* curl;
    CURLcode res;
    std::string readBuffer;

    curl = curl_easy_init();
    if(curl) {
        curl_easy_setopt(curl, CURLOPT_CUSTOMREQUEST, method.c_str());
        curl_easy_setopt(curl, CURLOPT_URL, url.c_str());

        struct curl_slist* headers = NULL;
        std::string auth_header = "Authorization: Bearer " + token;
        headers = curl_slist_append(headers, auth_header.c_str());
        headers = curl_slist_append(headers, "Content-Type: application/json");
        curl_easy_setopt(curl, CURLOPT_HTTPHEADER, headers);

        if (!payload.empty()) {
            curl_easy_setopt(curl, CURLOPT_POSTFIELDS, payload.c_str());
        }

        curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, WriteCallback);
        curl_easy_setopt(curl, CURLOPT_WRITEDATA, &readBuffer);
        curl_easy_setopt(curl, CURLOPT_TIMEOUT, 10L); // Add timeout to prevent hanging

        res = curl_easy_perform(curl);
        if(res != CURLE_OK) {
            std::cerr << "[CURL_ERROR] curl_easy_perform() failed: " << curl_easy_strerror(res) << "\n";
        }

        curl_slist_free_all(headers);
        curl_easy_cleanup(curl);
    }
    return readBuffer;
}

// FIX H-04: RAII Wrapper for SQLite
class SQLiteDB {
public:
    explicit SQLiteDB(const std::string& path) {
        if (sqlite3_open(path.c_str(), &db_) != SQLITE_OK) {
            std::string err = sqlite3_errmsg(db_);
            if (db_) sqlite3_close(db_);
            throw std::runtime_error("Can't open database: " + err);
        }
        // Enable extended result codes for better debugging
        sqlite3_extended_result_codes(db_, 1);
        sqlite3_busy_timeout(db_, 5000);
    }
    ~SQLiteDB() {
        if (db_) {
            sqlite3_close(db_);
            db_ = nullptr;
        }
    }

    sqlite3* get() const { return db_; }

    void execute(const std::string& sql) {
        char* err_msg = nullptr;
        if (sqlite3_exec(db_, sql.c_str(), nullptr, nullptr, &err_msg) != SQLITE_OK) {
            std::string err = err_msg ? err_msg : "Unknown error";
            if (err_msg) sqlite3_free(err_msg);
            std::cerr << "[DB_ERROR] SQL error: " << err << " | Query: " << sql << "\n";
        }
    }

private:
    sqlite3* db_ = nullptr;
};

// Database helper functions using Parameterized Queries (FIX C-02)
int get_open_positions_count(SQLiteDB& db) {
    sqlite3_stmt* stmt;
    std::string sql = "SELECT COUNT(*) FROM active_positions WHERE status = 'OPEN'";
    int count = 0;
    if (sqlite3_prepare_v2(db.get(), sql.c_str(), -1, &stmt, nullptr) == SQLITE_OK) {
        if (sqlite3_step(stmt) == SQLITE_ROW) {
            count = sqlite3_column_int(stmt, 0);
        }
        sqlite3_finalize(stmt);
    }
    return count;
}

std::string format_price(double price, const std::string& pair) {
    int precision = 5;
    if (pair.find("XAU") != std::string::npos || pair.find("JPY") != std::string::npos) {
        precision = 3;
    }
    std::ostringstream ss;
    ss << std::fixed << std::setprecision(precision) << price;
    return ss.str();
}

// FIX H-06: Read actual RAM usage
double get_proc_ram_mb() {
    std::ifstream file("/proc/self/status");
    std::string line;
    while (std::getline(file, line)) {
        if (line.compare(0, 6, "VmRSS:") == 0) {
            std::istringstream iss(line.substr(6));
            double kb = 0;
            iss >> kb;
            return kb / 1024.0;
        }
    }
    return 0.0;
}

// FIX C-05: Check emergency halt flag
bool check_emergency_halt(SQLiteDB& db) {
    std::string sql = "SELECT config_value FROM system_state WHERE config_key = 'emergency_halt';";
    sqlite3_stmt* stmt;
    bool halted = false;
    if (sqlite3_prepare_v2(db.get(), sql.c_str(), -1, &stmt, nullptr) == SQLITE_OK) {
        if (sqlite3_step(stmt) == SQLITE_ROW) {
            std::string val = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 0));
            if (val == "TRUE" || val == "true") halted = true;
        }
        sqlite3_finalize(stmt);
    }
    return halted;
}

// FIX M-04: Deduplication Check
bool is_signal_processed(SQLiteDB& db, const std::string& signal_key) {
    std::string sql = "SELECT COUNT(*) FROM processed_signals WHERE signal_key = ?;";
    sqlite3_stmt* stmt;
    bool exists = false;
    if (sqlite3_prepare_v2(db.get(), sql.c_str(), -1, &stmt, nullptr) == SQLITE_OK) {
        sqlite3_bind_text(stmt, 1, signal_key.c_str(), -1, SQLITE_TRANSIENT);
        if (sqlite3_step(stmt) == SQLITE_ROW) {
            exists = (sqlite3_column_int(stmt, 0) > 0);
        }
        sqlite3_finalize(stmt);
    }
    return exists;
}

void mark_signal_processed(SQLiteDB& db, const std::string& signal_key) {
    std::string sql = "INSERT INTO processed_signals (signal_key) VALUES (?);";
    sqlite3_stmt* stmt;
    if (sqlite3_prepare_v2(db.get(), sql.c_str(), -1, &stmt, nullptr) == SQLITE_OK) {
        sqlite3_bind_text(stmt, 1, signal_key.c_str(), -1, SQLITE_TRANSIENT);
        sqlite3_step(stmt);
        sqlite3_finalize(stmt);
    }
}

// FIX C-02: Parameterized Insert
void save_position(SQLiteDB& db, const std::string& trade_id, const std::string& pair, const std::string& direction, double lot_size, int units, double entry_price, double sl, double tp) {
    std::string sql = "INSERT INTO active_positions (trade_id, pair, direction, lot_size, units, entry_price, stop_loss, take_profit, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'OPEN');";
    sqlite3_stmt* stmt;
    if (sqlite3_prepare_v2(db.get(), sql.c_str(), -1, &stmt, nullptr) == SQLITE_OK) {
        sqlite3_bind_text(stmt, 1, trade_id.c_str(), -1, SQLITE_TRANSIENT);
        sqlite3_bind_text(stmt, 2, pair.c_str(), -1, SQLITE_TRANSIENT);
        sqlite3_bind_text(stmt, 3, direction.c_str(), -1, SQLITE_TRANSIENT);
        sqlite3_bind_double(stmt, 4, lot_size);
        sqlite3_bind_int(stmt, 5, units);
        sqlite3_bind_double(stmt, 6, entry_price);
        sqlite3_bind_double(stmt, 7, sl);
        sqlite3_bind_double(stmt, 8, tp);
        
        if (sqlite3_step(stmt) != SQLITE_DONE) {
            std::cerr << "[DB_ERROR] Failed to insert position: " << sqlite3_errmsg(db.get()) << "\n";
        }
        sqlite3_finalize(stmt);
    } else {
        std::cerr << "[DB_ERROR] Failed to prepare insert position: " << sqlite3_errmsg(db.get()) << "\n";
    }
}

// FIX C-02: Parameterized Update (for CLOSE_TRADE)
void close_position_in_db(SQLiteDB& db, const std::string& trade_id) {
    std::string sql = "UPDATE active_positions SET status = 'CLOSED', closed_at = datetime('now') WHERE trade_id = ? AND status = 'OPEN';";
    sqlite3_stmt* stmt;
    if (sqlite3_prepare_v2(db.get(), sql.c_str(), -1, &stmt, nullptr) == SQLITE_OK) {
        sqlite3_bind_text(stmt, 1, trade_id.c_str(), -1, SQLITE_TRANSIENT);
        sqlite3_step(stmt);
        sqlite3_finalize(stmt);
    }
}

// Add heartbeat parameter binding
void log_heartbeat(SQLiteDB& db, double ram_mb) {
    std::string sql = "INSERT INTO heartbeat_log (node_name, status, ram_mb, cpu_pct) VALUES ('cpp_executor', 'ALIVE', ?, 0.05);";
    sqlite3_stmt* stmt;
    if (sqlite3_prepare_v2(db.get(), sql.c_str(), -1, &stmt, nullptr) == SQLITE_OK) {
        sqlite3_bind_double(stmt, 1, ram_mb);
        sqlite3_step(stmt);
        sqlite3_finalize(stmt);
    }
}


} // namespace quantelos

int main() {
    using namespace quantelos;
    std::setvbuf(stdout, NULL, _IONBF, 0);

    // FIX H-05: Register signal handlers
    std::signal(SIGINT, signal_handler);
    std::signal(SIGTERM, signal_handler);

    // Initialize CURL globally
    curl_global_init(CURL_GLOBAL_ALL);

    std::cout << "═══════════════════════════════════════════════\n";
    std::cout << "  Quantelos C++ Execution Engine v1.1 (FIXED)\n";
    std::cout << "  Status: Autonomous Mode | Loading settings...\n";
    std::cout << "═══════════════════════════════════════════════\n";

    std::string config_path = "./config/settings.toml";
    std::string db_path = get_toml_key(config_path, "database", "path");
    std::string api_url = get_toml_key(config_path, "oanda", "api_url");
    std::string account_id = get_toml_key(config_path, "oanda", "account_id");
    std::string api_token = get_toml_key(config_path, "oanda", "api_token");
    std::string ipc_path = get_toml_key(config_path, "zmq", "ipc_path");

    if (db_path.empty() || api_url.empty() || account_id.empty() || api_token.empty()) {
        std::cerr << "[ERROR] Configuration missing in " << config_path << "\n";
        curl_global_cleanup();
        return 1;
    }

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

    double initial_equity = 100000.0;
    std::string summary_url = api_url + "/v3/accounts/" + account_id + "/summary";
    std::string summary_res = exec_curl("GET", summary_url, api_token);
    try {
        auto summary_json = json::parse(summary_res);
        if (summary_json.contains("account")) {
            std::string bal_str = summary_json["account"]["balance"].get<std::string>();
            initial_equity = std::stod(bal_str) * 100.0;
            std::cout << "[OANDA] Initial Account Balance: " << std::stod(bal_str) << " (scaled to " << initial_equity << " USC)\n";
        }
    } catch (...) {
        std::cout << "[OANDA] Warning: Could not fetch initial balance. Using default: " << initial_equity << " USC\n";
    }

    RiskGatekeeper gate(initial_equity, risk_per_trade_pct, max_drawdown_pct);

    // Start background Heartbeat Thread (using its own DB connection)
    std::thread heartbeat_thread([db_path]() {
        try {
            SQLiteDB hb_db(db_path);
            while (g_running) {
                log_heartbeat(hb_db, get_proc_ram_mb());
                
                // Sleep with interruption check
                for (int i = 0; i < 60 && g_running; ++i) {
                    std::this_thread::sleep_for(std::chrono::seconds(1));
                }
            }
        } catch (const std::exception& e) {
            std::cerr << "[HEARTBEAT] Thread error: " << e.what() << "\n";
        }
    });

    // Initialize ZMQ Subscriber
    std::string zmq_address = "ipc://" + ipc_path;
    ZMQSubscriber sub(zmq_address);

    // Initialize Main Thread DB Connection
    std::unique_ptr<SQLiteDB> main_db;
    try {
        main_db = std::make_unique<SQLiteDB>(db_path);
    } catch (const std::exception& e) {
        std::cerr << "[ERROR] " << e.what() << "\n";
        g_running = false;
        heartbeat_thread.join();
        curl_global_cleanup();
        return 1;
    }

    auto on_message = [&](const std::string& payload) {
        if (!g_running) return;

        // FIX C-05: Check emergency halt BEFORE processing
        if (check_emergency_halt(*main_db)) {
            std::cerr << "[WARNING] SYSTEM HALTED. Ignoring signal.\n";
            return;
        }

        TradeOrder order;
        try {
            auto signal = json::parse(payload);

            if (signal.contains("decision") && signal["decision"] == "HEARTBEAT") {
                return;
            }

            // FIX C-04: Implement CLOSE_TRADE handler
            if (signal.contains("decision") && signal["decision"] == "CLOSE_TRADE") {
                if (signal.contains("trade_id")) {
                    std::string t_id = signal["trade_id"].get<std::string>();
                    std::cout << "\n[RECV] Received CLOSE_TRADE for ID: " << t_id << "\n";
                    
                    std::string close_url = api_url + "/v3/accounts/" + account_id + "/trades/" + t_id + "/close";
                    std::string close_payload = "{\"units\": \"ALL\"}";
                    std::string close_res = exec_curl("PUT", close_url, api_token, close_payload);
                    
                    auto res_json = json::parse(close_res);
                    if (res_json.contains("orderFillTransaction")) {
                        std::cout << "[✓ EXECUTED] Trade " << t_id << " successfully closed on OANDA.\n";
                        close_position_in_db(*main_db, t_id);
                        gate.decrement_positions();
                    } else {
                        std::cerr << "[✗ FAILED] Could not close trade " << t_id << " on OANDA.\n";
                    }
                }
                return;
            }

            // If not CLOSE_TRADE, proceed with EXECUTE_TRADE
            std::cout << "\n[RECV] Processing Trade Signal...\n";

            order.pair = signal["pair"].get<std::string>();
            order.direction = signal["direction"].get<std::string>();
            order.entry_price = signal["entry_price"].get<double>();
            order.stop_loss = signal["stop_loss"].get<double>();
            order.take_profit = signal["take_profit"].get<double>();
            order.confidence = signal["confidence"].get<double>();

            // FIX M-04: Deduplication check
            std::string signal_timestamp = signal.contains("timestamp") ? signal["timestamp"].get<std::string>() : "";
            std::string signal_key = order.pair + "_" + order.direction + "_" + signal_timestamp;
            if (is_signal_processed(*main_db, signal_key)) {
                std::cout << "[INFO] Duplicate signal ignored: " << signal_key << "\n";
                return;
            }
            mark_signal_processed(*main_db, signal_key);

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

            try {
                std::string summary_res_live = exec_curl("GET", summary_url, api_token);
                auto summary_json_live = json::parse(summary_res_live);
                if (summary_json_live.contains("account")) {
                    std::string nav_str = summary_json_live["account"]["NAV"].get<std::string>();
                    double current_equity = std::stod(nav_str) * 100.0;
                    gate.update_equity(current_equity);
                }
            } catch (...) {
                std::cout << "[OANDA] Warning: Could not fetch current equity. Using last known: " << gate.get_equity() << " USC\n";
            }

            int current_open_count = get_open_positions_count(*main_db);
            while (gate.get_open_positions() < current_open_count) {
                gate.increment_positions();
            }
            while (gate.get_open_positions() > current_open_count) {
                gate.decrement_positions();
            }

            int units = gate.full_validate(order, server_price);
            int signed_units = (order.direction == "BUY") ? units : -units;

            std::cout << "[✓ RISK_GATE] Signal Validated successfully!\n";
            std::cout << "  Pair      : " << order.pair << "\n";
            std::cout << "  Direction : " << order.direction << " (" << signed_units << " units)\n";
            std::cout << "  SL / TP   : " << order.stop_loss << " / " << order.take_profit << "\n";

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

                double lot_size = static_cast<double>(units) / 1000.0;
                save_position(*main_db, trade_id, order.pair, order.direction, lot_size, units, fill_price, order.stop_loss, order.take_profit);
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
            
            main_db->execute("UPDATE system_state SET config_value = 'TRUE' WHERE config_key = 'emergency_halt';");
            
            std::string discord_webhook = get_toml_key(config_path, "notifications", "discord_webhook");
            if (!discord_webhook.empty()) {
                std::string payload = "{\"embeds\": [{\"title\": \"🔴 EMERGENCY HALT - Drawdown Breach\", \"description\": \"" + std::string(e.what()) + "\", \"color\": 10038562}]}";
                exec_curl("POST", discord_webhook, "", payload);
            }
            
            std::string close_url = api_url + "/v3/accounts/" + account_id + "/positions/" + order.pair + "/close";
            std::string close_payload_long = "{\"longUnits\": \"ALL\"}";
            std::string close_payload_short = "{\"shortUnits\": \"ALL\"}";
            std::cout << "[EMERGENCY] Closing all open positions for " << order.pair << " on OANDA...\n";
            exec_curl("PUT", close_url, api_token, close_payload_long);
            exec_curl("PUT", close_url, api_token, close_payload_short);
            
            main_db->execute("UPDATE active_positions SET status = 'CLOSED', closed_at = datetime('now') WHERE status = 'OPEN';");

        } catch (const std::exception& e) {
            std::cerr << "[✗ REJECTED] Risk or Execution exception: " << e.what() << "\n";
        }
    };
    
    std::thread sub_thread([&]() {
        sub.listen(on_message);
    });

    while (g_running) {
        std::this_thread::sleep_for(std::chrono::milliseconds(200));
    }

    sub.stop();
    if (sub_thread.joinable()) sub_thread.join();
    if (heartbeat_thread.joinable()) heartbeat_thread.join();

    main_db.reset(); // Close DB before curl cleanup
    curl_global_cleanup();
    
    std::cout << "Quantelos C++ Execution Engine shut down gracefully.\n";
    return 0;
}
