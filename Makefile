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
# GCC's LTO wrapper invokes a recursive makefile that references this optional
# GNU variable. Export an explicit empty value so our warning policy remains
# useful without producing a false warning at link time.
export GNUMAKEFLAGS :=

# 2. Project Structure
# =============================================================================

# Build variants must never share objects: optimization, instrumentation,
# analyzer, and sanitizer flags all get their own output directory.
MODE ?= DEBUG
NATIVE ?= 0
ANALYZE ?=
SANITIZER ?=
TRACY ?= 0
PROFILE ::= $(if $(filter release,$(MODE)),release,debug)$(if $(filter 1,$(NATIVE)),-native)$(if $(strip $(SANITIZER)),-sanitized)$(if $(strip $(ANALYZE)),-analyzed)$(if $(filter 1,$(TRACY)),-tracy)

# Directories (Use POSIX simple expansion ::=)
BUILD_DIR       ::= build
OBJ_DIR         ::= $(BUILD_DIR)/obj/$(PROFILE)
BIN_DIR         ::= $(BUILD_DIR)/bin
PROFILE_BIN_DIR ::= $(BIN_DIR)/$(PROFILE)
PROTOCOL_DIR    ::= protocols
SHADER_DIR      ::= shaders
SPIRV_DIR       ::= $(BUILD_DIR)/shaders

TARGET        ::= $(PROFILE_BIN_DIR)/walle
ACTIVE_TARGET ::= $(BIN_DIR)/walle

# Core Application Sources (Located in root)
APP_SOURCES ::= walle.c shiro.c vulkan_renderer.c \
	parity/liquid_glass_reveal_mask_model.c parity/liquid_glass_postguard.c \
	parity/liquid_glass_raster.c parity/liquid_glass_pyramid.c \
	parity/liquid_glass_static_regular.c parity/liquid_glass_selected_region.c \
	parity/liquid_glass_transition_frame.c parity/liquid_glass_transition_profile.c \
	parity/liquid_glass_materialize.c parity/liquid_glass_darwin_powf.c \
	parity/liquid_glass_resolved_color.c

SPIRV_TARGETS ::= $(SPIRV_DIR)/maskVertex.spv $(SPIRV_DIR)/maskFragment.spv \
	$(SPIRV_DIR)/composeVertex.spv $(SPIRV_DIR)/composeFragment.spv
SPIRV_DEPS ::= $(SPIRV_TARGETS:%=%.d)

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
ifneq ($(origin SLANGC),environment)
ifneq ($(origin SLANGC),command line)
SLANGC := slangc
endif
endif
ifneq ($(origin SPIRV_VAL),environment)
ifneq ($(origin SPIRV_VAL),command line)
SPIRV_VAL := spirv-val
endif
endif
ifneq ($(origin SPIRV_DIS),environment)
ifneq ($(origin SPIRV_DIS),command line)
SPIRV_DIS := spirv-dis
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
#   make                       (Debug: assertions and symbols)
#   make MODE=release          (Release: NDEBUG, -O3, LTO)
#   make MODE=release TRACY=1  (separate opt-in Tracy profile)
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

# Supply the compiler-side thread model as well as the linker contract.
CFLAGS  += -pthread
LDFLAGS += -pthread

# Automatic Dependency Generation (Tracks headers and C23 #embed assets like shaders)
DEPFLAGS ::= -MMD -MP

# Base Include Paths
CFLAGS += -I. -I$(PROTOCOL_DIR)

# The development shell exposes Tracy's installed headers and client library
# through its compiler wrapper. Normal builds retain no profiling overhead or
# Tracy runtime dependency.
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
WAYLAND_DEPS ::= wayland-client

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

# 5.3 System Probing (Vulkan 1.4)
# =============================================================================
RENDER_DEPS ::= vulkan

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

# 5.4.1 Linux DRM format/modifier definitions
# =============================================================================
LIBDRM_DEPS ::= libdrm

LIBDRM_CFLAGS != $(PKG_CONFIG) --cflags $(LIBDRM_DEPS)
$(call CHECK_STATUS)
LIBDRM_LDLIBS != $(PKG_CONFIG) --libs $(LIBDRM_DEPS)
$(call CHECK_STATUS)

CFLAGS += $(LIBDRM_CFLAGS)
LDLIBS += $(LIBDRM_LDLIBS)

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

# 5.6 System Probing (io_uring Event Core)
# =============================================================================
# liburing >= 2.4 (sync-cancel API); runtime kernel floor is 5.15, probed at
# startup — see the event-core section in walle.c.
URING_DEPS ::= liburing >= 2.4

URING_CFLAGS != $(PKG_CONFIG) --cflags '$(URING_DEPS)'
$(call CHECK_STATUS)
URING_LDLIBS != $(PKG_CONFIG) --libs '$(URING_DEPS)'
$(call CHECK_STATUS)

# -isystem: liburing's UAPI headers use zero-size arrays that trip -Wpedantic.
CFLAGS += $(patsubst -I%,-isystem %,$(URING_CFLAGS))
LDLIBS += $(URING_LDLIBS)

# 6. Wayland Protocol Definitions
# =============================================================================

# Protocol XML Sources
XDG_SHELL_XML ::= $(WAYLAND_PROTOCOLS_DIR)/stable/xdg-shell/xdg-shell.xml
LINUX_DMABUF_XML ::= $(WAYLAND_PROTOCOLS_DIR)/stable/linux-dmabuf/linux-dmabuf-v1.xml

