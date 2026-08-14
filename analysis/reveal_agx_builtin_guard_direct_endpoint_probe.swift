import CryptoKit
import Foundation
import Metal
import simd

private enum ProbeError: Error {
    case invalid(String)
    case resource(String)
    case command(String)
}

private struct PlanRecord {
    let recordIndex: UInt32
    let groupIndex: UInt32
    let patternIndex: UInt32
    let distanceFixed: UInt32
    let viewport: UInt32
    let planeCode: UInt32
    let crossSpanPixels: UInt32
    let splitCode: UInt32
    let geometryBits: SIMD4<UInt32>
    let outerBits: SIMD4<UInt32>
    let innerBits: SIMD4<UInt32>
    let sample: SIMD2<UInt32>
}

private struct GroupSpan {
    let groupIndex: UInt32
    let viewport: Int
    let sample: SIMD2<UInt32>
    let range: Range<Int>
}

private let expectedPlanSHA256 =
    "f90e4e3b5f0d46b0fb8250c97aa40eb4ddb32c67050c9823feaecb0a20baaf8d"
private let expectedPreregistrationSHA256 =
    "020e98dea95357ccfeb3e796b0b1e8d68d1d9de74e32c81ef199f4c74090fb12"
private let planMagic = Data("AGXWGT01".utf8)
private let planHeaderBytes = 24
private let planRecordWords = 22
private let planRecordBytes = planRecordWords * MemoryLayout<UInt32>.stride
private let planRecordCount = 83_872
private let planPatternCount = 8
private let pullPhaseCount = 16
private let recordVectorCount = 5 + pullPhaseCount * 6
private let recordWords = recordVectorCount * 4
private let recordBytes = recordWords * MemoryLayout<UInt32>.stride
private let commandChunkRecords = 4_096

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
    constant uint4 *geometryBits [[buffer(0)]],
    constant float4x4 &mvp [[buffer(1)]],
    constant uint &caseIndex [[buffer(2)]],
    constant uint4 *outerBits [[buffer(3)]],
    constant uint4 *innerBits [[buffer(4)]],
    constant uint4 *metadata [[buffer(5)]],
    uint vertexID [[vertex_id]])
{
    const uint corner = vertexID % 6u;
    const bool isRight = corner == 1u || corner == 2u || corner == 3u;
    const bool isBottom = corner == 0u || corner == 1u || corner == 5u;
    const uint plane = metadata[caseIndex].w;
    const bool isOuter = plane == 0u ? !isRight
                       : plane == 1u ? isRight
                       : plane == 2u ? !isBottom
                                     : isBottom;
    const float4 geometry = as_type<float4>(geometryBits[caseIndex]);
    const float x = isRight ? geometry.y : geometry.x;
    const float y = isBottom ? geometry.w : geometry.z;
    const uint pattern = metadata[caseIndex].y;
    const float a = as_type<float>(0x3f800001u);
    const float b = as_type<float>(0x3f7fffffu);
    const float small = as_type<float>(0x35800001u);
    const float large = as_type<float>(0x49800001u);
    float4 outer;
    float4 inner;
    switch (pattern) {
    case 0u:
        outer = float4(a, 0.0f, -a, 0.0f);
        inner = float4(0.0f, a, 0.0f, -a);
        break;
    case 1u:
        outer = float4(b, 0.0f, -b, 0.0f);
        inner = float4(0.0f, b, 0.0f, -b);
        break;
    case 2u:
        outer = float4(small, 0.0f, -small, 0.0f);
        inner = float4(0.0f, small, 0.0f, -small);
        break;
    case 3u:
        outer = float4(large, 0.0f, -large, 0.0f);
        inner = float4(0.0f, large, 0.0f, -large);
        break;
    case 4u:
        outer = float4(a, -a, b, -b);
        inner = float4(-a, a, -b, b);
        break;
    case 5u:
        outer = float4(small, -small, large, -large);
        inner = float4(-small, small, -large, large);
        break;
    case 6u:
        outer = float4(a, b, -a, -b);
        inner = float4(b, a, -b, -a);
        break;
    default:
        outer = float4(0.25f, -0.25f, 1024.0f, -1024.0f);
        inner = float4(0.75f, -0.75f, 1025.0f, -1025.0f);
        break;
    }
    BasisVertexOutput output;
    output.position = mvp * float4(x, y, 0.0f, 1.0f);
    (void)outerBits;
    (void)innerBits;
    output.basis = isOuter ? outer : inner;
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
    results[base + 5u] = as_type<uint4>(
        input.basis.interpolate_at_offset(float2(-205.5f, -21.5f)));
    return 1.0f;
}
"""

private func sha256(_ data: Data) -> String {
    SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
}

private func matrix(viewport: Int) -> simd_float4x4 {
    simd_float4x4(columns: (
        SIMD4<Float>(2 / Float(viewport), 0, 0, 0),
        SIMD4<Float>(0, -2 / Float(viewport), 0, 0),
        SIMD4<Float>(0, 0, 0, 0),
        SIMD4<Float>(-1, 1, 0, 1)
    ))
}

private func makeTarget(device: MTLDevice) -> MTLTexture? {
    let descriptor = MTLTextureDescriptor.texture2DDescriptor(
        pixelFormat: .r32Float,
        width: 512,
        height: 512,
        mipmapped: false
    )
    descriptor.storageMode = .private
    descriptor.usage = [.renderTarget]
    return device.makeTexture(descriptor: descriptor)
}

private func loadPlan(_ url: URL) throws -> (Data, [PlanRecord], [GroupSpan]) {
    let data = try Data(contentsOf: url)
    guard sha256(data) == expectedPlanSHA256 else {
        throw ProbeError.invalid("plan SHA-256")
    }
    let expectedBytes = planHeaderBytes + planRecordCount * planRecordBytes
    guard data.count == expectedBytes,
          data.prefix(planMagic.count) == planMagic
    else {
        throw ProbeError.invalid("plan byte shape")
    }
    let records = try data.withUnsafeBytes { raw -> [PlanRecord] in
        func word(_ offset: Int) -> UInt32 {
            UInt32(littleEndian: raw.loadUnaligned(
                fromByteOffset: offset,
                as: UInt32.self
            ))
        }
        guard word(8) == 1,
              word(12) == UInt32(planRecordCount),
              word(16) == UInt32(planRecordWords),
              word(20) == UInt32(planPatternCount)
        else {
            throw ProbeError.invalid("plan header")
        }
        var result: [PlanRecord] = []
        result.reserveCapacity(planRecordCount)
        for recordIndex in 0 ..< planRecordCount {
            let base = planHeaderBytes + recordIndex * planRecordBytes
            let words = (0 ..< planRecordWords).map {
                word(base + $0 * MemoryLayout<UInt32>.stride)
            }
            guard words[0] == UInt32(recordIndex),
                  words[1] < 5,
                  words[2] < UInt32(planPatternCount),
                  words[4] == 256 || words[4] == 512,
                  words[5] < 4,
                  words[7] < 2,
                  words[20] < words[4],
                  words[21] < words[4]
            else {
                throw ProbeError.invalid("plan record \(recordIndex)")
            }
            result.append(PlanRecord(
                recordIndex: words[0],
                groupIndex: words[1],
                patternIndex: words[2],
                distanceFixed: words[3],
                viewport: words[4],
                planeCode: words[5],
                crossSpanPixels: words[6],
                splitCode: words[7],
                geometryBits: SIMD4(words[8], words[9], words[10], words[11]),
                outerBits: SIMD4(words[12], words[13], words[14], words[15]),
                innerBits: SIMD4(words[16], words[17], words[18], words[19]),
                sample: SIMD2(words[20], words[21])
            ))
        }
        return result
    }

    var spans: [GroupSpan] = []
    var start = 0
    while start < records.count {
        let first = records[start]
        var end = start + 1
        while end < records.count, records[end].groupIndex == first.groupIndex {
            guard records[end].viewport == first.viewport,
                  records[end].sample == first.sample
            else {
                throw ProbeError.invalid("group span \(first.groupIndex)")
            }
            end += 1
        }
        spans.append(GroupSpan(
            groupIndex: first.groupIndex,
            viewport: Int(first.viewport),
            sample: first.sample,
            range: start ..< end
        ))
        start = end
    }
    guard spans.count == 5,
          spans.map(\.groupIndex) == [0, 1, 2, 3, 4]
    else {
        throw ProbeError.invalid("plan group census")
    }
    return (data, records, spans)
}

private func makeBuffer<Element>(
    device: MTLDevice,
    values: [Element]
) -> MTLBuffer? {
    values.withUnsafeBufferPointer { buffer in
        guard let baseAddress = buffer.baseAddress else { return nil }
        return device.makeBuffer(
            bytes: baseAddress,
            length: buffer.count * MemoryLayout<Element>.stride,
            options: .storageModeShared
        )
    }
}

private func render(
    records: [PlanRecord],
    spans: [GroupSpan]
) throws -> (Data, [String: Any]) {
    guard let device = MTLCreateSystemDefaultDevice(),
          let queue = device.makeCommandQueue(),
          let target = makeTarget(device: device)
    else {
        throw ProbeError.resource("Metal device, queue, or target")
    }
    let geometry = records.map(\.geometryBits)
    let outer = records.map(\.outerBits)
    let inner = records.map(\.innerBits)
    let metadata = records.map {
        SIMD4($0.groupIndex, $0.patternIndex, $0.distanceFixed, $0.planeCode)
    }
    guard let geometryBuffer = makeBuffer(device: device, values: geometry),
          let outerBuffer = makeBuffer(device: device, values: outer),
          let innerBuffer = makeBuffer(device: device, values: inner),
          let metadataBuffer = makeBuffer(device: device, values: metadata)
    else {
        throw ProbeError.resource("input buffers")
    }
    let outputBytes = records.count * recordBytes
    guard outputBytes == 135_537_152,
          let outputBuffer = device.makeBuffer(
              length: outputBytes,
              options: .storageModeShared
          )
    else {
        throw ProbeError.resource("output buffer")
    }
    memset(outputBuffer.contents(), 0xff, outputBytes)

    let options = MTLCompileOptions()
    options.fastMathEnabled = true
    let library = try device.makeLibrary(source: metalSource, options: options)
    guard let vertex = library.makeFunction(name: "reveal_basis_vertex"),
          let fragment = library.makeFunction(name: "reveal_basis_fragment")
    else {
        throw ProbeError.resource("tomography Metal functions")
    }
    let pipelineDescriptor = MTLRenderPipelineDescriptor()
    pipelineDescriptor.vertexFunction = vertex
    pipelineDescriptor.fragmentFunction = fragment
    pipelineDescriptor.colorAttachments[0].pixelFormat = .r32Float
    let pipeline = try device.makeRenderPipelineState(descriptor: pipelineDescriptor)

    var completedRecords = 0
    for span in spans.prefix(1) {
        var chunkStart = span.range.lowerBound
        while chunkStart < span.range.upperBound {
            let chunkEnd = min(chunkStart + commandChunkRecords, span.range.upperBound)
            guard let commandBuffer = queue.makeCommandBuffer() else {
                throw ProbeError.resource("command buffer")
            }
            let pass = MTLRenderPassDescriptor()
            pass.colorAttachments[0].texture = target
            pass.colorAttachments[0].loadAction = .dontCare
            pass.colorAttachments[0].storeAction = .dontCare
            guard let encoder = commandBuffer.makeRenderCommandEncoder(
                descriptor: pass
            ) else {
                throw ProbeError.resource("render encoder")
            }
            var transform = matrix(viewport: span.viewport)
            encoder.setRenderPipelineState(pipeline)
            encoder.setViewport(MTLViewport(
                originX: 64,
                originY: 64,
                width: Double(span.viewport),
                height: Double(span.viewport),
                znear: 0,
                zfar: 1
            ))
            encoder.setScissorRect(MTLScissorRect(
                x: 205,
                y: 181,
                width: 1,
                height: 1
            ))
            encoder.setVertexBuffer(geometryBuffer, offset: 0, index: 0)
            withUnsafeBytes(of: &transform) {
                encoder.setVertexBytes($0.baseAddress!, length: $0.count, index: 1)
            }
            encoder.setVertexBuffer(outerBuffer, offset: 0, index: 3)
            encoder.setVertexBuffer(innerBuffer, offset: 0, index: 4)
            encoder.setVertexBuffer(metadataBuffer, offset: 0, index: 5)
            encoder.setFragmentBuffer(outputBuffer, offset: 0, index: 2)

            for recordIndex in chunkStart ..< chunkEnd {
                let record = records[recordIndex]
                var caseIndex = record.recordIndex
                var identity = SIMD4(
                    record.recordIndex,
                    record.groupIndex,
                    record.patternIndex,
                    record.distanceFixed
                )
                var sampleIdentity = SIMD4(
                    record.viewport,
                    record.planeCode,
                    record.splitCode,
                    record.crossSpanPixels
                )
                withUnsafeBytes(of: &caseIndex) {
                    encoder.setVertexBytes(
                        $0.baseAddress!, length: $0.count, index: 2
                    )
                }
                withUnsafeBytes(of: &identity) {
                    encoder.setFragmentBytes(
                        $0.baseAddress!, length: $0.count, index: 0
                    )
                }
                withUnsafeBytes(of: &sampleIdentity) {
                    encoder.setFragmentBytes(
                        $0.baseAddress!, length: $0.count, index: 1
                    )
                }
                encoder.drawPrimitives(type: .triangle, vertexStart: 0, vertexCount: 6)
            }
            encoder.endEncoding()
            commandBuffer.commit()
            commandBuffer.waitUntilCompleted()
            guard commandBuffer.status == .completed else {
                throw ProbeError.command(
                    commandBuffer.error?.localizedDescription
                        ?? "unknown Metal error"
                )
            }
            completedRecords += chunkEnd - chunkStart
            chunkStart = chunkEnd
        }
        print(
            "clip-weight-tomography: group \(span.groupIndex + 1)/\(spans.count), "
                + "records \(completedRecords)/\(records.count)"
        )
    }

    let words = outputBuffer.contents().bindMemory(
        to: UInt32.self,
        capacity: outputBytes / MemoryLayout<UInt32>.stride
    )
    for record in records.prefix(spans[0].range.upperBound) {
        let base = Int(record.recordIndex) * recordWords
        guard words[base] == 205,
              words[base + 1] == 181,
              words[base + 2] <= 1,
              words[base + 3] == record.recordIndex,
              words[base + 4] == record.recordIndex,
              words[base + 5] == record.groupIndex,
              words[base + 6] == record.patternIndex,
              words[base + 7] == record.distanceFixed,
              words[base + 8] == record.viewport,
              words[base + 9] == record.planeCode,
              words[base + 10] == record.splitCode,
              words[base + 11] == record.crossSpanPixels
        else {
            throw ProbeError.command("record \(record.recordIndex) was not written")
        }
    }
    let output = Data(
        bytesNoCopy: outputBuffer.contents(),
        count: outputBytes,
        deallocator: .none
    )
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
                "drawScissor": "translated interior control pixel (205,181)",
                "directEndpointVector": 5,
                "directEndpointOffset": [-205.5, -21.5],
                "commandChunkRecords": commandChunkRecords,
                "fragmentSourceBody": "byte-identical to authenticated basis-phase probe",
            ],
        ]
    )
}

private func run(
    planURL: URL,
    preregistrationURL: URL,
    outputDirectory: URL
) throws {
    guard !FileManager.default.fileExists(atPath: outputDirectory.path) else {
        throw ProbeError.invalid("output directory already exists")
    }
    let preregistration = try Data(contentsOf: preregistrationURL)
    guard sha256(preregistration) == expectedPreregistrationSHA256 else {
        throw ProbeError.invalid("preregistration SHA-256")
    }
    let (planData, records, spans) = try loadPlan(planURL)
    let (output, runtime) = try render(records: records, spans: spans)
    try FileManager.default.createDirectory(
        at: outputDirectory,
        withIntermediateDirectories: false
    )
    let outputName = "reveal-agx-clip-weight-tomography.raw"
    try output.write(
        to: outputDirectory.appendingPathComponent(outputName),
        options: .atomic
    )
    let executableData = try Data(
        contentsOf: URL(fileURLWithPath: CommandLine.arguments[0])
    )
    var manifest: [String: Any] = [
        "schema": "walle-reveal-agx-clip-weight-tomography-capture-v1",
        "authority": [
            "usesPublicClipInputsOnly": true,
            "opensReferencePixels": false,
            "observedCoefficientsReadBeforePlanFreeze": false,
            "mutatesProductionRenderer": false,
            "establishesClipSetupLaw": false,
        ],
        "plan": [
            "file": planURL.lastPathComponent,
            "bytes": planData.count,
            "sha256": sha256(planData),
        ],
        "preregistration": [
            "file": preregistrationURL.lastPathComponent,
            "bytes": preregistration.count,
            "sha256": sha256(preregistration),
        ],
        "capture": [
            "file": outputName,
            "bytes": output.count,
            "sha256": sha256(output),
            "recordCount": records.count,
            "recordBytes": recordBytes,
            "recordVectorCount": recordVectorCount,
            "ordering": "plan recordIndex, 101 uint4 vectors",
            "coefficientExportRegions": [5, 21, 37, 53],
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
private struct RevealAGXClipWeightTomographyProbe {
    static func main() {
        do {
            guard CommandLine.arguments.count == 4 else {
                throw ProbeError.invalid(
                    "plan, preregistration, and output-directory arguments"
                )
            }
            try run(
                planURL: URL(fileURLWithPath: CommandLine.arguments[1]),
                preregistrationURL: URL(fileURLWithPath: CommandLine.arguments[2]),
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
