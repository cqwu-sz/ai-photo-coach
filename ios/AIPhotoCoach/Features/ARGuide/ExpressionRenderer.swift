import UIKit

/// Swift port of web/js/expression_system.js. Rasterises one of the 5
/// supported anime-style face expressions onto a UIImage so the
/// AvatarBuilderSCN's face plane has a textured face matching the AI's
/// recommended emotion.
public enum ExpressionRenderer {
    public enum Expression: String {
        case neutral, joy, smirk, surprised, pensive
    }

    public static func render(_ expr: Expression, style: AvatarStyle, size: CGFloat = 256) -> UIImage {
        let renderer = UIGraphicsImageRenderer(size: CGSize(width: size, height: size))
        return renderer.image { ctx in
            let cg = ctx.cgContext
            cg.clear(CGRect(x: 0, y: 0, width: size, height: size))

            let cx = size / 2
            let eyeY = size * 0.42
            let mouthY = size * 0.72
            let eyeDX = size * 0.18

            let iris = pickIris(style: style)
            let brow = darken(style.hairColor, by: 0.6)

            drawEyes(cg, cx: cx, y: eyeY, dx: eyeDX, expr: expr, iris: iris)
            drawBrows(cg, cx: cx, y: eyeY - size * 0.11, dx: eyeDX, expr: expr, color: brow)
            drawMouth(cg, cx: cx, y: mouthY, expr: expr)
            drawBlush(cg, cx: cx, y: eyeY + size * 0.06, dx: eyeDX)
        }
    }

    // ---- drawing primitives ---------------------------------------------------

    private static func drawEyes(_ cg: CGContext, cx: CGFloat, y: CGFloat, dx: CGFloat,
                                 expr: Expression, iris: UIColor) {
        let lash = UIColor(red: 0.11, green: 0.11, blue: 0.14, alpha: 1)
        let pupil = UIColor(red: 0.05, green: 0.05, blue: 0.07, alpha: 1)
        let sclera = UIColor.white

        for sgn in [CGFloat(-1), CGFloat(1)] {
            let x = cx + sgn * dx
            switch expr {
            case .joy:
                cg.setStrokeColor(lash.cgColor)
                cg.setLineWidth(4.5)
                cg.setLineCap(.round)
                cg.beginPath()
                cg.addArc(center: CGPoint(x: x, y: y + 6), radius: 16,
                          startAngle: .pi * 1.05, endAngle: .pi * 1.95, clockwise: true)
                cg.strokePath()
            case .pensive:
                drawEyeBall(cg, x: x, y: y + 4, w: 16, h: 9,
                            sclera: sclera, lash: lash, iris: iris, pupil: pupil)
                cg.setFillColor(lash.cgColor)
                cg.fillEllipse(in: CGRect(x: x - 18, y: y - 9, width: 36, height: 14))
            case .surprised:
                drawEyeBall(cg, x: x, y: y, w: 18, h: 18,
                            sclera: sclera, lash: lash, iris: iris, pupil: pupil, irisFill: 0.8)
            case .smirk:
                let h: CGFloat = sgn == -1 ? 12 : 10
                drawEyeBall(cg, x: x, y: y, w: 16, h: h,
                            sclera: sclera, lash: lash, iris: iris, pupil: pupil)
            case .neutral:
                drawEyeBall(cg, x: x, y: y, w: 16, h: 14,
                            sclera: sclera, lash: lash, iris: iris, pupil: pupil)
            }
        }
    }

    private static func drawEyeBall(_ cg: CGContext, x: CGFloat, y: CGFloat, w: CGFloat, h: CGFloat,
                                    sclera: UIColor, lash: UIColor, iris: UIColor, pupil: UIColor,
                                    irisFill: CGFloat = 0.65) {
        cg.setFillColor(sclera.cgColor)
        cg.fillEllipse(in: CGRect(x: x - w, y: y - h, width: w * 2, height: h * 2))

        cg.setFillColor(iris.cgColor)
        let irisW = w * irisFill, irisH = h * 0.85
        cg.fillEllipse(in: CGRect(x: x - irisW, y: y - irisH, width: irisW * 2, height: irisH * 2))

        cg.setFillColor(pupil.cgColor)
        cg.fillEllipse(in: CGRect(x: x - w * 0.28, y: y - h * 0.35, width: w * 0.56, height: h * 0.7))

        cg.setFillColor(UIColor.white.cgColor)
        cg.fillEllipse(in: CGRect(x: x - w * 0.33, y: y - h * 0.43, width: w * 0.26, height: h * 0.26))

        cg.setStrokeColor(lash.cgColor)
        cg.setLineWidth(3.5)
        cg.strokeEllipse(in: CGRect(x: x - w - 1, y: y - 1, width: w * 2 + 2, height: h + 2))
    }

