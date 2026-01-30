# SPDX-License-Identifier: MIT
# Makefile for elled-telemetryd
#
# Dependencies:
#   apt install gcc libjansson-dev libpcre2-dev libssl-dev libsystemd-dev
#
# Optional eBPF support (ENABLE_EBPF=1):
#   apt install clang llvm libbpf-dev libelf-dev
#
# Build without eBPF (default):
#   make
#
# Build with eBPF:
#   make ENABLE_EBPF=1

# Compiler settings
CC := gcc
CFLAGS := -g -O2 -Wall -Wextra -Werror -D_GNU_SOURCE

# Try pkg-config, fall back to manual flags if not available
JANSSON_CFLAGS := $(shell pkg-config --cflags jansson 2>/dev/null)
JANSSON_LIBS := $(shell pkg-config --libs jansson 2>/dev/null || echo "-ljansson")

PCRE2_CFLAGS := $(shell pkg-config --cflags libpcre2-8 2>/dev/null)
PCRE2_LIBS := $(shell pkg-config --libs libpcre2-8 2>/dev/null || echo "-lpcre2-8")

OPENSSL_CFLAGS := $(shell pkg-config --cflags openssl 2>/dev/null)
OPENSSL_LIBS := $(shell pkg-config --libs openssl 2>/dev/null || echo "-lssl -lcrypto")

SYSTEMD_CFLAGS := $(shell pkg-config --cflags libsystemd 2>/dev/null)
SYSTEMD_LIBS := $(shell pkg-config --libs libsystemd 2>/dev/null || echo "-lsystemd")

CFLAGS += $(JANSSON_CFLAGS) $(PCRE2_CFLAGS) $(OPENSSL_CFLAGS) $(SYSTEMD_CFLAGS)
LDFLAGS := $(JANSSON_LIBS) $(PCRE2_LIBS) $(OPENSSL_LIBS) $(SYSTEMD_LIBS)

# Optional eBPF support
ifdef ENABLE_EBPF
LIBBPF_CFLAGS := $(shell pkg-config --cflags libbpf 2>/dev/null)
LIBBPF_LIBS := $(shell pkg-config --libs libbpf 2>/dev/null || echo "-lbpf -lelf -lz")
CFLAGS += $(LIBBPF_CFLAGS) -DENABLE_EBPF=1
LDFLAGS += $(LIBBPF_LIBS)
# Add include path for generated skeletons
CFLAGS += -I../bpf/.output
endif

# Build directory
BUILD_DIR := build

# Source files (core)
SRCS := \
	main.c \
	socket.c \
	json.c \
	normalize/normalizer.c \
	normalize/category.c \
	normalize/entity.c \
	normalize/fingerprint.c \
	normalize/dedup.c \
	watchers/journal.c \
	watchers/docker.c \
	watchers/inotify.c \
	probes/scheduler.c \
	probes/psi.c \
	probes/memory.c \
	probes/disk.c \
	probes/network.c \
	probes/thermal.c \
	probes/package.c \
	probes/port.c \
	probes/connpool.c \
	probes/topproc.c \
	probes/ioperf.c \
	probes/cgroup.c

# Add eBPF loader when enabled
ifdef ENABLE_EBPF
SRCS += ebpf/loader.c
endif

# Object files
OBJS := $(patsubst %.c,$(BUILD_DIR)/%.o,$(SRCS))

# Target binary
TARGET := $(BUILD_DIR)/elled-telemetryd

# Default target
.PHONY: all
all: $(TARGET)

# Create build directories
$(BUILD_DIR):
	mkdir -p $(BUILD_DIR)/normalize $(BUILD_DIR)/watchers $(BUILD_DIR)/probes $(BUILD_DIR)/ebpf

# Compile source files
$(BUILD_DIR)/%.o: %.c | $(BUILD_DIR)
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -c $< -o $@

# Link binary
$(TARGET): $(OBJS)
	$(CC) $(CFLAGS) $^ -o $@ $(LDFLAGS)
