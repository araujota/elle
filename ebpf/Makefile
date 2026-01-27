# elled-ebpfd Makefile
# CO-RE eBPF collector using libbpf
#
# Requirements:
#   Ubuntu 24.04: apt install clang llvm libbpf-dev bpftool libelf-dev zlib1g-dev libjansson-dev

.PHONY: all clean install vmlinux help test

# =============================================================================
# Configuration
# =============================================================================

# Output directory
BUILD_DIR := build

# BPF object files
BPF_OBJS := $(BUILD_DIR)/oom.bpf.o \
            $(BUILD_DIR)/exec.bpf.o \
            $(BUILD_DIR)/block_io.bpf.o \
            $(BUILD_DIR)/tcp_retrans.bpf.o \
            $(BUILD_DIR)/cap_deny.bpf.o \
            $(BUILD_DIR)/file_deny.bpf.o \
            $(BUILD_DIR)/mount.bpf.o

# Userspace objects
USER_OBJS := $(BUILD_DIR)/main.o \
             $(BUILD_DIR)/collector.o \
             $(BUILD_DIR)/socket.o \
             $(BUILD_DIR)/json.o \
             $(BUILD_DIR)/config.o

# Final binary
TARGET := $(BUILD_DIR)/elled-ebpfd

# Compiler settings
CLANG := clang
LLC := llc
CC := gcc

# BPF compilation flags
BPF_CFLAGS := -g -O2 \
              -target bpf \
              -D__TARGET_ARCH_x86 \
              -I./bpf \
              -I/usr/include/bpf \
              -Wall -Wno-unused-value -Wno-pointer-sign -Wno-compare-distinct-pointer-types

# Userspace compilation flags
USER_CFLAGS := -g -O2 -Wall -Wextra -Werror \
               -I./user \
               -I./bpf \
               -I/usr/include/bpf \
               -D_GNU_SOURCE

# Linker flags
LDFLAGS := -lbpf -lelf -lz -ljansson

# Kernel BTF path
BTF_PATH := /sys/kernel/btf/vmlinux

# =============================================================================
# Targets
# =============================================================================

help:
	@echo "elled-ebpfd Build System"
	@echo ""
	@echo "Targets:"
	@echo "  all        Build the eBPF collector (default)"
	@echo "  vmlinux    Generate vmlinux.h from kernel BTF"
	@echo "  clean      Remove build artifacts"
	@echo "  install    Install to /usr/bin"
	@echo "  test       Run tests"
	@echo ""
	@echo "Debug:"
	@echo "  run        Run collector with stdout output"
	@echo "  run-verbose  Run with verbose logging"
	@echo ""
	@echo "Requirements:"
	@echo "  apt install clang llvm libbpf-dev bpftool libelf-dev zlib1g-dev libjansson-dev"

all: $(TARGET)

# Create build directory
$(BUILD_DIR):
	mkdir -p $(BUILD_DIR)

# =============================================================================
# vmlinux.h Generation
# =============================================================================

# Generate vmlinux.h from kernel BTF
bpf/vmlinux.h: | $(BUILD_DIR)
	@if [ ! -f $(BTF_PATH) ]; then \
		echo "ERROR: BTF not available at $(BTF_PATH)"; \
		echo "Ensure kernel has CONFIG_DEBUG_INFO_BTF=y"; \
		exit 1; \
	fi
	@echo "Generating vmlinux.h from kernel BTF..."
	bpftool btf dump file $(BTF_PATH) format c > $@
	@echo "Generated $@ ($(shell wc -l < $@) lines)"

vmlinux: bpf/vmlinux.h

# =============================================================================
# BPF Object Compilation
# =============================================================================

# Compile BPF programs to .o
$(BUILD_DIR)/%.bpf.o: bpf/%.bpf.c bpf/elled.h bpf/vmlinux.h | $(BUILD_DIR)
	@echo "  BPF   $<"
	$(CLANG) $(BPF_CFLAGS) -c $< -o $@

# =============================================================================
# Skeleton Generation
# =============================================================================

# Generate BPF skeletons from .o files
$(BUILD_DIR)/%.skel.h: $(BUILD_DIR)/%.bpf.o
	@echo "  SKEL  $@"
	bpftool gen skeleton $< > $@

# Dependency: all skeletons needed before userspace compilation
SKELETONS := $(BUILD_DIR)/oom.skel.h \
             $(BUILD_DIR)/exec.skel.h \
             $(BUILD_DIR)/block_io.skel.h \
             $(BUILD_DIR)/tcp_retrans.skel.h \
             $(BUILD_DIR)/cap_deny.skel.h \
             $(BUILD_DIR)/file_deny.skel.h \
             $(BUILD_DIR)/mount.skel.h

# =============================================================================
# Userspace Compilation
# =============================================================================

# Compile userspace .c to .o (depends on skeletons)
$(BUILD_DIR)/%.o: user/%.c $(SKELETONS) | $(BUILD_DIR)
	@echo "  CC    $<"
	$(CC) $(USER_CFLAGS) -I$(BUILD_DIR) -c $< -o $@

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
	@echo "Installing elled-ebpfd..."
	install -D -m 0755 $(TARGET) /usr/bin/elled-ebpfd
	@echo "Installed to /usr/bin/elled-ebpfd"

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

# Don't delete vmlinux.h on clean (it's expensive to regenerate)
distclean: clean
	rm -f bpf/vmlinux.h
