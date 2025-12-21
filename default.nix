{
gcc15,
stdenv,
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
}:

stdenv.mkDerivation rec {
  pname = "walle";
  version = "0.0.1";

  src = ./.;

  nativeBuildInputs = [
    gcc15
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
  ];

  makeFlags = [ "MODE=release" ];
  installPhase = ''
    install -Dm755 build/bin/walle -t $out/bin
  '';

}
