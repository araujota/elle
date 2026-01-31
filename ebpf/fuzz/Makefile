# Makefile for ELLE AFL++ fuzz harnesses
#
# Builds fuzz targets with afl-clang-fast and ASAN/UBSAN.
# Requires AFL++ installed: apt install afl++
#
# Usage:
#   make              Build all fuzz harnesses
#   make run          Run all fuzz targets for 5 minutes each
#   make run-TARGET   Run a specific target (e.g., make run-fuzz_category)
#   make clean        Remove build artifacts

.PHONY: all clean run help

# AFL++ compiler with sanitizers
CC := afl-clang-fast
CFLAGS := -g -O1 -Wall -Wextra -D_GNU_SOURCE \
          -fsanitize=address,undefined \
          -fno-omit-frame-pointer \
          -I../telemetryd \
          -I../telemetryd/normalize

# Library flags
JANSSON_LIBS := $(shell pkg-config --libs jansson 2>/dev/null || echo "-ljansson")
PCRE2_LIBS := $(shell pkg-config --libs libpcre2-8 2>/dev/null || echo "-lpcre2-8")
OPENSSL_LIBS := $(shell pkg-config --libs openssl 2>/dev/null || echo "-lssl -lcrypto")
SYSTEMD_LIBS := $(shell pkg-config --libs libsystemd 2>/dev/null || echo "-lsystemd")

LDFLAGS := -fsanitize=address,undefined \
           $(JANSSON_LIBS) $(PCRE2_LIBS) $(OPENSSL_LIBS) $(SYSTEMD_LIBS)

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

# Fuzz targets
TARGETS := \
	$(BUILD_DIR)/fuzz_category \
	$(BUILD_DIR)/fuzz_fingerprint \
	$(BUILD_DIR)/fuzz_normalizer \
	$(BUILD_DIR)/fuzz_dedup

# Fuzz run duration (seconds)
FUZZ_DURATION := 300

# Default: build all
all: $(TARGETS)

# Create build directories
$(BUILD_DIR):
	mkdir -p $(BUILD_DIR)/telemetryd/normalize $(BUILD_DIR)/telemetryd

# Compile normalization sources
$(BUILD_DIR)/telemetryd/%.o: ../telemetryd/%.c | $(BUILD_DIR)
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -c $< -o $@

# Build fuzz targets
$(BUILD_DIR)/fuzz_category: fuzz_category.c $(NORMALIZE_OBJS) | $(BUILD_DIR)
	$(CC) $(CFLAGS) $^ -o $@ $(LDFLAGS)

$(BUILD_DIR)/fuzz_fingerprint: fuzz_fingerprint.c $(NORMALIZE_OBJS) | $(BUILD_DIR)
	$(CC) $(CFLAGS) $^ -o $@ $(LDFLAGS)

$(BUILD_DIR)/fuzz_normalizer: fuzz_normalizer.c $(NORMALIZE_OBJS) | $(BUILD_DIR)
	$(CC) $(CFLAGS) $^ -o $@ $(LDFLAGS)

$(BUILD_DIR)/fuzz_dedup: fuzz_dedup.c $(NORMALIZE_OBJS) | $(BUILD_DIR)
	$(CC) $(CFLAGS) $^ -o $@ $(LDFLAGS)

# Run all fuzz targets
run: $(TARGETS)
	@echo "=== Running AFL++ fuzz targets ($(FUZZ_DURATION)s each) ==="
	@FAIL=0; \
	for target in $(TARGETS); do \
		NAME=$$(basename $$target); \
		echo ""; \
		echo "--- Fuzzing $$NAME for $(FUZZ_DURATION)s ---"; \
		mkdir -p findings/$$NAME; \
		timeout $(FUZZ_DURATION) afl-fuzz \
			-i seeds \
			-o findings/$$NAME \
			-V $(FUZZ_DURATION) \
			-- $$target 2>&1 | tail -5 || true; \
		if [ -d findings/$$NAME/default/crashes ] && \
		   [ "$$(ls -A findings/$$NAME/default/crashes 2>/dev/null)" ]; then \
			echo "CRASHES FOUND in $$NAME!"; \
			FAIL=1; \
		else \
			echo "No crashes found in $$NAME."; \
		fi; \
	done; \
	echo ""; \
	if [ $$FAIL -eq 1 ]; then \
		echo "FUZZING FOUND CRASHES - see findings/ directory"; \
		exit 1; \
	else \
		echo "All fuzz targets passed (no crashes)."; \
	fi

# Run individual targets
run-%: $(BUILD_DIR)/% | seeds
	@NAME=$*; \
	echo "Fuzzing $$NAME for $(FUZZ_DURATION)s..."; \
	mkdir -p findings/$$NAME; \
	timeout $(FUZZ_DURATION) afl-fuzz \
		-i seeds \
		-o findings/$$NAME \
		-V $(FUZZ_DURATION) \
		-- $(BUILD_DIR)/$$NAME

clean:
	rm -rf $(BUILD_DIR) findings

help:
	@echo "ELLE AFL++ Fuzz Harnesses"
	@echo ""
	@echo "Targets:"
	@echo "  all                Build all fuzz harnesses"
	@echo "  run                Run all targets for $(FUZZ_DURATION)s each"
	@echo "  run-fuzz_category  Run category fuzzer only"
	@echo "  run-fuzz_fingerprint  Run fingerprint fuzzer only"
	@echo "  run-fuzz_normalizer   Run normalizer fuzzer only"
	@echo "  run-fuzz_dedup     Run dedup fuzzer only"
	@echo "  clean              Remove build artifacts and findings"
	@echo ""
	@echo "Requirements: afl++ (apt install afl++)"
