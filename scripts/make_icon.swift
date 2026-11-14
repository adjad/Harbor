import AppKit
import CoreGraphics

// Draws the Harbor icon, "Daybreak": a gold sun rising out of the harbour mouth
// between two mint breakwater arms, a soft glow arc above it and a teal
// reflection on the water below, on the dark emerald squircle.
// Geometry is authored on a 1024 grid (y down, as in concept-7.svg) and mapped
// to Core Graphics' y-up space. At 32 px and below the glow arc and reflection
// are left out so the mark stays a clean half-sun over the split breakwater.
func draw(_ ctx: CGContext, _ s: CGFloat) {
    let cs = CGColorSpaceCreateDeviceRGB()
    let inset = s * 0.045
    let rect = CGRect(x: inset, y: inset, width: s - 2 * inset, height: s - 2 * inset)
    let corner = s * 0.2237
    ctx.saveGState()
    ctx.addPath(CGPath(roundedRect: rect, cornerWidth: corner, cornerHeight: corner, transform: nil))
    ctx.clip()
    let bg = CGGradient(colorsSpace: cs, colors: [
        CGColor(red: 0.05, green: 0.13, blue: 0.10, alpha: 1),
        CGColor(red: 0.01, green: 0.03, blue: 0.02, alpha: 1)] as CFArray, locations: [0, 1])!
    ctx.drawLinearGradient(bg, start: CGPoint(x: s / 2, y: s), end: CGPoint(x: s / 2, y: 0), options: [])
    ctx.restoreGState()

    ctx.saveGState()
    ctx.addPath(CGPath(roundedRect: rect, cornerWidth: corner, cornerHeight: corner, transform: nil))
    ctx.clip()

    // 1024-grid helpers: k scales, y flips.
    let k = s / 1024
    func pt(_ x: CGFloat, _ y: CGFloat) -> CGPoint { CGPoint(x: x * k, y: s - y * k) }
    func box(_ x: CGFloat, _ y: CGFloat, _ w: CGFloat, _ h: CGFloat) -> CGRect {
        CGRect(x: x * k, y: s - (y + h) * k, width: w * k, height: h * k)
    }
    let gold = CGColor(red: 1.0, green: 0.84, blue: 0.45, alpha: 1)
    let mintHi = CGColor(red: 0.80, green: 0.98, blue: 0.90, alpha: 1)
    let sea = CGColor(red: 0.45, green: 0.85, blue: 0.68, alpha: 1)
    let teal = CGColor(red: 0.353, green: 0.816, blue: 0.776, alpha: 0.8)   // HarborInk.teal.dark
    let small = s <= 32

    // Soft teal aurora behind the mark, brightest just above centre.
    let glow = CGGradient(colorsSpace: cs, colors: [
        CGColor(red: 0.353, green: 0.816, blue: 0.776, alpha: 0.16),
        CGColor(red: 0.353, green: 0.816, blue: 0.776, alpha: 0)] as CFArray, locations: [0, 1])!
    ctx.drawRadialGradient(glow, startCenter: pt(512, 430), startRadius: 0,
                           endCenter: pt(512, 430), endRadius: 520 * k, options: [])

    // The sun: upper half of a disc resting on the waterline.
    let sun = CGMutablePath()
    sun.addArc(center: pt(512, 548), radius: 170 * k, startAngle: 0, endAngle: .pi, clockwise: false)
    sun.closeSubpath()
    ctx.addPath(sun)
    ctx.setFillColor(gold)
    ctx.fillPath()

    // Its glow: one arc over the top, from 150° to 30°.
    if !small {
        ctx.addArc(center: pt(512, 548), radius: 240 * k,
                   startAngle: 150 * .pi / 180, endAngle: 30 * .pi / 180, clockwise: true)
        ctx.setLineWidth(34 * k)
        ctx.setLineCap(.round)
        ctx.setStrokeColor(gold.copy(alpha: 0.6)!)
        ctx.strokePath()
    }

    // The breakwater: two arms with the harbour mouth between them, mint fading to sea.
    let arms = CGGradient(colorsSpace: cs, colors: [mintHi, sea] as CFArray, locations: [0, 1])!
    for x in [CGFloat(226), 548] {
        let r = box(x, 584, 250, 68)
        ctx.saveGState()
        ctx.addPath(CGPath(roundedRect: r, cornerWidth: 34 * k, cornerHeight: 34 * k, transform: nil))
        ctx.clip()
        ctx.drawLinearGradient(arms, start: CGPoint(x: r.midX, y: r.maxY), end: CGPoint(x: r.midX, y: r.minY), options: [])
        ctx.restoreGState()
    }

    // The sun's reflection on the water inside.
    if !small {
        ctx.addPath(CGPath(roundedRect: box(352, 696, 320, 56), cornerWidth: 28 * k, cornerHeight: 28 * k, transform: nil))
        ctx.setFillColor(teal)
        ctx.fillPath()
    }

    // A faint hairline on the squircle's edge, as on the other app icons.
    if !small {
        let edge = rect.insetBy(dx: 1.5 * k, dy: 1.5 * k)
        ctx.addPath(CGPath(roundedRect: edge, cornerWidth: corner - 1.5 * k, cornerHeight: corner - 1.5 * k, transform: nil))
        ctx.setLineWidth(3 * k)
        ctx.setStrokeColor(CGColor(red: 1, green: 1, blue: 1, alpha: 0.07))
        ctx.strokePath()
    }

    ctx.restoreGState()
}

func render(_ px: Int) -> Data {
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: px, pixelsHigh: px,
        bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
        colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    let g = NSGraphicsContext(bitmapImageRep: rep)!
    NSGraphicsContext.current = g
    draw(g.cgContext, CGFloat(px))
    g.flushGraphics()
    NSGraphicsContext.restoreGraphicsState()
    return rep.representation(using: .png, properties: [:])!
}

let out = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "Harbor.iconset"
try? FileManager.default.createDirectory(atPath: out, withIntermediateDirectories: true)
let specs: [(String, Int)] = [
    ("icon_16x16", 16), ("icon_16x16@2x", 32), ("icon_32x32", 32), ("icon_32x32@2x", 64),
    ("icon_128x128", 128), ("icon_128x128@2x", 256), ("icon_256x256", 256),
    ("icon_256x256@2x", 512), ("icon_512x512", 512), ("icon_512x512@2x", 1024),
]
for (name, px) in specs {
    try! render(px).write(to: URL(fileURLWithPath: "\(out)/\(name).png"))
}
print("wrote iconset → \(out)")
