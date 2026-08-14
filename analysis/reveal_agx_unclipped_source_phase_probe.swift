import CryptoKit
import Foundation
import Metal
import simd

private enum ProbeError: Error {
    case invalid(String)
    case resource(String)
    case command(String)
}

private struct Catalog: Decodable {
    struct Target: Decodable {
        let width: Int
        let height: Int
        let stateCount: Int
    }

    struct Census: Decodable {
        let sourceCaseCount: Int
        let postGuardChildCount: Int
        let sampledChildCount: Int
        let sampleRecordCount: Int
    }

    struct Sample: Decodable {
        let recordIndex: Int
        let sampleOrdinal: Int
        let pixel: [Int]
        let tile: [Int]
    }

    struct Child: Decodable {
        let childOrdinalInState: Int
        let childOrdinalWithinSource: Int
        let generatedVertexBits: [[UInt32]]
        let samples: [Sample]
    }

    struct SourceCase: Decodable {
        let state: Int
        let family: String
        let sourcePrimitive: Int
        let sourceVertexIndices: [Int]
        let sourceVertexBits: [[UInt32]]
        let stateScissor: [Int]
        let children: [Child]
    }

    let schema: String
    let target: Target
    let census: Census
    let cases: [SourceCase]
}

private struct DrawRecord {
    let caseIndex: Int
    let state: Int
    let sourcePrimitive: Int
    let childOrdinal: Int
    let sampleOrdinal: Int
    let recordIndex: Int
    let x: Int
    let y: Int
}

private let expectedCatalogSHA256 =
    "bc8b96dc4d3dc7c2fb6383dda49baa839eb207b60128739604ad8ddcd9402bd6"
private let probeRasterExtent = 8_192
private let pullPhaseCount = 16
private let recordVectorCount = 5 + pullPhaseCount * 6
private let recordWords = recordVectorCount * 4
private let recordBytes = recordWords * MemoryLayout<UInt32>.stride

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

