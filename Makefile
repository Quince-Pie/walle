ifneq "$(firstword $(sort $(MAKE_VERSION) 4.4))" "4.4"
$(error FATAL: requires GNU Make 4.4 or later.)
endif

# Rigor: Eliminate legacy behavior and ensure clean termination.
.SUFFIXES:
.DELETE_ON_ERROR:

# Efficiency and Control: Disable implicit logic.
MAKEFLAGS += --no-builtin-rules --no-builtin-variables
# Synchronized output when the user runs with -j (Make 4.0+).
MAKEFLAGS += -Otarget
MAKEFLAGS += --warn-undefined-variables

# 2. Project Structure
# =============================================================================

MODE ?= DEBUG
NATIVE ?= 0
ANALYZE ?=
SANITIZER ?=
TRACY ?= 0
PROFILE ::= $(if $(filter release,$(MODE)),release,debug)$(if $(filter 1,$(NATIVE)),-native)$(if $(strip $(SANITIZER)),-sanitized)$(if $(strip $(ANALYZE)),-analyzed)$(if $(filter 1,$(TRACY)),-tracy)

# Directories (Use POSIX simple expansion ::=)
BUILD_DIR    ::= build
OBJ_DIR      ::= $(BUILD_DIR)/obj/$(PROFILE)
BIN_DIR      ::= $(BUILD_DIR)/bin
PROFILE_BIN_DIR ::= $(BIN_DIR)/$(PROFILE)
TEST_DIR     ::= $(BUILD_DIR)/tests/$(PROFILE)
PROTOCOL_DIR ::= protocols
SHADER_DIR   ::= shaders

TARGET     ::= $(PROFILE_BIN_DIR)/walle
URING_TEST ::= $(TEST_DIR)/uring_smoke
TILDE_TEST ::= $(TEST_DIR)/tilde_smoke
TESTS      ::= $(URING_TEST) $(TILDE_TEST)

# Core Application Sources (Located in root)
APP_SOURCES ::= walle.c shiro.c tilde.c uring.c

# 3. Toolchain and C23 Compliance Flags
# =============================================================================

# NOT `?=`: at parse time the builtin CC ('cc') still counts as defined, so
# `?=` skips — and --no-builtin-variables then strips the builtin, leaving CC
# empty. Only an environment/command-line value may override.
ifneq ($(origin CC),environment)
ifneq ($(origin CC),command line)
CC := gcc
endif
endif
ifneq ($(origin PKG_CONFIG),environment)
ifneq ($(origin PKG_CONFIG),command line)
PKG_CONFIG := pkg-config
endif
endif
RM ::= rm -f
CPPFLAGS ::=

C23_STRICT ::=\
    -std=c23 \
    -Wall -Wextra -Wpedantic \
    -Wshadow  \
    -Wimplicit-fallthrough

# Usage:
#   make              (Debug: Assertions ON, Symbols ON, -O0/O2)
#   make MODE=release (Release: NDEBUG Defined, -O3, LTO, Strip Symbols)
# NATIVE=1 additionally enables -march=native (host-specific binary; never for
# distributed/packaged builds).

ifeq ($(MODE),release)
    # -flto=auto: Link Time Optimization.
    CPPFLAGS += -DNDEBUG
    C23_OPTIMIZE := -O3 -flto=auto -fno-plt
    ifeq ($(NATIVE),1)
        C23_OPTIMIZE += -march=native
    endif
else
    # -g3: Maximal debug information (macros included).
    # -ggdb: Expressive GDB extensions.
    # -Og: Optimize for debugging experience (preserves variable values).
    C23_OPTIMIZE := -Og -g3 -ggdb
endif

# Security Hardening
C23_SECURITY ::=\
    -fstack-protector-strong -D_FORTIFY_SOURCE=3

# Static Analysis
# Usage: make ANALYZE=1
# Note: For Clang, use 'scan-build make' instead of setting ANALYZE=1.
ifneq ($(strip $(ANALYZE)),)
    C23_SECURITY += -fanalyzer
endif

SANITIZER_FLAGS ::=
ifneq ($(strip $(SANITIZER)),)
    SANITIZER_FLAGS ::= -fsanitize=address,undefined
