// raw BGRA frames on stdin (w h args) -> gray8 person masks on stdout, Apple Vision person segmentation (on device)
import Vision
import CoreVideo
import Foundation
import Accelerate
let w = Int(CommandLine.arguments[1])!, h = Int(CommandLine.arguments[2])!
let req = VNGeneratePersonSegmentationRequest()
req.qualityLevel = .accurate
req.outputPixelFormat = kCVPixelFormatType_OneComponent8
let handler = VNSequenceRequestHandler()
let size = w * h * 4
let inp = FileHandle.standardInput, out = FileHandle.standardOutput
while true {
  var buf = Data()
  while buf.count < size { let d = inp.readData(ofLength: size - buf.count); if d.isEmpty { break }; buf.append(d) }
  if buf.count < size { break }
  autoreleasepool {
    var pb: CVPixelBuffer?
    CVPixelBufferCreate(nil, w, h, kCVPixelFormatType_32BGRA, [kCVPixelBufferIOSurfacePropertiesKey: [:]] as CFDictionary, &pb)
    CVPixelBufferLockBaseAddress(pb!, [])
    let bpr = CVPixelBufferGetBytesPerRow(pb!), base = CVPixelBufferGetBaseAddress(pb!)!
    buf.withUnsafeBytes { p in for y in 0..<h { memcpy(base + y * bpr, p.baseAddress! + y * w * 4, w * 4) } }
    CVPixelBufferUnlockBaseAddress(pb!, [])
    try! handler.perform([req], on: pb!)
    let m = req.results!.first!.pixelBuffer
    CVPixelBufferLockBaseAddress(m, .readOnly)
    var src = vImage_Buffer(data: CVPixelBufferGetBaseAddress(m)!, height: vImagePixelCount(CVPixelBufferGetHeight(m)), width: vImagePixelCount(CVPixelBufferGetWidth(m)), rowBytes: CVPixelBufferGetBytesPerRow(m))
    var o = [UInt8](repeating: 0, count: w * h)
    o.withUnsafeMutableBytes { q in
      var dst = vImage_Buffer(data: q.baseAddress!, height: vImagePixelCount(h), width: vImagePixelCount(w), rowBytes: w)
      vImageScale_Planar8(&src, &dst, nil, vImage_Flags(kvImageHighQualityResampling))
    }
    CVPixelBufferUnlockBaseAddress(m, .readOnly)
    out.write(Data(o))
  }
}
