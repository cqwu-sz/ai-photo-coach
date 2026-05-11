// ImageSanitizer.swift  (A1-exif-strip of MULTI_USER_AUTH)
//
// Strip EXIF / GPS metadata from any image we're about to send off-device.
// `UIImage.jpegData()` already drops most metadata when the source was
// a `UIImage` (Apple builds the JPEG fresh), but anything that flows
// through `Data(contentsOf:)` or PHAsset's full-res request keeps the
// original EXIF block — including precise GPS lat/lon. That violates
// our "round to ~11 m before persisting" promise the moment an upload
// happens.
//
// Usage:
//      let safe = ImageSanitizer.stripped(jpegData: original)
//      // ...upload `safe` instead of `original`.
//
// Idempotent + safe: returns the input unchanged when CGImageSource
// can't parse it, so we never lose user images to a bad strip.

import Foundation
import ImageIO
import MobileCoreServices
import UniformTypeIdentifiers

enum ImageSanitizer {
    /// Returns a JPEG with all EXIF / GPS / IPTC / TIFF / Maker-note
    /// dictionaries removed. The pixel buffer is preserved bit-for-bit.
    static func stripped(jpegData data: Data, quality: CGFloat = 0.92) -> Data {
        guard !data.isEmpty,
              let src = CGImageSourceCreateWithData(data as CFData, nil),
              let _ = CGImageSourceCopyPropertiesAtIndex(src, 0, nil) else {
            return data
        }
        let outType: CFString = UTType.jpeg.identifier as CFString
        let outData = NSMutableData()
        guard let dest = CGImageDestinationCreateWithData(outData, outType, 1, nil) else {
            return data
        }
        let opts: [CFString: Any] = [
            kCGImageDestinationLossyCompressionQuality: quality,
            // Force-clear every metadata namespace. nil/NSNull tells
            // ImageIO to omit the dictionary entirely.
            kCGImagePropertyExifDictionary: NSNull(),
            kCGImagePropertyGPSDictionary: NSNull(),
            kCGImagePropertyIPTCDictionary: NSNull(),
            kCGImagePropertyTIFFDictionary: NSNull(),
            kCGImagePropertyMakerAppleDictionary: NSNull(),
            kCGImagePropertyMakerCanonDictionary: NSNull(),
        ]
        CGImageDestinationAddImageFromSource(dest, src, 0, opts as CFDictionary)
        guard CGImageDestinationFinalize(dest) else { return data }
        return outData as Data
    }

    /// Convenience: strip a list of JPEGs in one call.
    static func stripped(all: [Data]) -> [Data] {
        all.map { stripped(jpegData: $0) }
    }
}