ifdef ENABLE_EBPF
	@echo "Built: $@ (with eBPF support)"
else
	@echo "Built: $@ (without eBPF)"
endif

# Clean build artifacts
.PHONY: clean
clean:
	rm -rf $(BUILD_DIR)

# Install
.PHONY: install
install: $(TARGET)
	install -D -m 755 $(TARGET) /usr/local/sbin/elled-telemetryd
	@echo "Installed to /usr/local/sbin/elled-telemetryd"

# Uninstall
.PHONY: uninstall
uninstall:
	rm -f /usr/local/sbin/elled-telemetryd

# Check dependencies
.PHONY: check-deps
check-deps:
	@echo "Checking dependencies..."
	@echo ""
	@echo "Required packages: libjansson-dev libpcre2-dev libssl-dev libsystemd-dev"
	@echo "Optional (eBPF):   clang llvm libbpf-dev libelf-dev"
	@echo ""
	@echo "To install on Ubuntu/Debian:"
	@echo "  sudo apt install gcc libjansson-dev libpcre2-dev libssl-dev libsystemd-dev"
	@echo ""
	@echo "For eBPF support, also install:"
	@echo "  sudo apt install clang llvm libbpf-dev libelf-dev"
	@echo ""
	@echo "=== Required ==="
	@if pkg-config --exists jansson 2>/dev/null; then \
		echo "[OK] jansson"; \
	else \
		echo "[MISSING] libjansson-dev"; \
	fi
	@if pkg-config --exists libpcre2-8 2>/dev/null; then \
		echo "[OK] pcre2"; \
	else \
		echo "[MISSING] libpcre2-dev"; \
	fi
	@if pkg-config --exists openssl 2>/dev/null; then \
		echo "[OK] openssl"; \
	else \
		echo "[MISSING] libssl-dev"; \
	fi
	@if pkg-config --exists libsystemd 2>/dev/null; then \
		echo "[OK] systemd"; \
	else \
		echo "[MISSING] libsystemd-dev"; \
	fi
	@echo ""
	@echo "=== Optional (eBPF) ==="
	@if pkg-config --exists libbpf 2>/dev/null; then \
		echo "[OK] libbpf"; \
	else \
		echo "[MISSING] libbpf-dev (optional)"; \
	fi
	@echo ""
	@echo "Note: Build may still work if libraries are installed but pkg-config files are missing."

# Debug build
.PHONY: debug
debug: CFLAGS += -DDEBUG -fsanitize=address -fsanitize=undefined
debug: LDFLAGS += -fsanitize=address -fsanitize=undefined
debug: clean $(TARGET)

# Build with eBPF support
.PHONY: ebpf
ebpf:
	$(MAKE) ENABLE_EBPF=1

# Run tests (placeholder)
.PHONY: test
test: $(TARGET)
	@echo "Running tests..."
	@# TODO: Add unit tests
	@echo "No tests implemented yet."

# Show help
.PHONY: help
help:
	@echo "Targets:"
	@echo "  all        - Build elled-telemetryd (default, without eBPF)"
	@echo "  ebpf       - Build with eBPF support (requires libbpf)"
	@echo "  clean      - Remove build artifacts"
	@echo "  install    - Install to /usr/local/sbin"
	@echo "  uninstall  - Remove installed binary"
	@echo "  check-deps - Verify required libraries are installed"
	@echo "  debug      - Build with AddressSanitizer"
	@echo "  test       - Run tests"
	@echo "  help       - Show this message"
	@echo ""
	@echo "Variables:"
	@echo "  ENABLE_EBPF=1  - Enable eBPF program loading (requires libbpf)"
	@echo ""
	@echo "Examples:"
	@echo "  make                  # Build without eBPF"
	@echo "  make ENABLE_EBPF=1    # Build with eBPF"
	@echo "  make ebpf             # Same as ENABLE_EBPF=1"
