{
  lib,
  gcc15Stdenv,
  pkg-config,
  vips,
  jemalloc,
  systemd,
  wayland,
  wayland-protocols,
  wlr-protocols,
  wayland-scanner,
  libglvnd,
  inih,
  xxhash,
}:

# gcc15Stdenv.mkDerivation (not `gcc15` in nativeBuildInputs): the stdenv's cc
# wrapper is what the build actually invokes, and walle needs GCC >= 15 for
# C23 #embed.
gcc15Stdenv.mkDerivation {
  pname = "walle";
  version = "0.0.1";

  src = lib.fileset.toSource {
    root = ./.;
    fileset = lib.fileset.unions [
      ./Makefile
      ./walle.c
      ./shiro.c
      ./shiro.h
      ./tilde.c
      ./tilde.h
      ./uring.c
      ./uring.h
      ./shaders
    ];
  };

  nativeBuildInputs = [
    pkg-config
    wayland-scanner
  ];

  buildInputs = [
    vips
    systemd.dev
    wayland
    libglvnd.dev
    inih
    xxhash
    wayland-protocols
    wlr-protocols
    jemalloc
  ];

  makeFlags = [ "MODE=release" ];
  installPhase = ''
    install -Dm755 build/bin/walle -t $out/bin
  '';
}
