# elled-probed Makefile
# Userspace polling daemon for ground truth monitoring
#
# Requirements:
#   Ubuntu 24.04: apt install gcc libjansson-dev libsystemd-dev

.PHONY: all clean install help test

# =============================================================================
# Configuration
# =============================================================================

# Output directory
BUILD_DIR := build

# Userspace objects
USER_OBJS := $(BUILD_DIR)/main.o \
             $(BUILD_DIR)/scheduler.o \
             $(BUILD_DIR)/socket.o \
             $(BUILD_DIR)/json.o \
             $(BUILD_DIR)/config.o \
             $(BUILD_DIR)/psi.o \
             $(BUILD_DIR)/systemd.o \
             $(BUILD_DIR)/dns.o \
             $(BUILD_DIR)/conntrack.o \
             $(BUILD_DIR)/hardware.o \
             $(BUILD_DIR)/auth.o

# Final binary
TARGET := $(BUILD_DIR)/elled-probed

# Compiler settings
CC := gcc

# Compilation flags
CFLAGS := -g -O2 -Wall -Wextra -Werror \
          -I. \
          -I./probes \
          -D_GNU_SOURCE

# Linker flags
LDFLAGS := -ljansson -lsystemd

# =============================================================================
# Targets
# =============================================================================

help:
	@echo "elled-probed Build System"
	@echo ""
	@echo "Targets:"
	@echo "  all        Build the polling daemon (default)"
	@echo "  clean      Remove build artifacts"
	@echo "  install    Install to /usr/bin"
	@echo "  test       Run tests"
	@echo ""
	@echo "Debug:"
	@echo "  run        Run daemon with stdout output"
	@echo "  run-verbose  Run with verbose logging"
	@echo ""
	@echo "Requirements:"
	@echo "  apt install gcc libjansson-dev libsystemd-dev"

all: $(TARGET)

# Create build directory
$(BUILD_DIR):
	mkdir -p $(BUILD_DIR)

# =============================================================================
# Compilation
# =============================================================================

# Compile main sources
$(BUILD_DIR)/main.o: main.c scheduler.h socket.h config.h | $(BUILD_DIR)
	@echo "  CC    $<"
	$(CC) $(CFLAGS) -c $< -o $@

$(BUILD_DIR)/scheduler.o: scheduler.c scheduler.h | $(BUILD_DIR)
	@echo "  CC    $<"
	$(CC) $(CFLAGS) -c $< -o $@

$(BUILD_DIR)/socket.o: socket.c socket.h | $(BUILD_DIR)
	@echo "  CC    $<"
	$(CC) $(CFLAGS) -c $< -o $@

$(BUILD_DIR)/json.o: json.c json.h probed.h | $(BUILD_DIR)
	@echo "  CC    $<"
	$(CC) $(CFLAGS) -c $< -o $@

$(BUILD_DIR)/config.o: config.c config.h | $(BUILD_DIR)
	@echo "  CC    $<"
	$(CC) $(CFLAGS) -c $< -o $@

# Compile probes
$(BUILD_DIR)/psi.o: probes/psi.c probes/psi.h probed.h | $(BUILD_DIR)
	@echo "  CC    $<"
	$(CC) $(CFLAGS) -c $< -o $@

$(BUILD_DIR)/systemd.o: probes/systemd.c probes/systemd.h probed.h | $(BUILD_DIR)
	@echo "  CC    $<"
	$(CC) $(CFLAGS) -c $< -o $@

$(BUILD_DIR)/dns.o: probes/dns.c probes/dns.h probed.h | $(BUILD_DIR)
	@echo "  CC    $<"
	$(CC) $(CFLAGS) -c $< -o $@

$(BUILD_DIR)/conntrack.o: probes/conntrack.c probes/conntrack.h probed.h | $(BUILD_DIR)
	@echo "  CC    $<"
	$(CC) $(CFLAGS) -c $< -o $@

$(BUILD_DIR)/hardware.o: probes/hardware.c probes/hardware.h probed.h | $(BUILD_DIR)
	@echo "  CC    $<"
	$(CC) $(CFLAGS) -c $< -o $@

$(BUILD_DIR)/auth.o: probes/auth.c probes/auth.h probed.h | $(BUILD_DIR)
	@echo "  CC    $<"
	$(CC) $(CFLAGS) -c $< -o $@

# Link final binary
$(TARGET): $(USER_OBJS)
	@echo "  LINK  $@"
	$(CC) $(USER_OBJS) $(LDFLAGS) -o $@
	@echo ""
	@echo "Built: $@"

# =============================================================================
# Installation
# =============================================================================

install: $(TARGET)
	@echo "Installing elled-probed..."
	install -D -m 0755 $(TARGET) /usr/bin/elled-probed
	@echo "Installed to /usr/bin/elled-probed"

# =============================================================================
# Debug/Test Targets
# =============================================================================

run: $(TARGET)
	sudo $(TARGET) --stdout

run-verbose: $(TARGET)
	sudo $(TARGET) --stdout --verbose

test:
	@echo "Running tests..."
	cd tests && python3 -m pytest -v

# =============================================================================
# Cleanup
# =============================================================================

clean:
	rm -rf $(BUILD_DIR)
