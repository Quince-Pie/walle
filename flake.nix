{
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";

  outputs =
    { nixpkgs, ... }:
    let
      # Wayland + wlroots protocols + Linux io_uring: Linux only.
      forAllSystems =
        f:
        nixpkgs.lib.genAttrs nixpkgs.lib.platforms.linux (
          system:
          f {
            pkgs = import nixpkgs { inherit system; };
          }
        );
    in
    {
      packages = forAllSystems (
        { pkgs }: {
          default = pkgs.callPackage ./default.nix { };
        }
      );
      formatter = forAllSystems ({ pkgs }: pkgs.nixfmt);
      devShells = forAllSystems (
        { pkgs }:
        let
          analysisPython = pkgs.python314.withPackages (
            pythonPackages: with pythonPackages; [
              glcontext
              moderngl
              numpy
              opencv4
              pillow
              pyvips
              scikit-image
              scipy
            ]
          );
          tools = with pkgs; [
            actionlint
            bear
            cmake
            poop
            meson
            ninja
            ccache
            cmocka
            gh
            analysisPython
            ruff
            heaptrack
            jemalloc
            systemd.dev
            llvmPackages_21.clang-tools
            pkg-config
            vips
            wayland
            wayland-protocols
            wlr-protocols
            wayland-scanner
            swift
            tracy
            libglvnd
            libglvnd.dev
            mesa
            inih
            liburing
            xxhash
            valgrind
            gdb
          ];
          llvmToolchain = with pkgs.llvmPackages_21; [
            clang-tools
            clang
            lldb
            llvm
            bintools
          ];
          gccToolchain = with pkgs; [
            gcc15
            # ccls
          ];
          # mkShell ignores a plain `stdenv = ...;` attribute; the stdenv (and
          # therefore the shell's cc/linker wrappers) must be injected via
          # mkShell.override.
          mkShellWith = stdenv: pkgs.mkShell.override { inherit stdenv; };
          mkDevShell =
            stdenv: toolchain:
            (mkShellWith stdenv) {
              packages = tools ++ toolchain;
              shellHook = ''
                export LD_LIBRARY_PATH="${
                  pkgs.lib.makeLibraryPath [
                    pkgs.libglvnd
                    pkgs.mesa
                  ]
                }''${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
              '';
            };
        in
        {
          default = mkDevShell pkgs.gcc15Stdenv gccToolchain;

          gccMold = mkDevShell (pkgs.stdenvAdapters.useMoldLinker pkgs.gcc15Stdenv) gccToolchain;

          gccGold = mkDevShell (pkgs.stdenvAdapters.useGoldLinker pkgs.gcc15Stdenv) gccToolchain;

          llvm = mkDevShell pkgs.llvmPackages_21.stdenv llvmToolchain;

          llvmMold = mkDevShell (pkgs.stdenvAdapters.useMoldLinker pkgs.llvmPackages_21.stdenv) llvmToolchain;

          llvmGold = mkDevShell (pkgs.stdenvAdapters.useGoldLinker pkgs.llvmPackages_21.stdenv) llvmToolchain;
        }
      );
    };
}
