// cutout in.jpg out.png : lifts the main subject (Apple Vision foreground mask), writes RGBA
import Vision
import CoreImage
import Foundation
import ImageIO
import UniformTypeIdentifiers
let a = CommandLine.arguments
let ci = CIImage(contentsOf: URL(fileURLWithPath: a[1]), options: [.applyOrientationProperty: true])!
let req = VNGenerateForegroundInstanceMaskRequest()
let h = VNImageRequestHandler(ciImage: ci)
try h.perform([req])
guard let r = req.results?.first else { print("no subject"); exit(2) }
let ids = (a.count > 3 && a[3] != "all") ? IndexSet(a[3].split(separator: ",").map { Int($0)! }) : r.allInstances
let pb = try r.generateMaskedImage(ofInstances: ids, from: h, croppedToInstancesExtent: !(a.count > 4 && a[4] == "full"))
let img = CIImage(cvPixelBuffer: pb)
try CIContext().writePNGRepresentation(of: img, to: URL(fileURLWithPath: a[2]), format: .RGBA8, colorSpace: CGColorSpace(name: CGColorSpace.sRGB)!)
print("instances", r.allInstances.count, "size", img.extent.size)