vertex BasisVertexOutput reveal_basis_vertex(
    constant uint2 *positionBits [[buffer(0)]],
    constant float4x4 &mvp [[buffer(1)]],
    constant uint &caseIndex [[buffer(2)]],
    uint vertexID [[vertex_id]])
{
    const uint local = vertexID % 3u;
    const float2 position = as_type<float2>(positionBits[vertexID]);
    const float3 oneHot = local == 0u ? float3(1.0f, 0.0f, 0.0f)
                        : local == 1u ? float3(0.0f, 1.0f, 0.0f)
                                      : float3(0.0f, 0.0f, 1.0f);
    BasisVertexOutput output;
    output.position = mvp * float4(position, 0.0f, 1.0f);
    output.basis = float4(oneHot, dot(oneHot, float3(1.0f, 2.0f, 4.0f)));
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
    const uint base = record * 101u;
    const float4 center = input.basis.interpolate_at_center();
    const float2 tileCenter =
        float2(uint2(input.position.xy) & uint2(31u)) + 0.5f;
    const float2 tileOrigin = -tileCenter;

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
    for (uint phase = 0u; phase < 16u; ++phase) {
        const float position = float(phase) / 16.0f;
        results[base + 5u + phase] = as_type<uint4>(
            input.basis.interpolate_at_offset(
                tileOrigin + float2(position, 0.0f)));
        results[base + 21u + phase] = as_type<uint4>(
            input.basis.interpolate_at_offset(
                tileOrigin + float2(0.0f, position)));
        results[base + 37u + phase] = as_type<uint4>(
            input.basis.interpolate_at_offset(
                tileOrigin + float2(31.0f + position, 0.0f)));
        results[base + 53u + phase] = as_type<uint4>(
            input.basis.interpolate_at_offset(
                tileOrigin + float2(0.0f, 31.0f + position)));
        results[base + 69u + phase] = as_type<uint4>(
            input.basis.interpolate_at_offset(
                tileOrigin + float2(position, 0.5f)));
        results[base + 85u + phase] = as_type<uint4>(
            input.basis.interpolate_at_offset(
                tileOrigin + float2(0.5f, position)));
    }
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

private func loadCatalog(_ url: URL) throws -> (Catalog, Data) {
    let data = try Data(contentsOf: url)
    guard sha256(data) == expectedCatalogSHA256 else {
        throw ProbeError.invalid("catalog SHA-256")
    }
    let catalog = try JSONDecoder().decode(Catalog.self, from: data)
    guard catalog.schema == "walle-reveal-agx-basis-catalog-v1",
          catalog.target.width == 2_048,
          catalog.target.height == 2_048,
          catalog.target.stateCount == 65,
          catalog.census.sourceCaseCount == 183,
          catalog.census.postGuardChildCount == 271,
          catalog.census.sampledChildCount == 230,
          catalog.census.sampleRecordCount == 690,
          catalog.cases.count == catalog.census.sourceCaseCount
    else {
        throw ProbeError.invalid("catalog shape")
    }
    return (catalog, data)
}

private func flattenCatalog(
    _ catalog: Catalog
) throws -> ([SIMD2<UInt32>], [DrawRecord]) {
    var positionBits: [SIMD2<UInt32>] = []
    var draws: [DrawRecord] = []
    var records = Set<Int>()
    for (caseIndex, sourceCase) in catalog.cases.enumerated() {
        guard sourceCase.sourceVertexBits.count == 3,
              sourceCase.sourceVertexBits.allSatisfy({ $0.count == 8 }),
              sourceCase.sourceVertexIndices.count == 3,
              sourceCase.stateScissor.count == 4
        else {
            throw ProbeError.invalid("source case \(caseIndex)")
        }
        for vertex in sourceCase.sourceVertexBits {
            positionBits.append(SIMD2<UInt32>(vertex[0], vertex[1]))
        }
        for child in sourceCase.children {
            guard child.generatedVertexBits.count == 3,
                  child.generatedVertexBits.allSatisfy({ $0.count == 6 })
            else {
                throw ProbeError.invalid("child in source case \(caseIndex)")
            }
            for sample in child.samples {
                guard sample.pixel.count == 2,
                      sample.tile.count == 2,
                      sample.tile[0] == sample.pixel[0] / 32,
                      sample.tile[1] == sample.pixel[1] / 32,
                      0 <= sample.pixel[0], sample.pixel[0] < catalog.target.width,
                      0 <= sample.pixel[1], sample.pixel[1] < catalog.target.height,
                      records.insert(sample.recordIndex).inserted
                else {
                    throw ProbeError.invalid("sample in source case \(caseIndex)")
                }
                draws.append(DrawRecord(
                    caseIndex: caseIndex,
                    state: sourceCase.state,
                    sourcePrimitive: sourceCase.sourcePrimitive,
                    childOrdinal: child.childOrdinalInState,
                    sampleOrdinal: sample.sampleOrdinal,
                    recordIndex: sample.recordIndex,
                    x: sample.pixel[0],
                    y: sample.pixel[1]
                ))
            }
        }
    }
    guard positionBits.count == catalog.cases.count * 3,
          draws.count == catalog.census.sampleRecordCount,
          records == Set(0 ..< catalog.census.sampleRecordCount)
    else {
        throw ProbeError.invalid("flattened catalog census")
    }
    draws.sort { $0.recordIndex < $1.recordIndex }
    return (positionBits, draws)
}

private func render(
    catalog: Catalog,
    positions: [SIMD2<UInt32>],
    draws: [DrawRecord]
) throws -> (Data, [String: Any]) {
    guard let device = MTLCreateSystemDefaultDevice(),
          let queue = device.makeCommandQueue(),
          let target = makeTarget(
              device: device,
              width: probeRasterExtent,
              height: probeRasterExtent
          ),
          let positionBuffer = positions.withUnsafeBufferPointer({ buffer in
              device.makeBuffer(
                  bytes: buffer.baseAddress!,
                  length: buffer.count * MemoryLayout<SIMD2<UInt32>>.stride,
                  options: .storageModeShared
              )
          })
    else {
        throw ProbeError.resource("Metal device, queue, target, or position buffer")
    }
    let outputBytes = catalog.census.sampleRecordCount * recordBytes
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
    let pipelineDescriptor = MTLRenderPipelineDescriptor()
    pipelineDescriptor.vertexFunction = vertex
    pipelineDescriptor.fragmentFunction = fragment
    pipelineDescriptor.colorAttachments[0].pixelFormat = .r32Float
    let pipeline = try device.makeRenderPipelineState(descriptor: pipelineDescriptor)

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
    var transform = matrix(width: probeRasterExtent, height: probeRasterExtent)
    encoder.setRenderPipelineState(pipeline)
    encoder.setViewport(MTLViewport(
        originX: 0,
        originY: 0,
        width: Double(probeRasterExtent),
        height: Double(probeRasterExtent),
        znear: 0,
        zfar: 1
    ))
    encoder.setVertexBuffer(positionBuffer, offset: 0, index: 0)
    withUnsafeBytes(of: &transform) {
        encoder.setVertexBytes($0.baseAddress!, length: $0.count, index: 1)
    }
    encoder.setFragmentBuffer(outputBuffer, offset: 0, index: 2)

    for draw in draws {
        encoder.setScissorRect(MTLScissorRect(x: draw.x, y: draw.y, width: 1, height: 1))
        var caseIndex = UInt32(draw.caseIndex)
        var identity = SIMD4<UInt32>(
            UInt32(draw.recordIndex),
            UInt32(draw.state),
            UInt32(draw.sourcePrimitive),
            UInt32(draw.childOrdinal)
        )
        var sampleIdentity = SIMD4<UInt32>(
            UInt32(draw.sampleOrdinal), UInt32(draw.x / 32), UInt32(draw.y / 32), 0
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
            vertexStart: draw.caseIndex * 3,
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

    let words = outputBuffer.contents().bindMemory(
        to: UInt32.self,
        capacity: outputBytes / MemoryLayout<UInt32>.stride
    )
    for draw in draws {
        let base = draw.recordIndex * recordWords
        guard words[base] == UInt32(draw.x),
              words[base + 1] == UInt32(draw.y),
              words[base + 2] == 0,
              words[base + 3] == UInt32(draw.caseIndex),
              words[base + 4] == UInt32(draw.recordIndex),
              words[base + 5] == UInt32(draw.state),
              words[base + 6] == UInt32(draw.sourcePrimitive),
              words[base + 7] == UInt32(draw.childOrdinal)
        else {
            throw ProbeError.command(
                "record \(draw.recordIndex) at state \(draw.state) was not written"
            )
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
            "compile": [
                "fastMathEnabled": true,
                "centerPath": "interpolant.interpolate_at_center / AGX ITER",
                "offsetPath": "interpolant.interpolate_at_offset / AGX LDCF+FFMA",
                "drawScissor": "one strictly interior pixel per record",
                "geometryPath": "original source triangle with clipping-free 8192 viewport",
                "probeRasterExtent": probeRasterExtent,
                "pullLattice": [
                    "X0=tile-origin+(phase/16,0)",
                    "Y0=tile-origin+(0,phase/16)",
                    "X31=tile-origin+(31+phase/16,0)",
                    "Y31=tile-origin+(0,31+phase/16)",
                    "XHalf=tile-origin+(phase/16,0.5)",
                    "YHalf=tile-origin+(0.5,phase/16)",
                ],
            ],
        ]
    )
}

private func run(catalogURL: URL, outputDirectory: URL) throws {
    guard !FileManager.default.fileExists(atPath: outputDirectory.path) else {
        throw ProbeError.invalid("output directory already exists")
    }
    let (catalog, catalogData) = try loadCatalog(catalogURL)
    let (positions, draws) = try flattenCatalog(catalog)
    let (output, runtime) = try render(
        catalog: catalog,
        positions: positions,
        draws: draws
    )
    try FileManager.default.createDirectory(
        at: outputDirectory,
        withIntermediateDirectories: false
    )
    let outputName = "reveal-agx-basis-phase.raw"
    try output.write(
        to: outputDirectory.appendingPathComponent(outputName),
        options: .atomic
    )
    let executableData = try Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[0]))
    var manifest: [String: Any] = [
        "schema": "walle-reveal-agx-basis-phase-capture-v2",
        "authority": [
            "usesPublicRevealInputsOnly": true,
            "opensReferencePixels": false,
            "mutatesProductionRenderer": false,
            "establishesClipSetupLaw": false,
        ],
        "catalog": [
            "file": catalogURL.lastPathComponent,
            "bytes": catalogData.count,
            "sha256": sha256(catalogData),
        ],
        "capture": [
            "file": outputName,
            "bytes": output.count,
            "sha256": sha256(output),
            "recordCount": draws.count,
            "recordBytes": recordBytes,
            "recordVectorCount": recordVectorCount,
            "ordering": "catalog recordIndex, 101 uint4 vectors",
            "vectors": [
                "pixel/primitive/case",
                "record/state/source-primitive/child",
                "sample/tile-x/tile-y/reserved",
                "builtin barycentric xyz/sum",
                "ITER center basis",
                "16 X-axis LDCF pulls at tile-origin+(phase/16, 0)",
                "16 Y-axis LDCF pulls at tile-origin+(0, phase/16)",
                "16 X-axis LDCF pulls at tile-origin+(31+phase/16, 0)",
                "16 Y-axis LDCF pulls at tile-origin+(0, 31+phase/16)",
                "16 X-axis validation pulls at tile-origin+(phase/16, 0.5)",
                "16 Y-axis validation pulls at tile-origin+(0.5, phase/16)",
            ],
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
private struct RevealAGXBasisPhaseProbe {
    static func main() {
        do {
            guard CommandLine.arguments.count == 3 else {
                throw ProbeError.invalid("catalog and output-directory arguments")
            }
            try run(
                catalogURL: URL(fileURLWithPath: CommandLine.arguments[1]),
                outputDirectory: URL(
                    fileURLWithPath: CommandLine.arguments[2],
                    isDirectory: true
                )
            )
        } catch {
            FileHandle.standardError.write(Data("error: \(error)\n".utf8))
            Foundation.exit(1)
        }
    }
}

