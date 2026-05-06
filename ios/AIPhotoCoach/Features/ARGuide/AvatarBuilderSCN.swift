import SceneKit
import UIKit

/// SceneKit port of web/js/avatar_builder.js. Produces a single SCNNode
/// hierarchy you can drop into an ARAnchor child node — no shared
/// vertices, no skinning, just rotated SCNNode joints. Same skeleton
/// names as the JS version so PosePresets.swift stays in sync.
public final class AvatarBuilderSCN {
    public struct Built {
        public let root: SCNNode
        public let joints: [String: SCNNode]
        public let facePlate: SCNNode
        public let style: AvatarStyle
    }

    public static func build(_ style: AvatarStyle) -> Built {
        let total = style.height
        let headH = total * 0.13
        let neckH = total * 0.025
        let torsoH = total * 0.32
        let upperArmH = total * 0.18
        let forearmH = total * 0.16
        let thighH = total * 0.245
        let calfH = total * 0.245
        let footL = total * 0.06

        let torsoW = total * (style.gender == .male ? 0.16 : 0.14)
        let torsoD = total * 0.085
        let hipW = total * (style.gender == .male ? 0.13 : 0.12)
        let armR = total * 0.025
        let legR = total * 0.035

        let skinMat = pbr(style.skinColor, roughness: 0.78)
        let hairMat = pbr(style.hairColor, roughness: 0.55, metalness: 0.05)
        let topMat = pbr(style.topColor, roughness: 0.85)
        let bottomMat = pbr(style.bottomColor, roughness: 0.85)
        let shoeMat = pbr(style.shoeColor, roughness: 0.65, metalness: 0.05)

        var joints: [String: SCNNode] = [:]

        let root = SCNNode(); root.name = "avatarRoot"

        let pelvis = SCNNode(); pelvis.name = "pelvis"
        pelvis.position = SCNVector3(0, Float(thighH + calfH), 0)
        root.addChildNode(pelvis); joints["pelvis"] = pelvis

        // Hip block
        let hipMesh = SCNNode(geometry: SCNBox(
            width: hipW * 1.05, height: total * 0.06, length: torsoD, chamferRadius: 0.0))
        hipMesh.geometry?.firstMaterial = bottomMat
        hipMesh.position.y = -Float(total * 0.005)
        pelvis.addChildNode(hipMesh)

        // Torso
        let torso = SCNNode(); torso.name = "torso"
        torso.position.y = Float(torsoH * 0.42)
        pelvis.addChildNode(torso); joints["torso"] = torso

        let torsoMesh = SCNNode(geometry: SCNBox(width: torsoW, height: torsoH, length: torsoD, chamferRadius: 0))
        torsoMesh.geometry?.firstMaterial = topMat
        torso.addChildNode(torsoMesh)

        decorateTorso(on: torso, style: style, torsoW: torsoW, torsoH: torsoH, torsoD: torsoD)

        // Neck
        let neck = SCNNode(); neck.name = "neck"
        neck.position.y = Float(torsoH * 0.5 + neckH * 0.5)
        torso.addChildNode(neck); joints["neck"] = neck
        let neckMesh = SCNNode(geometry: SCNCylinder(radius: total * 0.026, height: neckH))
        neckMesh.geometry?.firstMaterial = skinMat
        neck.addChildNode(neckMesh)

        // Head
        let head = SCNNode(); head.name = "head"
        head.position.y = Float(neckH * 0.5 + headH * 0.5)
        neck.addChildNode(head); joints["head"] = head
        buildHead(on: head, style: style, headH: headH, hairMat: hairMat, skinMat: skinMat)

        // Face plate
        let face = makeFacePlate(style: style, headH: headH)
        face.position = SCNVector3(0, Float(headH * 0.06), Float(headH * 0.42))
        head.addChildNode(face)

        // Arms
        for side in ["left", "right"] {
            let sgn: Float = side == "left" ? 1 : -1

            let shoulder = SCNNode(); shoulder.name = "\(side)Shoulder"
            shoulder.position = SCNVector3(
                sgn * Float(torsoW * 0.5 + armR * 0.4),
                Float(torsoH * 0.42), 0)
            torso.addChildNode(shoulder); joints["\(side)Shoulder"] = shoulder

            let upperArm = SCNNode(); upperArm.name = "\(side)Arm"
            upperArm.position.y = -Float(upperArmH * 0.5)
            shoulder.addChildNode(upperArm); joints["\(side)Arm"] = upperArm

            let armMesh = SCNNode(geometry: SCNCylinder(radius: armR, height: upperArmH))
            armMesh.geometry?.firstMaterial = sleeveMat(style: style, top: topMat, skin: skinMat)
            upperArm.addChildNode(armMesh)

            let elbow = SCNNode(); elbow.name = "\(side)Elbow"
            elbow.position.y = -Float(upperArmH * 0.5)
            upperArm.addChildNode(elbow); joints["\(side)Elbow"] = elbow

            let forearm = SCNNode(); forearm.name = "\(side)Forearm"
            forearm.position.y = -Float(forearmH * 0.5)
            elbow.addChildNode(forearm); joints["\(side)Forearm"] = forearm

            let forearmMesh = SCNNode(geometry: SCNCylinder(radius: armR * 0.85, height: forearmH))
            forearmMesh.geometry?.firstMaterial = forearmMat(style: style, top: topMat, skin: skinMat)
            forearm.addChildNode(forearmMesh)

            let hand = SCNNode(); hand.name = "\(side)Hand"
            hand.position.y = -Float(forearmH * 0.5)
            forearm.addChildNode(hand); joints["\(side)Hand"] = hand

            let handMesh = SCNNode(geometry: SCNSphere(radius: armR * 1.05))
            handMesh.geometry?.firstMaterial = skinMat
            handMesh.scale = SCNVector3(1, 1.3, 0.7)
            handMesh.position.y = -Float(armR * 0.5)
            hand.addChildNode(handMesh)
        }

        // Legs
        for side in ["left", "right"] {
            let sgn: Float = side == "left" ? 1 : -1

            let hip = SCNNode(); hip.name = "\(side)Hip"
            hip.position = SCNVector3(sgn * Float(hipW * 0.3), -Float(total * 0.005), 0)
            pelvis.addChildNode(hip); joints["\(side)Hip"] = hip

            let thigh = SCNNode(); thigh.name = "\(side)Thigh"
            thigh.position.y = -Float(thighH * 0.5)
            hip.addChildNode(thigh); joints["\(side)Thigh"] = thigh

            let thighMesh = SCNNode(geometry: SCNCylinder(radius: legR, height: thighH))
            thighMesh.geometry?.firstMaterial = legMat(style: style, isCalf: false, bottom: bottomMat, skin: skinMat)
            thigh.addChildNode(thighMesh)

            let knee = SCNNode(); knee.name = "\(side)Knee"
            knee.position.y = -Float(thighH * 0.5)
            thigh.addChildNode(knee); joints["\(side)Knee"] = knee

            let calf = SCNNode(); calf.name = "\(side)Calf"
            calf.position.y = -Float(calfH * 0.5)
            knee.addChildNode(calf); joints["\(side)Calf"] = calf

            let calfMesh = SCNNode(geometry: SCNCylinder(radius: legR * 0.78, height: calfH))
            calfMesh.geometry?.firstMaterial = legMat(style: style, isCalf: true, bottom: bottomMat, skin: skinMat)
            calf.addChildNode(calfMesh)

            let foot = SCNNode(); foot.name = "\(side)Foot"
            foot.position.y = -Float(calfH * 0.5)
            calf.addChildNode(foot); joints["\(side)Foot"] = foot

            let footMesh = SCNNode(geometry: SCNBox(width: legR * 1.5, height: legR * 0.6, length: footL, chamferRadius: 0))
            footMesh.geometry?.firstMaterial = shoeMat
            footMesh.position = SCNVector3(0, -Float(legR * 0.3), Float(footL * 0.3))
            foot.addChildNode(footMesh)
        }

        relaxedRest(joints)

        return Built(root: root, joints: joints, facePlate: face, style: style)
    }

