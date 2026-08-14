import Foundation
import Metal

private enum ProbeKind: String {
    case sharedBuffer = "shared-buffer"
    case privateBuffer = "private-buffer"
    case sharedTexture = "shared-texture"
    case privateTexture = "private-texture"
}

@main
private enum StorageModeProbe {
    static func main() throws {
        guard CommandLine.arguments.count == 2,
              let kind = ProbeKind(rawValue: CommandLine.arguments[1])
        else {
            FileHandle.standardError.write(
                Data("usage: macos_agx_storage_mode_probe <shared-buffer|private-buffer|shared-texture|private-texture>\n".utf8))
            Foundation.exit(2)
        }

        guard let device = MTLCreateSystemDefaultDevice() else {
            throw ProbeError.noDevice
        }

        switch kind {
        case .sharedBuffer:
            guard let buffer = device.makeBuffer(
                length: 0x1_3579,
                options: .storageModeShared)
            else {
                throw ProbeError.allocationFailed
            }
            memset(buffer.contents(), 0x5a, buffer.length)
            print("buffer mode=shared length=\(buffer.length)")

        case .privateBuffer:
            guard let buffer = device.makeBuffer(
                length: 0x1_3579,
                options: .storageModePrivate)
            else {
                throw ProbeError.allocationFailed
            }
            print("buffer mode=private length=\(buffer.length)")

        case .sharedTexture, .privateTexture:
            let descriptor = MTLTextureDescriptor.texture2DDescriptor(
                pixelFormat: .rgba8Unorm,
                width: 257,
                height: 263,
                mipmapped: false)
            descriptor.storageMode = kind == .sharedTexture ? .shared : .private
            descriptor.usage = [.renderTarget, .shaderRead]
            guard let texture = device.makeTexture(descriptor: descriptor) else {
                throw ProbeError.allocationFailed
            }
            print("texture mode=\(descriptor.storageMode.rawValue) bytes=\(texture.allocatedSize)")
        }
    }
}

private enum ProbeError: Error {
    case noDevice
    case allocationFailed
}
