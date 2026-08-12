#version 450 core
layout(location = 0) in vec2 in_vert;
layout(location = 1) in vec2 in_texcoord;
out vec2 sdf_uv;
out vec2 v_UV;
out vec2 source_uv;
void main() {
    gl_Position = vec4(in_vert, 0.0, 1.0);
    vec2 uv = vec2(in_texcoord.x, 1.0 - in_texcoord.y);
    sdf_uv = uv;
    v_UV = uv;
    source_uv = uv;
}
