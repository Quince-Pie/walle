#version 300 es
layout(location = 0) in vec2 in_vert;
layout(location = 1) in vec2 in_texcoord;
out vec2 v_UV;
void main() {
    gl_Position = vec4(in_vert, 0.0, 1.0);
    // Flip Y for OpenGL texture coordinate convention vs Image Top-Left
    v_UV = vec2(in_texcoord.x, 1.0 - in_texcoord.y);
}
