// =============================================================================
// Quantelos AI Trader — C++ ZeroMQ Subscriber Header
// =============================================================================
#pragma once
#include <string>
#include <functional>
#include <atomic>
#include <thread>
#include <iostream>

// NOTE: Requires cppzmq (header-only). Install: sudo apt install libzmq3-dev
#include <zmq.hpp>

namespace quantelos {

class ZMQSubscriber {
public:
    using MessageHandler = std::function<void(const std::string&)>;

    explicit ZMQSubscriber(const std::string& address = "ipc:///tmp/quantelos_ipc.sock")
        : context_(1)
        , socket_(context_, zmq::socket_type::sub)
        , running_(false) {
        socket_.connect(address);
        socket_.set(zmq::sockopt::subscribe, "");  // Subscribe to all messages
        socket_.set(zmq::sockopt::rcvtimeo, 5000); // 5s poll timeout
        std::cout << "[ZMQ_SUB] Connected to " << address << "\n";
    }

    /// Start listening loop in the current thread (blocking).
    void listen(MessageHandler handler) {
        running_ = true;
        std::cout << "[ZMQ_SUB] Listening for signals...\n";

        while (running_) {
            zmq::message_t msg;
            auto result = socket_.recv(msg, zmq::recv_flags::none);
            if (result) {
                std::string payload(static_cast<char*>(msg.data()), msg.size());
                handler(payload);
            }
            // If recv times out (no message), loop continues (idle, ~0% CPU)
        }
    }

    void stop() { running_ = false; }

    ~ZMQSubscriber() {
        stop();
        socket_.close();
        context_.close();
    }

private:
    zmq::context_t context_;
    zmq::socket_t  socket_;
    std::atomic<bool> running_;
};

} // namespace quantelos
