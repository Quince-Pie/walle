{
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
  xxHash,
  liburing,
}:

# gcc15Stdenv.mkDerivation (not `gcc15` in nativeBuildInputs): the stdenv's cc
# wrapper is what the build actually invokes, and walle needs GCC >= 15 for
# C23 #embed.
gcc15Stdenv.mkDerivation {
  pname = "walle";
  version = "0.0.1";

  src = ./.;

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
    xxHash
    wayland-protocols
    wlr-protocols
    jemalloc
    liburing
  ];

  makeFlags = [ "MODE=release" ];
  installPhase = ''
    install -Dm755 build/bin/walle -t $out/bin
  '';
}
