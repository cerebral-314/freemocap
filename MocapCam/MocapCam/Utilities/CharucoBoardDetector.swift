import AVFoundation
import CoreGraphics
import ImageIO
import Vision

struct CharucoBoardObservation {
    let normalizedRect: CGRect
    let confidence: Double
}

final class CharucoBoardDetector: @unchecked Sendable {
    private static let fallbackBoardAspectRatio: CGFloat = 7.0 / 5.0

    private let boardAspectRatio: CGFloat
    private let referenceImageAspectRatio: CGFloat

    init() {
        boardAspectRatio = Self.loadBoardInkAspectRatio()
        referenceImageAspectRatio = Self.loadReferenceImageAspectRatio()
    }

    func detect(in sampleBuffer: CMSampleBuffer) -> CharucoBoardObservation? {
        guard let pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else {
            return nil
        }

        let request = VNDetectRectanglesRequest()
        request.maximumObservations = 10
        request.minimumConfidence = 0.35
        request.minimumSize = 0.06
        request.quadratureTolerance = 35
        request.minimumAspectRatio = 0.35
        request.maximumAspectRatio = 2.4

        let handler = VNImageRequestHandler(cvPixelBuffer: pixelBuffer, orientation: .up, options: [:])
        do {
            try handler.perform([request])
        } catch {
            return nil
        }

        let imageSize = CGSize(
            width: CVPixelBufferGetWidth(pixelBuffer),
            height: CVPixelBufferGetHeight(pixelBuffer)
        )
        guard imageSize.width > 0, imageSize.height > 0 else {
            return nil
        }

        return request.results?
            .compactMap { observation in
                scoredObservation(observation, imageSize: imageSize)
            }
            .max { lhs, rhs in lhs.score < rhs.score }?
            .observation
    }

    private func scoredObservation(
        _ rectangle: VNRectangleObservation,
        imageSize: CGSize
    ) -> (observation: CharucoBoardObservation, score: Double)? {
        let boundingBox = rectangle.boundingBox.standardized
        guard boundingBox.width > 0, boundingBox.height > 0 else {
            return nil
        }

        let pixelWidth = boundingBox.width * imageSize.width
        let pixelHeight = boundingBox.height * imageSize.height
        guard pixelWidth > 0, pixelHeight > 0 else {
            return nil
        }

        let aspectRatio = pixelWidth / pixelHeight
        let aspectScore = max(
            Self.aspectScore(aspectRatio, target: boardAspectRatio),
            Self.aspectScore(aspectRatio, target: 1.0 / boardAspectRatio),
            Self.aspectScore(aspectRatio, target: referenceImageAspectRatio),
            Self.aspectScore(aspectRatio, target: 1.0 / referenceImageAspectRatio)
        )
        guard aspectScore > 0.12 else {
            return nil
        }

        let area = Double(boundingBox.width * boundingBox.height)
        let areaScore = min(max((area - 0.004) / 0.12, 0), 1)
        let score = Double(rectangle.confidence) * 0.45 + Double(aspectScore) * 0.45 + areaScore * 0.10
        let topLeftRect = CGRect(
            x: boundingBox.minX,
            y: 1.0 - boundingBox.maxY,
            width: boundingBox.width,
            height: boundingBox.height
        ).clampedToUnit()

        return (
            CharucoBoardObservation(normalizedRect: topLeftRect, confidence: score),
            score
        )
    }

    private static func aspectScore(_ aspectRatio: CGFloat, target: CGFloat) -> CGFloat {
        guard aspectRatio > 0, target > 0 else {
            return 0
        }

        let ratio = max(aspectRatio / target, target / aspectRatio)
        return max(0, 1.0 - ((ratio - 1.0) / 0.9))
    }

    private static func loadReferenceImageAspectRatio() -> CGFloat {
        guard let image = loadReferenceImage() else {
            return fallbackBoardAspectRatio
        }
        return CGFloat(image.width) / CGFloat(image.height)
    }

    private static func loadBoardInkAspectRatio() -> CGFloat {
        guard let image = loadReferenceImage() else {
            return fallbackBoardAspectRatio
        }

        let width = image.width
        let height = image.height
        let bytesPerPixel = 4
        let bytesPerRow = width * bytesPerPixel
        var pixels = [UInt8](repeating: 0, count: height * bytesPerRow)

        let didDraw = pixels.withUnsafeMutableBytes { rawBuffer -> Bool in
            guard let context = CGContext(
                data: rawBuffer.baseAddress,
                width: width,
                height: height,
                bitsPerComponent: 8,
                bytesPerRow: bytesPerRow,
                space: CGColorSpaceCreateDeviceRGB(),
                bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
            ) else {
                return false
            }
            context.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
            return true
        }
        guard didDraw else {
            return fallbackBoardAspectRatio
        }

        var minX = width
        var minY = height
        var maxX = 0
        var maxY = 0
        let sampleStep = max(1, min(width, height) / 700)

        for y in stride(from: 0, to: height, by: sampleStep) {
            for x in stride(from: 0, to: width, by: sampleStep) {
                let index = y * bytesPerRow + x * bytesPerPixel
                let red = Int(pixels[index])
                let green = Int(pixels[index + 1])
                let blue = Int(pixels[index + 2])
                let alpha = Int(pixels[index + 3])
                let luma = (red + green + blue) / 3
                if alpha > 16, luma < 210 {
                    minX = min(minX, x)
                    minY = min(minY, y)
                    maxX = max(maxX, x)
                    maxY = max(maxY, y)
                }
            }
        }

        guard minX < maxX, minY < maxY else {
            return fallbackBoardAspectRatio
        }

        let aspectRatio = CGFloat(maxX - minX + sampleStep) / CGFloat(maxY - minY + sampleStep)
        guard aspectRatio.isFinite, aspectRatio > 0.2, aspectRatio < 5 else {
            return fallbackBoardAspectRatio
        }
        return aspectRatio
    }

    private static func loadReferenceImage() -> CGImage? {
        guard let url = Bundle.module.url(forResource: "charuco_board_7x5", withExtension: "png"),
              let source = CGImageSourceCreateWithURL(url as CFURL, nil) else {
            return nil
        }
        return CGImageSourceCreateImageAtIndex(source, 0, nil)
    }
}

private extension CGRect {
    func clampedToUnit() -> CGRect {
        let clampedMinX = min(max(minX, 0), 1)
        let clampedMinY = min(max(minY, 0), 1)
        let clampedMaxX = min(max(maxX, 0), 1)
        let clampedMaxY = min(max(maxY, 0), 1)
        return CGRect(
            x: clampedMinX,
            y: clampedMinY,
            width: max(0, clampedMaxX - clampedMinX),
            height: max(0, clampedMaxY - clampedMinY)
        )
    }
}