# Robustly locate WLR protocols (often requires the 'wlr-protocols' package).
# Attempt pkg-config first, fallback to standard directory if pkg-config fails or returns empty.
WLR_PROTOCOLS_DIR != $(PKG_CONFIG) --variable=pkgdatadir wlr-protocols 2>/dev/null || echo $(WAYLAND_PROTOCOLS_DIR)
WLR_LAYER_SHELL_XML := $(wildcard $(WLR_PROTOCOLS_DIR)/unstable/wlr-layer-shell-unstable-v1.xml)

# Generated Outputs
XDG_C ::= $(PROTOCOL_DIR)/xdg-shell.c
XDG_H ::= $(PROTOCOL_DIR)/xdg-shell.h
WLR_C ::= $(PROTOCOL_DIR)/wlr-layer-shell-unstable-v1.c
WLR_H ::= $(PROTOCOL_DIR)/wlr-layer-shell-unstable-v1.h
LINUX_DMABUF_C ::= $(PROTOCOL_DIR)/linux-dmabuf-v1.c
LINUX_DMABUF_H ::= $(PROTOCOL_DIR)/linux-dmabuf-v1.h

GENERATED_SOURCES ::= $(XDG_C) $(LINUX_DMABUF_C)
GENERATED_HEADERS ::= $(XDG_H) $(LINUX_DMABUF_H)

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

.PHONY: all activate clean fuzz reveal-best-known-corpus-gate \
	reveal-best-known-process-gate reveal-mask-model-gate reveal-raster-gate

all: $(TARGET) activate

activate: $(TARGET) | $(BIN_DIR)
	ln -sfn $(PROFILE)/walle $(ACTIVE_TARGET)

reveal-mask-model-gate:
	./parity/run_liquid_glass_reveal_mask_model_gate.sh

reveal-raster-gate: parity/raster_p25_selector_ceil_bits.bin \
		artifacts/apple-float-intrinsics-r8-30556057571.bin \
		parity/apple_fast_sqrt_correction_nibbles.bin
	bash parity/run_liquid_glass_reveal_raster_gate.sh

reveal-best-known-corpus-gate: reveal-best-known-process-gate

reveal-best-known-process-gate: $(TARGET) \
		analysis/run_walle_reveal_process_capture_gate.sh
	bash analysis/run_walle_reveal_process_capture_gate.sh $(TARGET)

# --- Linking Rule ---
$(TARGET): $(OBJECTS) | $(PROFILE_BIN_DIR)
	@echo "MODE: $(MODE)"
	@echo "[LD] $@"
	$(CC) $(CFLAGS) $(LDFLAGS) $^ $(LDLIBS) -o $@

# --- Unified Compilation Rule ---
# This single pattern handles sources in the root, vendor/, and protocols/ directories.
$(OBJ_DIR)/%.c.o: %.c
	@echo "[CC] $<"
	@# Ensure the specific output subdirectory (e.g., build/obj/vendor/inih/src) exists.
	@mkdir -p $(@D)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(DEPFLAGS) -c $< -o $@

$(OBJ_DIR)/vulkan_renderer.c.o: $(SPIRV_TARGETS)

SLANG_COMMON ::= -target spirv -profile spirv_1_6 -std 2026 -O2 \
	-capability vk_mem_model \
	-emit-spirv-directly -matrix-layout-row-major -restrictive-capability-check \
	-fp-mode precise -fvk-use-entrypoint-name -default-image-format-unknown

$(SPIRV_DIR)/maskVertex.spv: $(SHADER_DIR)/reveal_mask.slang Makefile | $(SPIRV_DIR)
	@echo "[SLANG] $@"
	$(SLANGC) $< -entry maskVertex -stage vertex $(SLANG_COMMON) -depfile $@.d -o $@
	$(SPIRV_VAL) --target-env vulkan1.4 $@

$(SPIRV_DIR)/maskFragment.spv: $(SHADER_DIR)/reveal_mask.slang Makefile | $(SPIRV_DIR)
	@echo "[SLANG] $@"
	$(SLANGC) $< -entry maskFragment -stage fragment $(SLANG_COMMON) -depfile $@.d -o $@
	$(SPIRV_VAL) --target-env vulkan1.4 $@

$(SPIRV_DIR)/composeVertex.spv: $(SHADER_DIR)/liquid_glass.slang Makefile | $(SPIRV_DIR)
	@echo "[SLANG] $@"
	$(SLANGC) $< -entry composeVertex -stage vertex $(SLANG_COMMON) -depfile $@.d -o $@
	$(SPIRV_VAL) --target-env vulkan1.4 $@

# The parity composition consumes no push constants (the reveal mask alone
# drives the code-value blend), so no shader-side push ABI remains to assert;
# the C-side pipeline layout keeps its superset push range.
$(SPIRV_DIR)/composeFragment.spv: $(SHADER_DIR)/liquid_glass.slang Makefile | $(SPIRV_DIR)
	@echo "[SLANG] $@"
	$(SLANGC) $< -entry composeFragment -stage fragment $(SLANG_COMMON) -depfile $@.d -o $@
	$(SPIRV_VAL) --target-env vulkan1.4 $@

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

$(LINUX_DMABUF_C) $(LINUX_DMABUF_H) &: $(LINUX_DMABUF_XML) | $(PROTOCOL_DIR)
	@echo "[WL_SCAN] Linux dma-buf ($<)"
	$(WAYLAND_SCANNER) client-header $< $(LINUX_DMABUF_H)
	$(WAYLAND_SCANNER) private-code $< $(LINUX_DMABUF_C)

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
$(BIN_DIR) $(PROFILE_BIN_DIR) $(PROTOCOL_DIR) $(SPIRV_DIR):
	@mkdir -p $@

# Include generated dependency files.
-include $(DEPS)
-include $(SPIRV_DEPS)

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
