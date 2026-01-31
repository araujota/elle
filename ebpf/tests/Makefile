# Makefile for ELLE C unit test harnesses
# Builds test binaries with debug symbols and no optimization for Valgrind
#
# Usage:
#   make                  Build all test harnesses
#   make valgrind         Run all tests under Valgrind
#   make clean            Remove build artifacts
#
# Dependencies:
#   apt install gcc libjansson-dev libpcre2-dev libssl-dev libsystemd-dev valgrind

.PHONY: all clean valgrind valgrind-harnesses help

# Compiler settings - debug, no optimization for accurate Valgrind traces
CC := gcc
CFLAGS := -g -O0 -Wall -Wextra -Werror -D_GNU_SOURCE \
          -I../telemetryd \
          -I../telemetryd/normalize

# Library flags
JANSSON_LIBS := $(shell pkg-config --libs jansson 2>/dev/null || echo "-ljansson")
PCRE2_LIBS := $(shell pkg-config --libs libpcre2-8 2>/dev/null || echo "-lpcre2-8")
OPENSSL_LIBS := $(shell pkg-config --libs openssl 2>/dev/null || echo "-lssl -lcrypto")
SYSTEMD_LIBS := $(shell pkg-config --libs libsystemd 2>/dev/null || echo "-lsystemd")

LDFLAGS := $(JANSSON_LIBS) $(PCRE2_LIBS) $(OPENSSL_LIBS) $(SYSTEMD_LIBS)

# Build directory
BUILD_DIR := build

# Normalization source files (the code under test)
NORMALIZE_SRCS := \
	../telemetryd/normalize/normalizer.c \
	../telemetryd/normalize/category.c \
	../telemetryd/normalize/entity.c \
	../telemetryd/normalize/fingerprint.c \
	../telemetryd/normalize/dedup.c \
	../telemetryd/json.c

NORMALIZE_OBJS := $(patsubst ../%.c,$(BUILD_DIR)/%.o,$(NORMALIZE_SRCS))

# Test harnesses
TESTS := \
	$(BUILD_DIR)/test_normalizer \
	$(BUILD_DIR)/test_dedup \
	$(BUILD_DIR)/test_json \
	$(BUILD_DIR)/test_fingerprint

# Valgrind settings
VALGRIND := valgrind
VALGRIND_FLAGS := --leak-check=full \
                  --error-exitcode=1 \
                  --track-origins=yes \
                  --show-leak-kinds=all \
                  --errors-for-leak-kinds=definite,possible \
                  --suppressions=valgrind.supp

# Default: build all test harnesses
all: $(TESTS)

# Create build directories
$(BUILD_DIR):
	mkdir -p $(BUILD_DIR)/telemetryd/normalize $(BUILD_DIR)/telemetryd

# Compile normalization sources as object files
$(BUILD_DIR)/telemetryd/%.o: ../telemetryd/%.c | $(BUILD_DIR)
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -c $< -o $@

# Compile test harnesses
$(BUILD_DIR)/test_normalizer: test_normalizer.c $(NORMALIZE_OBJS) | $(BUILD_DIR)
	$(CC) $(CFLAGS) $^ -o $@ $(LDFLAGS)

$(BUILD_DIR)/test_dedup: test_dedup.c $(NORMALIZE_OBJS) | $(BUILD_DIR)
	$(CC) $(CFLAGS) $^ -o $@ $(LDFLAGS)

$(BUILD_DIR)/test_json: test_json.c $(NORMALIZE_OBJS) | $(BUILD_DIR)
	$(CC) $(CFLAGS) $^ -o $@ $(LDFLAGS)

$(BUILD_DIR)/test_fingerprint: test_fingerprint.c $(NORMALIZE_OBJS) | $(BUILD_DIR)
	$(CC) $(CFLAGS) $^ -o $@ $(LDFLAGS)

# Alias for CI: just build the harnesses
valgrind-harnesses: $(TESTS)

# Run all tests under Valgrind
valgrind: $(TESTS)
	@echo "=== Running test harnesses under Valgrind ==="
	@FAIL=0; \
	for test in $(TESTS); do \
		echo ""; \
		echo "--- $$test ---"; \
		$(VALGRIND) $(VALGRIND_FLAGS) $$test || FAIL=1; \
	done; \
	echo ""; \
	if [ $$FAIL -eq 0 ]; then \
		echo "All Valgrind checks passed."; \
	else \
		echo "Valgrind detected memory errors!"; \
		exit 1; \
	fi

clean:
	rm -rf $(BUILD_DIR)

help:
	@echo "ELLE C Test Harnesses"
	@echo ""
	@echo "Targets:"
	@echo "  all               Build all test harnesses"
	@echo "  valgrind          Run all tests under Valgrind"
	@echo "  valgrind-harnesses  Build harnesses only (alias for CI)"
	@echo "  clean             Remove build artifacts"
	@echo ""
	@echo "Individual tests:"
	@echo "  build/test_normalizer    Normalizer create/process/destroy cycle"
	@echo "  build/test_dedup         Ring buffer wrap-around, hash collisions"
	@echo "  build/test_json          JSON ref counting, serialization"
	@echo "  build/test_fingerprint   NULL inputs, empty strings, long messages"
