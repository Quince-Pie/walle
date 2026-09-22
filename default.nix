{
  lib,
  gcc15Stdenv,
  python314,
  pkg-config,
  vips,
  jemalloc,
  libdrm,
  systemd,
  wayland,
  wayland-protocols,
  wlr-protocols,
  wayland-scanner,
  vulkan-loader,
  vulkan-headers,
  shader-slang,
  spirv-tools,
  inih,
  xxhash,
  liburing,
}:

# gcc15Stdenv.mkDerivation (not `gcc15` in nativeBuildInputs): the stdenv's cc
# wrapper is what the build actually invokes, and walle needs GCC >= 15 for
# C23 #embed.
gcc15Stdenv.mkDerivation {
  pname = "walle";
  version = "0.0.1";

  # Generated artifacts must never bypass a clean package build.
  src = lib.cleanSourceWith {
    src = ./.;
    filter = path: _: !(builtins.elem (baseNameOf path) [ ".git" "build" "protocols" ".direnv" "result" "__pycache__" ]);
  };
  enableParallelBuilding = true;

  nativeBuildInputs = [
    pkg-config
    python314
    wayland-scanner
    shader-slang
    spirv-tools
  ];

  buildInputs = [
    vips
    systemd.dev
    wayland
    vulkan-loader
    vulkan-headers
    inih
    xxhash
    wayland-protocols
    wlr-protocols
    jemalloc
    libdrm
    liburing
  ];

  makeFlags = [ "MODE=release" ];
  doCheck = true;
  checkPhase = ''
    runHook preCheck
    make MODE=release test
    runHook postCheck
  '';
  installPhase = ''
    install -Dm755 build/bin/walle -t $out/bin
  '';
}
