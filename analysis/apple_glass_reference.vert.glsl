#version 450 core

precision highp float;

layout(location = 0) in vec4 in_position;
layout(location = 1) in vec2 in_sdf_uv;
layout(location = 2) in vec2 in_source_uv;

uniform mat4 MVP;

out highp vec2 sdf_uv;
out highp vec2 source_uv;

void main()
{
    gl_Position = MVP * in_position;
    sdf_uv = in_sdf_uv;
    source_uv = in_source_uv;
}
