{
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";

  outputs =
    { nixpkgs, ... }:
    let
      forAllSystems =
        f:
        nixpkgs.lib.genAttrs nixpkgs.lib.platforms.unix (
          system:
          f {
            pkgs = import nixpkgs { inherit system; };
          }
        );
    in
    {
      packages = forAllSystems ({ pkgs }: {
        default = pkgs.callPackage ./default.nix {};
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
            ]
            ++ lib.optionals stdenv.isLinux [
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
        in
        {
          default = pkgs.mkShell {
            stdenv = pkgs.gcc15Stdenv;
            packages = tools ++ gccToolchain;
            TRACY_SRC = "${pkgs.tracy.src}";
          };

          gccMold = pkgs.mkShell {
            stdenv = pkgs.stdenvAdapters.useMoldLinker pkgs.gcc15Stdenv;
            packages = tools ++ gccToolchain;
          };

          gccGold = pkgs.mkShell {
            stdenv = pkgs.stdenvAdapters.useGoldLinker pkgs.gcc15Stdenv;
            packages = tools ++ gccToolchain;
          };

          llvm = pkgs.mkShell {
            stdenv = pkgs.llvmPackages_21.stdenv;
            packages = tools ++ llvmToolchain;
          };

          llvmMold = pkgs.mkShell {
            stdenv = pkgs.stdenvAdapters.useMoldLinker pkgs.llvmPackages_21.stdenv;
            packages = tools ++ llvmToolchain;
          };

          llvmGold = pkgs.mkShell {
            stdenv = pkgs.stdenvAdapters.useGoldLinker pkgs.llvmPackages_21.stdenv;
            packages = tools ++ llvmToolchain;
          };
        }
      );
    };
}