    // ---- Decoration helpers ---------------------------------------------------

    private static func decorateTorso(on torso: SCNNode, style: AvatarStyle,
                                      torsoW: CGFloat, torsoH: CGFloat, torsoD: CGFloat) {
        switch style.top {
        case .hoodie:
            let hood = SCNNode(geometry: SCNSphere(radius: torsoW * 0.55))
            hood.geometry?.firstMaterial = pbr(style.topColor, roughness: 0.9)
            hood.position = SCNVector3(0, Float(torsoH * 0.45), -Float(torsoD * 0.4))
            hood.scale = SCNVector3(1, 0.9, 1.4)
            torso.addChildNode(hood)
        case .jacket:
            let split = SCNNode(geometry: SCNPlane(width: torsoW * 0.06, height: torsoH * 0.95))
            let mat = SCNMaterial(); mat.diffuse.contents = UIColor(white: 0, alpha: 0.6); mat.transparent.contents = UIColor(white: 1, alpha: 0.6)
            split.geometry?.firstMaterial = mat
            split.position = SCNVector3(0, 0, Float(torsoD * 0.51))
            torso.addChildNode(split)
        default: break
        }
        switch style.bottom {
        case .skirt, .longSkirt:
            let len: CGFloat = style.bottom == .longSkirt ? torsoH * 1.0 : torsoH * 0.5
            let cone = SCNCone(topRadius: torsoW * 0.55, bottomRadius: torsoW * 0.95, height: len)
            let n = SCNNode(geometry: cone)
            n.geometry?.firstMaterial = pbr(style.bottomColor, roughness: 0.9)
            n.position.y = -Float(torsoH * 0.5 + len * 0.5)
            torso.addChildNode(n)
        default: break
        }
        if style.top == .dress || style.bottom == .dress {
            let cone = SCNCone(topRadius: torsoW * 0.6, bottomRadius: torsoW * 1.0, height: torsoH * 0.9)
            let n = SCNNode(geometry: cone)
            n.geometry?.firstMaterial = pbr(style.topColor, roughness: 0.85)
            n.position.y = -Float(torsoH * 0.5 + torsoH * 0.4)
            torso.addChildNode(n)
        }
    }

