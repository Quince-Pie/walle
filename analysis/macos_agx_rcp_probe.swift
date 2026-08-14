import Foundation
import Metal

// Measure AGX reciprocal/divide words for (num, den) pairs read from stdin
// ("num den" per line).  Emits, per pair and per math mode, the raw f32
// words of: rcp = 1/den, div = num/den, fma(num, rcp, 0), num*rcp.

let source = """
#include <metal_stdlib>
using namespace metal;
kernel void probe(device const float *num [[buffer(0)]],
                  device const float *den [[buffer(1)]],
                  device float4 *out [[buffer(2)]],
                  uint i [[thread_position_in_grid]])
{
    float n = num[i];
    float d = den[i];
    float r = 1.0f / d;
    float q = n / d;
    out[i] = float4(r, q, fma(n, r, 0.0f), n * r);
}
"""

var nums: [Float] = []
var dens: [Float] = []
while let line = readLine() {
    let t = line.split(separator: " ")
    guard t.count == 2, let n = Float(t[0]), let d = Float(t[1]) else { continue }
    nums.append(n)
    dens.append(d)
}
guard let device = MTLCreateSystemDefaultDevice(),
      let queue = device.makeCommandQueue()
else { fatalError("no Metal device") }

for fast in [true, false] {
    let options = MTLCompileOptions()
    options.fastMathEnabled = fast
    let library = try! device.makeLibrary(source: source, options: options)
    let fn = library.makeFunction(name: "probe")!
    let pipeline = try! device.makeComputePipelineState(function: fn)
    let count = nums.count
    let nb = device.makeBuffer(bytes: nums, length: count * 4)!
    let db = device.makeBuffer(bytes: dens, length: count * 4)!
    let ob = device.makeBuffer(length: count * 16)!
    let cb = queue.makeCommandBuffer()!
    let enc = cb.makeComputeCommandEncoder()!
    enc.setComputePipelineState(pipeline)
    enc.setBuffer(nb, offset: 0, index: 0)
    enc.setBuffer(db, offset: 0, index: 1)
    enc.setBuffer(ob, offset: 0, index: 2)
    enc.dispatchThreads(MTLSize(width: count, height: 1, depth: 1),
                        threadsPerThreadgroup: MTLSize(width: 64, height: 1, depth: 1))
    enc.endEncoding()
    cb.commit()
    cb.waitUntilCompleted()
    let words = ob.contents().bindMemory(to: UInt32.self, capacity: count * 4)
    for i in 0..<count {
        let base = i * 4
        print("\(fast ? "fast" : "prec") \(nums[i]) \(dens[i]) " +
              String(format: "%08x %08x %08x %08x",
                     words[base], words[base+1], words[base+2], words[base+3]))
    }
}
