import SceneKit

/// Swift port of web/js/pose_presets.js.
///
/// Each preset rotates a subset of the joints produced by
/// AvatarBuilderSCN. We keep the joint *names* identical to the JS
/// builder so the same AI-rationale-driven preset names work on both
/// clients.
enum PosePresets {
static let all: [String] = [
        "standing", "hands_clasped", "walking", "half_sit", "crouch",
        "looking_back", "holding_hands", "hand_on_hip", "v_sign",
    "arms_crossed", "facing_partner", "leaning", "pockets",
    "shy_pose", "hands_back", "wave",
    ]

static func apply(_ name: String, joints: [String: SCNNode], mirror: Bool = false) {
        // Always start from standing so any unset joint stays sensible.
        standing(joints)
        switch name {
        case "hands_clasped": handsClasped(joints)
        case "walking": walking(joints, mirror: mirror)
        case "half_sit": halfSit(joints)
        case "crouch": crouch(joints)
        case "looking_back": lookingBack(joints, mirror: mirror)
        case "holding_hands": holdingHands(joints, mirror: mirror)
        case "hand_on_hip": handOnHip(joints, mirror: mirror)
        case "v_sign": vSign(joints, mirror: mirror)
        case "arms_crossed": armsCrossed(joints)
        case "facing_partner": facingPartner(joints, mirror: mirror)
        case "leaning": leaning(joints, mirror: mirror)
        case "pockets": pockets(joints, mirror: mirror)
        case "shy_pose": shyPose(joints, mirror: mirror)
        case "hands_back": handsBack(joints, mirror: mirror)
        case "wave": wave(joints, mirror: mirror)
        default: break // standing already applied
        }
    }

static func pick(for person: PersonPose) -> String {
        let blob = [
            person.stance ?? "",
            person.upperBody ?? "",
            person.hands ?? "",
            person.gaze ?? "",
            person.positionHint ?? "",
        ].joined(separator: " ").lowercased()

        if blob.range(of: #"v\s?字|v[-\s]?sign|比耶|比v|peace"#,
                      options: .regularExpression) != nil { return "v_sign" }
    if blob.range(of: #"挥手|招手|wave|hello|打招呼"#,
              options: .regularExpression) != nil { return "wave" }
    if blob.range(of: #"插兜|口袋|pocket"#,
              options: .regularExpression) != nil { return "pockets" }
    if blob.range(of: #"背手|双手背后|手放身后|behind\s+back"#,
              options: .regularExpression) != nil { return "hands_back" }
    if blob.range(of: #"害羞|甜美|可爱|少女|提裙|轻提裙摆|shy|cute|sweet"#,
              options: .regularExpression) != nil { return "shy_pose" }
        if blob.range(of: #"牵手|拉手|hold(?:ing)?\s+hand"#,
                      options: .regularExpression) != nil { return "holding_hands" }
        if blob.range(of: #"回头|看向|看着|gaze|over\s+shoulder"#,
                      options: .regularExpression) != nil { return "looking_back" }
        if blob.range(of: #"抱臂|交叉|cross(?:ed)?\s+arms"#,
                      options: .regularExpression) != nil { return "arms_crossed" }
        if blob.range(of: #"叉腰|手插腰|hand\s+on\s+hip"#,
                      options: .regularExpression) != nil { return "hand_on_hip" }
        if blob.range(of: #"坐|sit|半坐"#,
                      options: .regularExpression) != nil { return "half_sit" }
        if blob.range(of: #"蹲|crouch|squat|半蹲"#,
                      options: .regularExpression) != nil { return "crouch" }
        if blob.range(of: #"靠|lean(?:ing)?"#,
                      options: .regularExpression) != nil { return "leaning" }
        if blob.range(of: #"走|漫步|散步|walk(?:ing)?|stride|前行|向前"#,
                      options: .regularExpression) != nil { return "walking" }
        if blob.range(of: #"对视|面向|相对|face"#,
                      options: .regularExpression) != nil { return "facing_partner" }
        if blob.range(of: #"双手|交握|clasp"#,
                      options: .regularExpression) != nil { return "hands_clasped" }
        return "standing"
    }

static func classifyExpression(_ person: PersonPose) -> ExpressionRenderer.Expression {
        let t = (person.expression ?? "").lowercased()
        if t.isEmpty { return .neutral }
        if t.range(of: #"抿嘴|smirk|淡笑|微微一笑"#, options: .regularExpression) != nil { return .smirk }
        if t.range(of: #"惊|surprised|wide-?eyed|睁大|意外"#, options: .regularExpression) != nil { return .surprised }
        if t.range(of: #"认真|沉思|皱眉|pensive|frown"#, options: .regularExpression) != nil { return .pensive }
        if t.range(of: #"笑|smile|joy|happy|开心|愉悦|轻松"#, options: .regularExpression) != nil { return .joy }
        return .neutral
    }

    // ---- presets --------------------------------------------------------------

    private static let d2r: Float = .pi / 180

    private static func setEuler(_ j: SCNNode?, x: Float = 0, y: Float = 0, z: Float = 0) {
        j?.eulerAngles = SCNVector3(x * d2r, y * d2r, z * d2r)
    }

    private static func standing(_ j: [String: SCNNode]) {
        setEuler(j["leftShoulder"], x: 6, y: 4, z: -10)
        setEuler(j["rightShoulder"], x: 6, y: -4, z: 10)
        setEuler(j["leftElbow"], x: 12)
        setEuler(j["rightElbow"], x: 12)
        setEuler(j["leftForearm"], y: -4)
        setEuler(j["rightForearm"], y: 4)
        setEuler(j["leftHip"]); setEuler(j["rightHip"])
        setEuler(j["leftKnee"]); setEuler(j["rightKnee"])
        setEuler(j["head"], x: -1)
        setEuler(j["neck"])
    }

    private static func pockets(_ j: [String: SCNNode], mirror: Bool) {
        let pocket = mirror ? "right" : "left"
        let free = mirror ? "left" : "right"
        setEuler(j["\(pocket)Shoulder"], x: 10, y: mirror ? -6 : 6, z: mirror ? 18 : -18)
        setEuler(j["\(pocket)Elbow"], x: 38)
        setEuler(j["\(pocket)Forearm"], y: mirror ? -14 : 14)
        setEuler(j["\(free)Shoulder"], x: 4, y: mirror ? 2 : -2, z: mirror ? -8 : 8)
        setEuler(j["\(free)Elbow"], x: 10)
        setEuler(j["torso"], z: mirror ? 4 : -4)
        setEuler(j["head"], x: -2, y: mirror ? -8 : 8)
    }

    private static func shyPose(_ j: [String: SCNNode], mirror: Bool) {
        setEuler(j["leftShoulder"], x: 8, y: 8, z: -24)
        setEuler(j["rightShoulder"], x: 8, y: -8, z: 24)
        setEuler(j["leftElbow"], x: 72)
        setEuler(j["rightElbow"], x: 72)
        setEuler(j["leftForearm"], y: -12)
        setEuler(j["rightForearm"], y: 12)
        setEuler(j["torso"], x: 2, z: mirror ? 5 : -5)
        setEuler(j["head"], x: 6, y: mirror ? -12 : 12)
    }

    private static func handsBack(_ j: [String: SCNNode], mirror: Bool) {
        setEuler(j["leftShoulder"], x: -16, y: -10, z: mirror ? 8 : -8)
        setEuler(j["rightShoulder"], x: -16, y: 10, z: mirror ? -8 : 8)
        setEuler(j["leftElbow"], x: 28)
        setEuler(j["rightElbow"], x: 28)
        setEuler(j["leftForearm"], y: -18)
        setEuler(j["rightForearm"], y: 18)
        setEuler(j["head"], x: -2, y: mirror ? -6 : 6)
    }

    private static func wave(_ j: [String: SCNNode], mirror: Bool) {
        let waving = mirror ? "left" : "right"
        let free = mirror ? "right" : "left"
        setEuler(j["\(waving)Shoulder"], x: -42, z: mirror ? -36 : 36)
        setEuler(j["\(waving)Elbow"], x: 94)
        setEuler(j["\(waving)Forearm"], y: mirror ? -10 : 10)
        setEuler(j["\(free)Shoulder"], x: 4, z: mirror ? 10 : -10)
        setEuler(j["\(free)Elbow"], x: 10)
        setEuler(j["head"], x: -1, y: mirror ? -10 : 10)
    }

    private static func handsClasped(_ j: [String: SCNNode]) {
        setEuler(j["leftShoulder"], y: 10, z: -50)
        setEuler(j["rightShoulder"], y: -10, z: 50)
        setEuler(j["leftElbow"], x: 90)
        setEuler(j["rightElbow"], x: 90)
        setEuler(j["leftForearm"], y: -25)
        setEuler(j["rightForearm"], y: 25)
    }

    private static func walking(_ j: [String: SCNNode], mirror: Bool) {
        let f: Float = mirror ? -1 : 1
        setEuler(j["leftShoulder"], x: -25 * f, z: -75)
        setEuler(j["rightShoulder"], x: 25 * f, z: 75)
        setEuler(j["leftElbow"], x: 25)
        setEuler(j["rightElbow"], x: 25)
        setEuler(j["leftHip"], x: 22 * f)
        setEuler(j["rightHip"], x: -22 * f)
        setEuler(j["leftKnee"], x: 12)
        setEuler(j["head"], x: 5, y: mirror ? 8 : -8)
    }

    private static func halfSit(_ j: [String: SCNNode]) {
        setEuler(j["leftHip"], x: 70, y: 6)
        setEuler(j["rightHip"], x: 70, y: -6)
        setEuler(j["leftKnee"], x: -90); setEuler(j["rightKnee"], x: -90)
        setEuler(j["leftShoulder"], y: 5, z: -60)
        setEuler(j["rightShoulder"], y: -5, z: 60)
        setEuler(j["leftElbow"], x: 65); setEuler(j["rightElbow"], x: 65)
    }

    private static func crouch(_ j: [String: SCNNode]) {
        setEuler(j["leftHip"], x: 95, y: 8); setEuler(j["rightHip"], x: 95, y: -8)
        setEuler(j["leftKnee"], x: -130); setEuler(j["rightKnee"], x: -130)
        setEuler(j["leftShoulder"], x: 25, z: -50); setEuler(j["rightShoulder"], x: 25, z: 50)
        setEuler(j["leftElbow"], x: 50); setEuler(j["rightElbow"], x: 50)
        setEuler(j["head"], x: 6)
    }

    private static func lookingBack(_ j: [String: SCNNode], mirror: Bool) {
        setEuler(j["head"], x: -3, y: mirror ? -110 : 110)
        setEuler(j["neck"], y: mirror ? -25 : 25)
        setEuler(j["torso"], y: mirror ? -8 : 8)
        setEuler(j["leftShoulder"], y: 10, z: -55); setEuler(j["rightShoulder"], y: -10, z: 55)
        setEuler(j["leftElbow"], x: 70); setEuler(j["rightElbow"], x: 70)
    }

    private static func holdingHands(_ j: [String: SCNNode], mirror: Bool) {
        let inner = mirror ? "right" : "left"
        let outer = mirror ? "left" : "right"
        setEuler(j["\(inner)Shoulder"], x: 5, z: mirror ? 70 : -70)
        setEuler(j["\(inner)Elbow"], x: 12)
        setEuler(j["\(outer)Shoulder"], z: mirror ? -75 : 75)
        setEuler(j["\(outer)Elbow"], x: 5)
        setEuler(j["head"], x: -3, y: mirror ? -8 : 8)
    }

    private static func handOnHip(_ j: [String: SCNNode], mirror: Bool) {
        let side = mirror ? "right" : "left"
        let other = mirror ? "left" : "right"
        setEuler(j["\(side)Shoulder"], z: mirror ? 30 : -30)
        setEuler(j["\(side)Elbow"], x: 100)
        setEuler(j["\(other)Shoulder"], z: mirror ? -78 : 78)
        setEuler(j["\(other)Elbow"], x: 5)
        setEuler(j["torso"], z: mirror ? 4 : -4)
        setEuler(j["head"], x: -2, y: mirror ? 6 : -6)
    }

    private static func vSign(_ j: [String: SCNNode], mirror: Bool) {
        let v = mirror ? "left" : "right"
        let other = mirror ? "right" : "left"
        setEuler(j["\(v)Shoulder"], x: -50, z: mirror ? -45 : 45)
        setEuler(j["\(v)Elbow"], x: 95)
        setEuler(j["\(other)Shoulder"], z: mirror ? 78 : -78)
        setEuler(j["\(other)Elbow"], x: 5)
        setEuler(j["head"], x: -2, y: mirror ? -4 : 4)
    }

    private static func armsCrossed(_ j: [String: SCNNode]) {
        setEuler(j["leftShoulder"], y: 30, z: -50)
        setEuler(j["rightShoulder"], y: -30, z: 50)
        setEuler(j["leftElbow"], x: 95); setEuler(j["rightElbow"], x: 95)
        setEuler(j["leftForearm"], y: 35); setEuler(j["rightForearm"], y: -35)
    }

    private static func facingPartner(_ j: [String: SCNNode], mirror: Bool) {
        setEuler(j["torso"], y: mirror ? -18 : 18)
        setEuler(j["head"], x: -3, y: mirror ? -25 : 25)
        setEuler(j["leftShoulder"], y: 5, z: -65); setEuler(j["rightShoulder"], y: -5, z: 65)
        setEuler(j["leftElbow"], x: 35); setEuler(j["rightElbow"], x: 35)
    }

    private static func leaning(_ j: [String: SCNNode], mirror: Bool) {
        setEuler(j["torso"], x: -3, z: mirror ? 6 : -6)
        setEuler(j["leftShoulder"], x: mirror ? -30 : 0, z: mirror ? 30 : -78)
        setEuler(j["rightShoulder"], x: mirror ? 0 : -30, z: mirror ? 78 : -30)
        setEuler(j["head"], y: mirror ? -10 : 10)
    }
}