    private static func buildHead(on head: SCNNode, style: AvatarStyle,
                                  headH: CGFloat, hairMat: SCNMaterial, skinMat: SCNMaterial) {
        let r = headH * 0.5
        let skull = SCNNode(geometry: SCNSphere(radius: r))
        skull.geometry?.firstMaterial = skinMat
        skull.scale = SCNVector3(0.95, 1.0, 0.95)
        head.addChildNode(skull)

        switch style.hair {
        case .buzz, .short, .sideSwept:
            let cap = SCNNode(geometry: SCNSphere(radius: r * 1.05))
            cap.geometry?.firstMaterial = hairMat
            cap.scale = SCNVector3(1, 0.55, 1)
            cap.position.y = Float(r * 0.05)
            head.addChildNode(cap)
            if style.hair != .buzz {
                let fringe = SCNNode(geometry: SCNBox(width: r * 0.95, height: r * 0.16, length: r * 0.18, chamferRadius: 0))
                fringe.geometry?.firstMaterial = hairMat
                fringe.position = SCNVector3(0, Float(r * 0.55), Float(r * 0.6))
                head.addChildNode(fringe)
            }
        case .bob:
            let cap = SCNNode(geometry: SCNSphere(radius: r * 1.12))
            cap.geometry?.firstMaterial = hairMat
            cap.scale = SCNVector3(1.05, 0.78, 1.05)
            head.addChildNode(cap)
        case .longStraight:
            let cap = SCNNode(geometry: SCNSphere(radius: r * 1.1))
            cap.geometry?.firstMaterial = hairMat
            cap.scale = SCNVector3(1, 0.85, 1)
            head.addChildNode(cap)
            let curtain = SCNNode(geometry: SCNBox(width: r * 2.0, height: r * 3.0, length: r * 0.5, chamferRadius: 0))
            curtain.geometry?.firstMaterial = hairMat
            curtain.position = SCNVector3(0, -Float(r * 1.2), -Float(r * 0.55))
            head.addChildNode(curtain)
        case .twinTails:
            let cap = SCNNode(geometry: SCNSphere(radius: r * 1.08))
            cap.geometry?.firstMaterial = hairMat
            cap.scale = SCNVector3(1, 0.7, 1)
            head.addChildNode(cap)
            for sgn in [Float(-1), Float(1)] {
                let tail = SCNNode(geometry: SCNCylinder(radius: r * 0.14, height: r * 2.4))
                tail.geometry?.firstMaterial = hairMat
                tail.position = SCNVector3(sgn * Float(r * 0.95), -Float(r * 0.3), 0)
                tail.eulerAngles.z = sgn * 0.18
                head.addChildNode(tail)
            }
        case .longCurly:
            let cap = SCNNode(geometry: SCNSphere(radius: r * 1.16))
            cap.geometry?.firstMaterial = hairMat
            cap.scale = SCNVector3(1.05, 0.85, 1.0)
            head.addChildNode(cap)
            for i in 0..<8 {
                let a = (CGFloat(i) / 8.0) * .pi * 2
                let puff = SCNNode(geometry: SCNSphere(radius: r * 0.32))
                puff.geometry?.firstMaterial = hairMat
                puff.position = SCNVector3(Float(sin(a) * r * 1.08),
                                           -Float(r * 0.6),
                                           Float(cos(a) * r * 1.08))
                head.addChildNode(puff)
            }
        case .wolfTail:
            let cap = SCNNode(geometry: SCNSphere(radius: r * 1.08))
            cap.geometry?.firstMaterial = hairMat
            cap.scale = SCNVector3(1, 0.7, 1)
            cap.position.y = Float(r * 0.04)
            head.addChildNode(cap)
            let tail = SCNNode(geometry: SCNCylinder(radius: r * 0.14, height: r * 1.6))
            tail.geometry?.firstMaterial = hairMat
            tail.position = SCNVector3(0, -Float(r * 0.6), -Float(r * 0.4))
            tail.eulerAngles.x = -0.2
            head.addChildNode(tail)
        }

        if style.accessory == .glasses {
            let lensMat = pbr(UIColor(white: 0.2, alpha: 1), roughness: 0.5, metalness: 0.5)
            for sgn in [Float(-1), Float(1)] {
                let l = SCNNode(geometry: SCNTorus(ringRadius: r * 0.13, pipeRadius: r * 0.018))
                l.geometry?.firstMaterial = lensMat
                l.position = SCNVector3(sgn * Float(r * 0.18), Float(r * 0.05), Float(r * 0.42))
                l.eulerAngles.x = .pi / 2
                head.addChildNode(l)
            }
        }
    }

