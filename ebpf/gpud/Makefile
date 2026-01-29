# SPDX-License-Identifier: MIT
# Makefile for elled-gpud
#
# Dependencies:
#   apt install gcc libjansson-dev
#
# Build:
#   make
#
# Note: No NVML/CUDA build dependency - we dlopen libnvidia-ml.so.1 at runtime

# Compiler settings
CC := gcc
CFLAGS := -g -O2 -Wall -Wextra -Werror -D_GNU_SOURCE

# Dependencies
JANSSON_CFLAGS := $(shell pkg-config --cflags jansson 2>/dev/null)
JANSSON_LIBS := $(shell pkg-config --libs jansson 2>/dev/null || echo "-ljansson")

CFLAGS += $(JANSSON_CFLAGS)
LDFLAGS := $(JANSSON_LIBS) -ldl

# Build directory
BUILD_DIR := build

# Source files
SRCS := \
	main.c \
	nvml.c \
	client.c \
	collector.c \
	json.c

# Object files
OBJS := $(patsubst %.c,$(BUILD_DIR)/%.o,$(SRCS))

# Target binary
TARGET := $(BUILD_DIR)/elled-gpud

# Default target
.PHONY: all
all: $(TARGET)

# Create build directory
$(BUILD_DIR):
	mkdir -p $(BUILD_DIR)

# Compile source files
$(BUILD_DIR)/%.o: %.c | $(BUILD_DIR)
	$(CC) $(CFLAGS) -c $< -o $@

# Link binary
$(TARGET): $(OBJS)
	$(CC) $(CFLAGS) $^ -o $@ $(LDFLAGS)
	@echo "Built: $@"

# Clean build artifacts
.PHONY: clean
clean:
	rm -rf $(BUILD_DIR)

# Install
.PHONY: install
install: $(TARGET)
	install -D -m 755 $(TARGET) /usr/local/sbin/elled-gpud
	@echo "Installed to /usr/local/sbin/elled-gpud"

# Uninstall
.PHONY: uninstall
uninstall:
	rm -f /usr/local/sbin/elled-gpud

# Check dependencies
.PHONY: check-deps
check-deps:
	@echo "Checking dependencies..."
	@echo ""
	@echo "Required: libjansson-dev"
	@echo "Runtime:  libnvidia-ml.so.1 (NVIDIA driver)"
	@echo ""
	@echo "To install on Ubuntu/Debian:"
	@echo "  sudo apt install gcc libjansson-dev"
	@echo ""
	@if pkg-config --exists jansson 2>/dev/null; then \
		echo "[OK] jansson"; \
	else \
		echo "[MISSING] libjansson-dev"; \
	fi
	@echo ""
	@if [ -f /usr/lib/x86_64-linux-gnu/libnvidia-ml.so.1 ] || \
	   [ -f /usr/lib64/libnvidia-ml.so.1 ] || \
	   ldconfig -p | grep -q libnvidia-ml.so.1; then \
		echo "[OK] libnvidia-ml.so.1 (runtime)"; \
	else \
		echo "[INFO] libnvidia-ml.so.1 not found (needed at runtime for GPU monitoring)"; \
	fi

# Debug build
.PHONY: debug
debug: CFLAGS += -DDEBUG -fsanitize=address -fsanitize=undefined
debug: LDFLAGS += -fsanitize=address -fsanitize=undefined
debug: clean $(TARGET)

# Run (for testing)
.PHONY: run
run: $(TARGET)
	./$(TARGET) -v

# Show help
.PHONY: help
help:
	@echo "Targets:"
	@echo "  all        - Build elled-gpud (default)"
	@echo "  clean      - Remove build artifacts"
	@echo "  install    - Install to /usr/local/sbin"
	@echo "  uninstall  - Remove installed binary"
	@echo "  check-deps - Verify required libraries are installed"
	@echo "  debug      - Build with AddressSanitizer"
	@echo "  run        - Build and run with verbose output"
	@echo "  help       - Show this message"