endif

CFLAGS  ::= $(C23_STRICT) $(C23_OPTIMIZE) $(C23_SECURITY) $(SANITIZER_FLAGS)
# Sanitizer runtimes and LTO both require the codegen flags at link time.
LDFLAGS ::= $(SANITIZER_FLAGS)
LDLIBS  ::= -lm

# pthreads are part of the C library on current glibc, but -pthread also
# supplies the compiler-side thread model and remains the portable contract.
CFLAGS  += -pthread
LDFLAGS += -pthread

# Automatic Dependency Generation (Tracks headers and C23 #embed assets like shaders)
DEPFLAGS ::= -MMD -MP

# Base Include Paths
CFLAGS += -I. -I$(PROTOCOL_DIR)

# Opt-in profiling build. The flake dev shells provide Tracy's installed
# headers and client library through the compiler wrapper; ordinary and
# packaged builds do not acquire instrumentation overhead or a Tracy runtime
# dependency.
ifeq ($(TRACY),1)
CPPFLAGS += -DWALLE_TRACY=1 -DTRACY_ENABLE=1 -DTRACY_ON_DEMAND=1
LDLIBS += -lTracyClient
endif

# Macro for rigorous status checking using .SHELLSTATUS (Make 4.2+).
CHECK_STATUS = $(if $(filter-out 0,$(strip $(.SHELLSTATUS))),$(error FATAL: Last shell command failed (Status $(.SHELLSTATUS))),)

# 4. External Dependency (INIH)
# =============================================================================
INIH_DEPS ::= inih

INIH_CFLAGS != $(PKG_CONFIG) --cflags $(INIH_DEPS)
$(call CHECK_STATUS)
INIH_LDLIBS != $(PKG_CONFIG) --libs $(INIH_DEPS)
$(call CHECK_STATUS)

CFLAGS += $(INIH_CFLAGS)
LDLIBS += $(INIH_LDLIBS)

# 5. System Probing (Wayland)
# =============================================================================

# Probe Wayland environment. Use != for immediate shell assignment (Make 4.0+).
WAYLAND_DEPS ::= wayland-client wayland-egl

WAYLAND_CFLAGS != $(PKG_CONFIG) --cflags $(WAYLAND_DEPS)
$(call CHECK_STATUS)
WAYLAND_LDLIBS != $(PKG_CONFIG) --libs $(WAYLAND_DEPS)
$(call CHECK_STATUS)
WAYLAND_PROTOCOLS_DIR != $(PKG_CONFIG) --variable=pkgdatadir wayland-protocols
$(call CHECK_STATUS)

# Locate the wayland-scanner binary robustly.
WAYLAND_SCANNER != $(PKG_CONFIG) --variable=wayland_scanner wayland-scanner
$(call CHECK_STATUS)

# Apply configuration
CFLAGS += $(WAYLAND_CFLAGS)
LDLIBS += $(WAYLAND_LDLIBS)

# 5.1 System Probing (Vips)
# =============================================================================
VIPS_DEPS ::= vips

VIPS_CFLAGS != $(PKG_CONFIG) --cflags $(VIPS_DEPS)
$(call CHECK_STATUS)
VIPS_LDLIBS != $(PKG_CONFIG) --libs $(VIPS_DEPS)
$(call CHECK_STATUS)

CFLAGS += $(VIPS_CFLAGS)
LDLIBS += $(VIPS_LDLIBS)

# 5.2 System Probing (Hashing)
# =============================================================================
HASH_DEPS ::= libxxhash

HASH_CFLAGS != $(PKG_CONFIG) --cflags $(HASH_DEPS)
$(call CHECK_STATUS)
HASH_LDLIBS != $(PKG_CONFIG) --libs $(HASH_DEPS)
$(call CHECK_STATUS)

CFLAGS += $(HASH_CFLAGS)
LDLIBS += $(HASH_LDLIBS)

# 5.3 System Probing (Rendering)
# =============================================================================
RENDER_DEPS ::= glesv2 egl