    private static func makeFacePlate(style: AvatarStyle, headH: CGFloat) -> SCNNode {
        let img = ExpressionRenderer.render(.neutral, style: style, size: 256)
        let plane = SCNPlane(width: headH * 0.92, height: headH * 0.85)
        let mat = SCNMaterial()
        mat.diffuse.contents = img
        mat.isDoubleSided = false
        mat.transparent.contents = img.cgImage
        mat.transparencyMode = .aOne
        plane.firstMaterial = mat
        let node = SCNNode(geometry: plane)
        node.name = "facePlate"
        return node
    }

    // ---- materials ------------------------------------------------------------

    private static func pbr(_ color: UIColor, roughness: Float = 0.85, metalness: Float = 0) -> SCNMaterial {
        let m = SCNMaterial()
        m.lightingModel = .physicallyBased
        m.diffuse.contents = color
        m.roughness.contents = roughness
        m.metalness.contents = metalness
        return m
    }

    private static func sleeveMat(style: AvatarStyle, top: SCNMaterial, skin: SCNMaterial) -> SCNMaterial {
        // All sleeve types currently use the top material in this stylised model.
        return top
    }

    private static func forearmMat(style: AvatarStyle, top: SCNMaterial, skin: SCNMaterial) -> SCNMaterial {
        switch style.top {
        case .hoodie, .jacket, .sweater: return top
        default: return skin
        }
    }

    private static func legMat(style: AvatarStyle, isCalf: Bool,
                               bottom: SCNMaterial, skin: SCNMaterial) -> SCNMaterial {
        if (style.bottom == .skirt || style.bottom == .shorts) && isCalf { return skin }
        return bottom
    }

    private static func relaxedRest(_ joints: [String: SCNNode]) {
        let d2r: Float = .pi / 180
        joints["leftShoulder"]?.eulerAngles.z = -1.35
        joints["rightShoulder"]?.eulerAngles.z = 1.35
        joints["leftElbow"]?.eulerAngles.x = 0.1
        joints["rightElbow"]?.eulerAngles.x = 0.1
        joints["head"]?.eulerAngles.x = -2 * d2r
    }
}
