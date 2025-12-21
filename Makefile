ifneq "$(firstword $(sort $(MAKE_VERSION) 4.4))" "4.4"
$(error FATAL: requires GNU Make 4.4 or later.)
endif

# Rigor: Eliminate legacy behavior and ensure clean termination.
.POSIX:
.SUFFIXES:
.DELETE_ON_ERROR:

# Efficiency and Control: Disable implicit logic.
MAKEFLAGS += --no-builtin-rules --no-builtin-variables
# Parallelism with synchronized output (Make 4.0+)
MAKEFLAGS += -j -Otarget
# GNU specific flags (Make 4.0+): Enforce variable definition rigor (Make 4.4 control).
GNUMAKEFLAGS += --warn=undefined-vars

# 2. Project Structure
# =============================================================================

# Directories (Use POSIX simple expansion ::=)
BUILD_DIR    ::= build
OBJ_DIR      ::= $(BUILD_DIR)/obj
BIN_DIR      ::= $(BUILD_DIR)/bin
PROTOCOL_DIR ::= protocols
SHADER_DIR   ::= shaders

TARGET ::= $(BIN_DIR)/walle

# Core Application Sources (Located in root)
APP_SOURCES ::= walle.c shiro.c

# 3. Toolchain and C23 Compliance Flags
# =============================================================================

CC?::= gcc
PKG_CONFIG?::= pkg-config
RM ::= rm -f

C23_STRICT ::=\
    -std=c23 \
    -Wall -Wextra -Wpedantic \
    -Wshadow  \
    -Wimplicit-fallthrough

# Usage:
#   make              (Debug: Assertions ON, Symbols ON, -O0/O2)
#   make MODE=release (Release: NDEBUG Defined, -O3, LTO, Strip Symbols)
MODE ?= DEBUG

ifeq ($(MODE),release)
    # -march=native: Unlocks AVX/SIMD instructions for the host CPU.
    # -flto=auto: Link Time Optimization.
    CPPFLAGS += -DNDEBUG
    C23_OPTIMIZE := -O3 -march=native -flto=auto -fno-plt
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

ifneq ($(strip $(SANITIZER)),)
    C23_SECURITY += -fsanitize=address
endif

CFLAGS  ::= $(C23_STRICT) $(C23_OPTIMIZE) $(C23_SECURITY)
LDFLAGS ::=
LDLIBS  ::= -lm

# Automatic Dependency Generation (Tracks headers and C23 #embed assets like shaders)
DEPFLAGS ::= -MMD -MP

# Base Include Paths
CFLAGS += -I. -I$(PROTOCOL_DIR)

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
# processes with many small allocations (recommended by libvips documentation).
JEMALLOC_DEPS ::= jemalloc

JEMALLOC_CFLAGS != $(PKG_CONFIG) --cflags $(JEMALLOC_DEPS)
$(call CHECK_STATUS)
JEMALLOC_LDLIBS != $(PKG_CONFIG) --libs $(JEMALLOC_DEPS)
$(call CHECK_STATUS)

CFLAGS += $(JEMALLOC_CFLAGS)
LDLIBS += $(JEMALLOC_LDLIBS)

# 5.5 System Probing (D-Bus / GameMode Integration)
# =============================================================================
# libsystemd provides sd-bus for monitoring org.freedesktop.portal.GameMode
SYSTEMD_DEPS ::= libsystemd

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

# Conditionally add WLR support
ifeq ($(WLR_LAYER_SHELL_XML),)
$(warning WARNING: wlr-layer-shell XML not found. Building without layer shell support.)
else
GENERATED_SOURCES += $(WLR_C)
GENERATED_HEADERS += $(WLR_H)
endif

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

.PHONY: all clean fuzz

all: $(TARGET)

# --- Linking Rule ---
$(TARGET): $(OBJECTS) | $(BIN_DIR)
	@echo "MODE: $(MODE)"
	@echo "[LD] $@"
	$(CC) $(LDFLAGS) $^ $(LDLIBS) -o $@

# --- Unified Compilation Rule ---
# This single pattern handles sources in the root, vendor/, and protocols/ directories.
$(OBJ_DIR)/%.c.o: %.c
	@echo "[CC] $<"
	@# Ensure the specific output subdirectory (e.g., build/obj/vendor/inih/src) exists.
	@mkdir -p $(@D)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(DEPFLAGS) -c $< -o $@

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
$(BIN_DIR) $(PROTOCOL_DIR):
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
