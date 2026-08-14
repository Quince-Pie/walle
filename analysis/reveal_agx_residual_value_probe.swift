import CryptoKit
import Darwin
import Foundation
import Metal
import simd

private enum ProbeError: Error {
    case invalid(String)
    case resource(String)
    case command(String)
}

private struct Plan: Decodable {
    struct Target: Decodable {
        let width: Int
        let height: Int
    }

    struct VertexData: Decodable {
        let bytes: Int
        let sha256: String
        let recordCount: Int
        let verticesPerRecord: Int
        let wordsPerVertex: Int
    }

    struct Census: Decodable {
        let targetCount: Int
        let patternCount: Int
        let drawCount: Int
        let coefficientTripleCount: Int
    }

    struct Draw: Decodable {
        let recordIndex: Int
        let targetIndex: Int
        let targetRecordIndex: Int
        let sampleRecordIndex: Int
        let sampleOrdinal: Int
        let patternIndex: Int
        let x: Int
        let y: Int
        let tileX: Int
        let tileY: Int
    }

    let schema: String
    let target: Target
    let vertexData: VertexData
    let census: Census
    let draws: [Draw]
}

private let recordVectorCount = 9
private let recordWords = recordVectorCount * 4
private let recordBytes = recordWords * MemoryLayout<UInt32>.stride

private func captureTrackedAGXAllocationsIfRequested() throws {
    guard ProcessInfo.processInfo.environment["WALLE_AGX_CAPTURE_NOW"] == "1"
    else {
        return
    }
    guard let process = dlopen(nil, RTLD_NOW),
          let symbol = dlsym(process, "walle_agx_capture_now")
    else {
        throw ProbeError.command("AGX allocation capture hook is unavailable")
    }
    typealias CaptureFunction = @convention(c) () -> Void
    let capture = unsafeBitCast(symbol, to: CaptureFunction.self)
    capture()
}

/* Keep this Metal source identical to the authenticated direct-child probe. */
private let metalSource = """
#include <metal_stdlib>
using namespace metal;

struct BasisVertexOutput {
    float4 position [[position]];
    float4 basis [[user(reveal_basis)]];
    uint caseIndex [[user(reveal_case), flat]];
};

struct BasisFragmentInput {
    float4 position [[position]];
    interpolant<float4, interpolation::no_perspective>
        basis [[user(reveal_basis)]];
    uint caseIndex [[user(reveal_case), flat]];
};

struct DirectVertexWords {
    uint4 positionAndPadding;
    uint4 basis;
};

vertex BasisVertexOutput reveal_basis_vertex(
    constant DirectVertexWords *directVertices [[buffer(0)]],
    constant float4x4 &mvp [[buffer(1)]],
    constant uint &caseIndex [[buffer(2)]],
    uint vertexID [[vertex_id]])
{
    const float2 position =
        as_type<float2>(directVertices[vertexID].positionAndPadding.xy);
    BasisVertexOutput output;
    output.position = mvp * float4(position, 0.0f, 1.0f);
    output.basis = as_type<float4>(directVertices[vertexID].basis);
    output.caseIndex = caseIndex;
    return output;
}

fragment float reveal_basis_fragment(
    BasisFragmentInput input [[stage_in]],
    float3 builtinBarycentric [[barycentric_coord]],
    uint primitiveID [[primitive_id]],
    constant uint4 &identity [[buffer(0)]],
    constant uint4 &sampleIdentity [[buffer(1)]],
    device uint4 *results [[buffer(2)]])
{
    const uint record = identity.x;
    const uint base = record * 9u;
    const float4 center = input.basis.interpolate_at_center();

    results[base + 0u] = uint4(
        uint(input.position.x), uint(input.position.y), primitiveID, input.caseIndex);
    results[base + 1u] = identity;
    results[base + 2u] = sampleIdentity;
    results[base + 3u] = uint4(
        as_type<uint>(builtinBarycentric.x),
        as_type<uint>(builtinBarycentric.y),
        as_type<uint>(builtinBarycentric.z),
        as_type<uint>(builtinBarycentric.x + builtinBarycentric.y
                      + builtinBarycentric.z));
    results[base + 4u] = as_type<uint4>(center);
    results[base + 5u] = as_type<uint4>(
        input.basis.interpolate_at_offset(float2(1.5f, 0.5f)));
    results[base + 6u] = as_type<uint4>(
        input.basis.interpolate_at_offset(float2(-0.5f, 0.5f)));
    results[base + 7u] = as_type<uint4>(
        input.basis.interpolate_at_offset(float2(0.5f, 1.5f)));
    results[base + 8u] = as_type<uint4>(
        input.basis.interpolate_at_offset(float2(0.5f, -0.5f)));
    return 1.0f;
}
"""

