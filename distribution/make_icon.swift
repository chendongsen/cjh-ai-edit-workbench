import AppKit
import Foundation
let destination = URL(fileURLWithPath: CommandLine.arguments[1])
try FileManager.default.createDirectory(at: destination, withIntermediateDirectories: true)
for size in [16, 32, 128, 256, 512] {
    for scale in [1, 2] {
        let pixels = size * scale
        let image = NSImage(size: NSSize(width: pixels, height: pixels))
        image.lockFocus()
        let p = CGFloat(pixels)
        NSColor(calibratedRed: 0.07, green: 0.13, blue: 0.22, alpha: 1).setFill()
        NSBezierPath(roundedRect: NSRect(x: p*0.05, y: p*0.05, width: p*0.9, height: p*0.9), xRadius: p*0.2, yRadius: p*0.2).fill()
        NSColor(calibratedRed: 0.2, green: 0.88, blue: 0.74, alpha: 1).setFill()
        let play = NSBezierPath()
        play.move(to: NSPoint(x:p*0.38, y:p*0.35)); play.line(to:NSPoint(x:p*0.38, y:p*0.75))
        play.line(to:NSPoint(x:p*0.71, y:p*0.55)); play.close(); play.fill()
        for i in 0..<3 {
            NSBezierPath(roundedRect:NSRect(x:p*0.24, y:p*(0.18+Double(i)*0.04), width:p*(0.52-Double(i)*0.08), height:p*0.018), xRadius:p*0.009, yRadius:p*0.009).fill()
        }
        image.unlockFocus()
        let bitmap = NSBitmapImageRep(data:image.tiffRepresentation!)!
        let suffix = scale == 2 ? "@2x" : ""
        try bitmap.representation(using:.png, properties:[:])!.write(to:destination.appendingPathComponent("icon_\(size)x\(size)\(suffix).png"))
    }
}
