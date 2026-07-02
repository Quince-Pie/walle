{
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";

  outputs =
    { nixpkgs, ... }:
    let
      # Wayland + wlroots protocols + io_uring: Linux only.
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
      packages = forAllSystems ({ pkgs }: {
        default = pkgs.callPackage ./default.nix { };
      });
      formatter = forAllSystems ({ pkgs }: pkgs.nixfmt-rfc-style);
      devShells = forAllSystems (
        { pkgs }:
        let
          tools =
            with pkgs;
            [
              bear
              cmake
              poop
              meson
              ninja
              ccache
              cmocka
              python3
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
              tracy
              libglvnd.dev
              inih
              xxHash
              liburing
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
        in
        {
          default = (mkShellWith pkgs.gcc15Stdenv) {
            packages = tools ++ gccToolchain;
            TRACY_SRC = "${pkgs.tracy.src}";
          };

          gccMold = (mkShellWith (pkgs.stdenvAdapters.useMoldLinker pkgs.gcc15Stdenv)) {
            packages = tools ++ gccToolchain;
          };

          gccGold = (mkShellWith (pkgs.stdenvAdapters.useGoldLinker pkgs.gcc15Stdenv)) {
            packages = tools ++ gccToolchain;
          };

          llvm = (mkShellWith pkgs.llvmPackages_21.stdenv) {
            packages = tools ++ llvmToolchain;
          };

          llvmMold = (mkShellWith (pkgs.stdenvAdapters.useMoldLinker pkgs.llvmPackages_21.stdenv)) {
            packages = tools ++ llvmToolchain;
          };

          llvmGold = (mkShellWith (pkgs.stdenvAdapters.useGoldLinker pkgs.llvmPackages_21.stdenv)) {
            packages = tools ++ llvmToolchain;
          };
        }
      );
    };
}
