#version 320 es
precision highp float;

layout(location = 0) in vec4 in_Position;
layout(location = 1) in vec2 in_FirstCoordinates;
layout(location = 2) in vec2 in_SecondCoordinates;

uniform vec2  RevealResolution;
uniform float RevealCompactFamily;

out vec2 v_SDF;

void main() {
    vec2 position = vec2(
        2.0 * in_Position.x / RevealResolution.x - 1.0,
        1.0 - 2.0 * in_Position.y / RevealResolution.y);
    gl_Position = vec4(position, 0.0, 1.0);
    v_SDF = mix(in_SecondCoordinates, in_FirstCoordinates, RevealCompactFamily);
}