    private static func drawBrows(_ cg: CGContext, cx: CGFloat, y: CGFloat, dx: CGFloat,
                                  expr: Expression, color: UIColor) {
        cg.setStrokeColor(color.cgColor)
        cg.setLineWidth(5)
        cg.setLineCap(.round)
        for sgn in [CGFloat(-1), CGFloat(1)] {
            let x = cx + sgn * dx
            switch expr {
            case .pensive:
                cg.move(to: CGPoint(x: x - sgn * 18, y: y))
                cg.addLine(to: CGPoint(x: x + sgn * 18, y: y - 6))
                cg.strokePath()
            case .surprised:
                cg.move(to: CGPoint(x: x - 18, y: y + 4))
                cg.addQuadCurve(to: CGPoint(x: x + 18, y: y + 4), control: CGPoint(x: x, y: y - 8))
                cg.strokePath()
            case .joy:
                cg.move(to: CGPoint(x: x - 18, y: y + 2))
                cg.addQuadCurve(to: CGPoint(x: x + 18, y: y + 2), control: CGPoint(x: x, y: y - 4))
                cg.strokePath()
            default:
                cg.move(to: CGPoint(x: x - 18, y: y + 2))
                cg.addLine(to: CGPoint(x: x + 18, y: y + 2))
                cg.strokePath()
            }
        }
    }

    private static func drawMouth(_ cg: CGContext, cx: CGFloat, y: CGFloat, expr: Expression) {
        cg.setLineCap(.round)
        cg.setLineJoin(.round)
        switch expr {
        case .joy:
            cg.setFillColor(UIColor(red: 0.23, green: 0.10, blue: 0.15, alpha: 1).cgColor)
            cg.beginPath()
            cg.move(to: CGPoint(x: cx - 22, y: y))
            cg.addQuadCurve(to: CGPoint(x: cx + 22, y: y), control: CGPoint(x: cx, y: y + 22))
            cg.addQuadCurve(to: CGPoint(x: cx - 22, y: y), control: CGPoint(x: cx, y: y + 4))
            cg.fillPath()
            cg.setFillColor(UIColor.white.cgColor)
            cg.fill(CGRect(x: cx - 16, y: y + 2, width: 32, height: 4))
        case .smirk:
            cg.setStrokeColor(UIColor(red: 0.63, green: 0.25, blue: 0.31, alpha: 1).cgColor)
            cg.setLineWidth(4)
            cg.beginPath()
            cg.move(to: CGPoint(x: cx - 18, y: y + 2))
            cg.addQuadCurve(to: CGPoint(x: cx + 18, y: y - 6), control: CGPoint(x: cx + 3, y: y + 8))
            cg.strokePath()
        case .surprised:
            cg.setFillColor(UIColor(red: 0.23, green: 0.10, blue: 0.15, alpha: 1).cgColor)
            cg.fillEllipse(in: CGRect(x: cx - 7, y: y - 7, width: 14, height: 18))
        case .pensive:
            cg.setStrokeColor(UIColor(red: 0.63, green: 0.25, blue: 0.31, alpha: 1).cgColor)
            cg.setLineWidth(4)
            cg.beginPath()
            cg.move(to: CGPoint(x: cx - 16, y: y))
            cg.addQuadCurve(to: CGPoint(x: cx + 16, y: y), control: CGPoint(x: cx, y: y - 6))
            cg.strokePath()
        case .neutral:
            cg.setStrokeColor(UIColor(red: 0.63, green: 0.25, blue: 0.31, alpha: 1).cgColor)
            cg.setLineWidth(4)
            cg.move(to: CGPoint(x: cx - 14, y: y))
            cg.addLine(to: CGPoint(x: cx + 14, y: y))
            cg.strokePath()
        }
    }

    private static func drawBlush(_ cg: CGContext, cx: CGFloat, y: CGFloat, dx: CGFloat) {
        cg.setFillColor(UIColor(red: 1, green: 0.51, blue: 0.59, alpha: 0.45).cgColor)
        for sgn in [CGFloat(-1), CGFloat(1)] {
            cg.fillEllipse(in: CGRect(x: cx + sgn * dx * 1.05 - 16, y: y - 8, width: 32, height: 16))
        }
    }

    private static func pickIris(style: AvatarStyle) -> UIColor {
        // Slightly lighter than hair for visual pop.
        return darken(style.hairColor, by: -0.1)
    }

    private static func darken(_ color: UIColor, by amount: CGFloat) -> UIColor {
        var r: CGFloat = 0, g: CGFloat = 0, b: CGFloat = 0, a: CGFloat = 1
        color.getRed(&r, green: &g, blue: &b, alpha: &a)
        let k = 1.0 - amount
        return UIColor(red: clamp(r * k), green: clamp(g * k), blue: clamp(b * k), alpha: a)
    }

    private static func clamp(_ v: CGFloat) -> CGFloat { min(1, max(0, v)) }
}