private func sha256(_ data: Data) -> String {
    SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
}

private func matrix(width: Int, height: Int) -> simd_float4x4 {
    simd_float4x4(columns: (
        SIMD4<Float>(2 / Float(width), 0, 0, 0),
        SIMD4<Float>(0, -2 / Float(height), 0, 0),
        SIMD4<Float>(0, 0, 0, 0),
        SIMD4<Float>(-1, 1, 0, 1)
    ))
}

private func makeTarget(
    device: MTLDevice,
    width: Int,
    height: Int
) -> MTLTexture? {
    let descriptor = MTLTextureDescriptor.texture2DDescriptor(
        pixelFormat: .r32Float,
        width: width,
        height: height,
        mipmapped: false
    )
    descriptor.storageMode = .private
    descriptor.usage = [.renderTarget]
    return device.makeTexture(descriptor: descriptor)
}

private func loadPlan(_ url: URL) throws -> (Plan, Data) {
    let data = try Data(contentsOf: url)
    let plan = try JSONDecoder().decode(Plan.self, from: data)
    guard plan.schema == "walle-reveal-agx-setup-accumulator-plan-v1",
          plan.target.width == 2_048,
          plan.target.height == 2_048,
          plan.vertexData.recordCount == plan.census.drawCount,
          plan.vertexData.verticesPerRecord == 3,
          plan.vertexData.wordsPerVertex == 8,
          plan.vertexData.bytes == plan.census.drawCount * 3 * 8 * 4,
          plan.census.coefficientTripleCount == plan.census.drawCount * 4,
          plan.census.targetCount == 8,
          plan.census.patternCount > 0,
          plan.draws.count == plan.census.drawCount
    else {
        throw ProbeError.invalid("plan shape")
    }
    for (recordIndex, draw) in plan.draws.enumerated() {
        guard draw.recordIndex == recordIndex,
              0 <= draw.targetIndex, draw.targetIndex < plan.census.targetCount,
              0 <= draw.patternIndex, draw.patternIndex < plan.census.patternCount,
              0 <= draw.sampleOrdinal, draw.sampleOrdinal < 3,
              0 <= draw.x, draw.x < plan.target.width,
              0 <= draw.y, draw.y < plan.target.height,
              draw.tileX == draw.x / 32,
              draw.tileY == draw.y / 32,
              draw.targetRecordIndex >= 0,
              draw.sampleRecordIndex >= 0
        else {
            throw ProbeError.invalid("draw \(recordIndex)")
        }
    }
    return (plan, data)
}