RENDER_CFLAGS != $(PKG_CONFIG) --cflags $(RENDER_DEPS)
$(call CHECK_STATUS)
RENDER_LDLIBS != $(PKG_CONFIG) --libs $(RENDER_DEPS)
$(call CHECK_STATUS)

CFLAGS += $(RENDER_CFLAGS)
LDLIBS += $(RENDER_LDLIBS)

# 5.4 System Probing (Memory Allocator)
# =============================================================================
# jemalloc mitigates glibc memory fragmentation in long-running multi-threaded
# processes with many small allocations. Sanitizers must own the allocation
# entry points so their interceptors remain compatible with Mesa's LLVM stack.
ifeq ($(strip $(SANITIZER)),)
JEMALLOC_DEPS ::= jemalloc

JEMALLOC_CFLAGS != $(PKG_CONFIG) --cflags $(JEMALLOC_DEPS)
$(call CHECK_STATUS)
JEMALLOC_LDLIBS != $(PKG_CONFIG) --libs $(JEMALLOC_DEPS)
$(call CHECK_STATUS)

CFLAGS += $(JEMALLOC_CFLAGS)
LDLIBS += $(JEMALLOC_LDLIBS)
endif

# 5.5 System Probing (D-Bus / GameMode Integration)
# =============================================================================
# libsystemd provides sd-bus for monitoring org.freedesktop.portal.GameMode
SYSTEMD_DEPS ::= libsystemd liburing

SYSTEMD_CFLAGS != $(PKG_CONFIG) --cflags $(SYSTEMD_DEPS)
$(call CHECK_STATUS)
SYSTEMD_LDLIBS != $(PKG_CONFIG) --libs $(SYSTEMD_DEPS)
$(call CHECK_STATUS)

CFLAGS += $(SYSTEMD_CFLAGS)
LDLIBS += $(SYSTEMD_LDLIBS)

# 6. Wayland Protocol Definitions
# =============================================================================

# Protocol XML Sources
XDG_SHELL_XML ::= $(WAYLAND_PROTOCOLS_DIR)/stable/xdg-shell/xdg-shell.xml

# Robustly locate WLR protocols (often requires the 'wlr-protocols' package).
# Attempt pkg-config first, fallback to standard directory if pkg-config fails or returns empty.
WLR_PROTOCOLS_DIR != $(PKG_CONFIG) --variable=pkgdatadir wlr-protocols 2>/dev/null || echo $(WAYLAND_PROTOCOLS_DIR)
WLR_LAYER_SHELL_XML := $(wildcard $(WLR_PROTOCOLS_DIR)/unstable/wlr-layer-shell-unstable-v1.xml)

# Generated Outputs
XDG_C ::= $(PROTOCOL_DIR)/xdg-shell.c
XDG_H ::= $(PROTOCOL_DIR)/xdg-shell.h
WLR_C ::= $(PROTOCOL_DIR)/wlr-layer-shell-unstable-v1.c
WLR_H ::= $(PROTOCOL_DIR)/wlr-layer-shell-unstable-v1.h

GENERATED_SOURCES ::= $(XDG_C)
GENERATED_HEADERS ::= $(XDG_H)

# wlr-layer-shell is a hard requirement: walle renders exclusively onto
# zwlr_layer_shell_v1 background surfaces and is useless without it.
ifeq ($(WLR_LAYER_SHELL_XML),)
$(error FATAL: wlr-layer-shell-unstable-v1.xml not found (install wlr-protocols))
endif
GENERATED_SOURCES += $(WLR_C)
GENERATED_HEADERS += $(WLR_H)

# Prevent Make from deleting generated files (Make 4.4)
.NOTINTERMEDIATE: $(GENERATED_SOURCES) $(GENERATED_HEADERS)

# 7. Source Mapping and Build Rules
# =============================================================================

# Aggregate all sources
ALL_SOURCES ::= $(APP_SOURCES) $(GENERATED_SOURCES)

# Map sources to objects in OBJ_DIR, preserving directory structure.
# Use the %.c.o pattern (e.g., build/obj/protocols/xdg-shell.c.o)
# This robustly prevents object file collisions if source names overlap across directories.
OBJECTS ::= $(ALL_SOURCES:%.c=$(OBJ_DIR)/%.c.o)
DEPS    ::= $(OBJECTS:.o=.d)

