# =============================================================================
# Quantelos AI Trader — C++ Execution Engine Build Configuration
# =============================================================================
cmake_minimum_required(VERSION 3.16)
project(quantelos_executor VERSION 1.0.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# ─── Find Dependencies ───────────────────────────────────────────────────────
find_package(PkgConfig REQUIRED)
pkg_check_modules(ZMQ REQUIRED libzmq)
pkg_check_modules(SQLITE3 REQUIRED sqlite3)
find_package(CURL REQUIRED)

# ─── Include Directories ─────────────────────────────────────────────────────
include_directories(${CMAKE_SOURCE_DIR}/include)
include_directories(${ZMQ_INCLUDE_DIRS})
include_directories(${SQLITE3_INCLUDE_DIRS})
include_directories(${CURL_INCLUDE_DIRS})

# ─── Main Executable ─────────────────────────────────────────────────────────
add_executable(quantelos_executor
    src/main.cpp
)
target_link_libraries(quantelos_executor
    ${ZMQ_LIBRARIES}
    ${SQLITE3_LIBRARIES}
    ${CURL_LIBRARIES}
    pthread
)

# ─── TDD Test Runner ─────────────────────────────────────────────────────────
add_executable(tdd_runner
    tests/tdd_suite.cpp
)
# TDD tests are header-only, no external libs needed

# ─── Installation ─────────────────────────────────────────────────────────────
install(TARGETS quantelos_executor DESTINATION bin)