private func render(
    plan: Plan,
    vertexData: Data
) throws -> (Data, [String: Any]) {
    guard let device = MTLCreateSystemDefaultDevice(),
          let queue = device.makeCommandQueue(),
          let target = makeTarget(
              device: device,
              width: plan.target.width,
              height: plan.target.height
          ),
          let vertexBuffer = vertexData.withUnsafeBytes({ bytes in
              device.makeBuffer(
                  bytes: bytes.baseAddress!,
                  length: bytes.count,
                  options: .storageModeShared
              )
          })
    else {
        throw ProbeError.resource("Metal device, queue, target, or vertex buffer")
    }

    let outputBytes = plan.census.drawCount * recordBytes
    guard let outputBuffer = device.makeBuffer(
        length: outputBytes,
        options: .storageModeShared
    ) else {
        throw ProbeError.resource("output buffer")
    }
    memset(outputBuffer.contents(), 0xff, outputBytes)

    let options = MTLCompileOptions()
    options.fastMathEnabled = true
    let library = try device.makeLibrary(source: metalSource, options: options)
    guard let vertex = library.makeFunction(name: "reveal_basis_vertex"),
          let fragment = library.makeFunction(name: "reveal_basis_fragment")
    else {
        throw ProbeError.resource("basis Metal functions")
    }
    let descriptor = MTLRenderPipelineDescriptor()
    descriptor.vertexFunction = vertex
    descriptor.fragmentFunction = fragment
    descriptor.colorAttachments[0].pixelFormat = .r32Float
    let pipeline = try device.makeRenderPipelineState(descriptor: descriptor)

    guard let commandBuffer = queue.makeCommandBuffer() else {
        throw ProbeError.resource("command buffer")
    }
    let pass = MTLRenderPassDescriptor()
    pass.colorAttachments[0].texture = target
    pass.colorAttachments[0].loadAction = .dontCare
    pass.colorAttachments[0].storeAction = .dontCare
    guard let encoder = commandBuffer.makeRenderCommandEncoder(descriptor: pass) else {
        throw ProbeError.resource("render encoder")
    }

    var transform = matrix(width: plan.target.width, height: plan.target.height)
    encoder.setRenderPipelineState(pipeline)
    encoder.setViewport(MTLViewport(
        originX: 0,
        originY: 0,
        width: Double(plan.target.width),
        height: Double(plan.target.height),
        znear: 0,
        zfar: 1
    ))
    encoder.setVertexBuffer(vertexBuffer, offset: 0, index: 0)
    withUnsafeBytes(of: &transform) {
        encoder.setVertexBytes($0.baseAddress!, length: $0.count, index: 1)
    }
    encoder.setFragmentBuffer(outputBuffer, offset: 0, index: 2)

    for draw in plan.draws {
        encoder.setScissorRect(MTLScissorRect(x: draw.x, y: draw.y, width: 1, height: 1))
        var caseIndex = UInt32(draw.targetIndex)
        var identity = SIMD4<UInt32>(
            UInt32(draw.recordIndex),
            UInt32(draw.targetRecordIndex),
            UInt32(draw.sampleRecordIndex),
            UInt32(draw.patternIndex)
        )
        var sampleIdentity = SIMD4<UInt32>(
            UInt32(draw.tileX),
            UInt32(draw.tileY),
            UInt32(draw.sampleOrdinal),
            UInt32(draw.targetIndex)
        )
        withUnsafeBytes(of: &caseIndex) {
            encoder.setVertexBytes($0.baseAddress!, length: $0.count, index: 2)
        }
        withUnsafeBytes(of: &identity) {
            encoder.setFragmentBytes($0.baseAddress!, length: $0.count, index: 0)
        }
        withUnsafeBytes(of: &sampleIdentity) {
            encoder.setFragmentBytes($0.baseAddress!, length: $0.count, index: 1)
        }
        encoder.drawPrimitives(
            type: .triangle,
            vertexStart: draw.recordIndex * 3,
            vertexCount: 3
        )
    }
    encoder.endEncoding()
    commandBuffer.commit()
    commandBuffer.waitUntilCompleted()
    guard commandBuffer.status == .completed else {
        throw ProbeError.command(
            commandBuffer.error?.localizedDescription ?? "unknown Metal error"
        )
    }
    try captureTrackedAGXAllocationsIfRequested()

    let words = outputBuffer.contents().bindMemory(
        to: UInt32.self,
        capacity: outputBytes / MemoryLayout<UInt32>.stride
    )
    var uncovered: [Int] = []
    for draw in plan.draws {
        let base = draw.recordIndex * recordWords
        if words[base] != UInt32(draw.x)
            || words[base + 1] != UInt32(draw.y)
            || words[base + 4] != UInt32(draw.recordIndex) {
            /* The candidate triangle does not rasterize this pixel: that is
             * a coverage measurement, not a failure. */
            uncovered.append(draw.recordIndex)
        }
    }
    let output = Data(bytes: outputBuffer.contents(), count: outputBytes)
    return (
        output,
        [
            "device": [
                "name": device.name,
                "registryID": String(device.registryID),
                "recommendedMaxWorkingSetSize": String(
                    device.recommendedMaxWorkingSetSize
                ),
            ],
            "uncoveredRecords": uncovered.map(String.init).joined(separator: ","),
            "compile": [
                "fastMathEnabled": true,
                "geometryPath": "direct public canonical post-guard child",
                "centerPath": "interpolant.interpolate_at_center / AGX ITER",
                "offsetPath": "interpolant.interpolate_at_offset / AGX LDCF+FFMA",
                "drawScissor": "one residual pixel per record; +5..+8 are the four +-1-pixel partner values",
            ],
        ]
    )
}