.PHONY: all check clean fuzz

all: $(TARGET) | $(BIN_DIR)
	@ln -sfn $(PROFILE)/walle $(BIN_DIR)/walle

check: $(TESTS)
	@echo "[TEST] raw io_uring reactor"
	$(URING_TEST)
	@echo "[TEST] tilde expansion"
	$(TILDE_TEST)

$(URING_TEST): tests/uring_smoke.c uring.c uring.h Makefile | $(TEST_DIR)
	@echo "[CCLD] $@"
	$(CC) $(CPPFLAGS) $(CFLAGS) tests/uring_smoke.c uring.c -o $@

$(TILDE_TEST): tests/tilde_smoke.c tilde.c tilde.h Makefile | $(TEST_DIR)
	@echo "[CCLD] $@"
	$(CC) $(CPPFLAGS) $(CFLAGS) tests/tilde_smoke.c tilde.c -o $@

# --- Linking Rule ---
$(TARGET): $(OBJECTS) Makefile | $(PROFILE_BIN_DIR)
	@echo "MODE: $(MODE)"
	@echo "[LD] $@"
	$(CC) $(CFLAGS) $(LDFLAGS) $(filter-out Makefile,$^) $(LDLIBS) -o $@

# --- Unified Compilation Rule ---
# This single pattern handles sources in the root, vendor/, and protocols/ directories.
$(OBJ_DIR)/%.c.o: %.c Makefile
	@echo "[CC] $<"
	@# Ensure the specific output subdirectory (e.g., build/obj/vendor/inih/src) exists.
	@mkdir -p $(@D)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(DEPFLAGS) -c $< -o $@

# Cold-start correctness: on the first build no .d files exist yet, so objects
# must explicitly depend on the generated protocol headers or a parallel build
# races the scanner (walle.c includes wlr-layer-shell-unstable-v1.h).
$(OBJECTS): $(GENERATED_HEADERS)

# --- Protocol Generation Rules (Grouped Targets - Make 4.3+) ---

# Rule for XDG Shell
# The "T1 T2 &:" syntax ensures the recipe is executed once to generate both targets atomically.
$(XDG_C) $(XDG_H) &: $(XDG_SHELL_XML) | $(PROTOCOL_DIR)
	@echo "[WL_SCAN] XDG Shell ($<)"
	# wayland-scanner requires separate invocations for header and code.
	$(WAYLAND_SCANNER) client-header $< $(XDG_H)
	$(WAYLAND_SCANNER) private-code $< $(XDG_C)

# Rule for WLR Layer Shell (Conditional)
ifneq ($(WLR_LAYER_SHELL_XML),)
$(WLR_C) $(WLR_H) &: $(WLR_LAYER_SHELL_XML) | $(PROTOCOL_DIR)
	@echo "[WL_SCAN] WLR Layer Shell ($<)"
	# Use the first match if wildcard found multiple (though unlikely here).
	$(WAYLAND_SCANNER) client-header $(firstword $<) $(WLR_H)
	$(WAYLAND_SCANNER) private-code $(firstword $<) $(WLR_C)
endif

# --- Infrastructure (Directories) ---
# Order-only prerequisites (|) ensure creation without triggering unnecessary rebuilds.
$(BIN_DIR) $(PROFILE_BIN_DIR) $(TEST_DIR) $(PROTOCOL_DIR):
	@mkdir -p $@

# Include generated dependency files.
-include $(DEPS)

# 8. Utility Targets
# =============================================================================

# Fuzz Testing (Make 4.4+)
# Validates Makefile integrity by randomizing prerequisite evaluation order.
fuzz:
	@echo "[FUZZ] Testing parallel execution integrity (--shuffle)..."
	$(MAKE) clean
	# Execute the build with randomized order to expose missing dependencies.
	$(MAKE) all --shuffle

clean:
	@echo "[CLEAN] Build artifacts..."
	$(RM) -r $(BUILD_DIR)
	@echo "[CLEAN] Generated protocols..."
	$(RM) -r $(PROTOCOL_DIR)