private func run(
    planURL: URL,
    vertexURL: URL,
    outputDirectory: URL
) throws {
    guard !FileManager.default.fileExists(atPath: outputDirectory.path) else {
        throw ProbeError.invalid("output directory already exists")
    }
    let (plan, planData) = try loadPlan(planURL)
    let vertexData = try Data(contentsOf: vertexURL)
    guard vertexData.count == plan.vertexData.bytes,
          sha256(vertexData) == plan.vertexData.sha256
    else {
        throw ProbeError.invalid("vertex data identity")
    }
    let (output, runtime) = try render(plan: plan, vertexData: vertexData)

    try FileManager.default.createDirectory(
        at: outputDirectory,
        withIntermediateDirectories: false
    )
    let outputName = "reveal-agx-setup-accumulator.raw"
    try output.write(
        to: outputDirectory.appendingPathComponent(outputName),
        options: .atomic
    )
    let executableData = try Data(
        contentsOf: URL(fileURLWithPath: CommandLine.arguments[0])
    )
    var manifest: [String: Any] = [
        "schema": "walle-reveal-agx-setup-accumulator-capture-v1",
        "authority": [
            "usesPublicRevealInputsOnly": true,
            "opensReferencePixels": false,
            "mutatesProductionRenderer": false,
            "establishesAGXAccumulatorLaw": false,
        ],
        "plan": [
            "file": planURL.lastPathComponent,
            "bytes": planData.count,
            "sha256": sha256(planData),
        ],
        "vertexData": [
            "file": vertexURL.lastPathComponent,
            "bytes": vertexData.count,
            "sha256": sha256(vertexData),
        ],
        "capture": [
            "file": outputName,
            "bytes": output.count,
            "sha256": sha256(output),
            "recordCount": plan.census.drawCount,
            "recordBytes": recordBytes,
            "recordVectorCount": recordVectorCount,
            "ordering": "plan recordIndex, 9 uint4 vectors",
        ],
        "executable": [
            "bytes": executableData.count,
            "sha256": sha256(executableData),
        ],
    ]
    for (key, value) in runtime {
        manifest[key] = value
    }
    let manifestData = try JSONSerialization.data(
        withJSONObject: manifest,
        options: [.prettyPrinted, .sortedKeys]
    )
    var terminated = manifestData
    terminated.append(0x0a)
    try terminated.write(
        to: outputDirectory.appendingPathComponent("manifest.json"),
        options: .atomic
    )
}

@main
private struct RevealAGXSetupAccumulatorProbe {
    static func main() {
        do {
            guard CommandLine.arguments.count == 4 else {
                throw ProbeError.invalid("plan, vertex data, and output arguments")
            }
            try run(
                planURL: URL(fileURLWithPath: CommandLine.arguments[1]),
                vertexURL: URL(fileURLWithPath: CommandLine.arguments[2]),
                outputDirectory: URL(
                    fileURLWithPath: CommandLine.arguments[3],
                    isDirectory: true
                )
            )
        } catch {
            FileHandle.standardError.write(Data("error: \(error)\n".utf8))
            Foundation.exit(1)
        }
    }
}
