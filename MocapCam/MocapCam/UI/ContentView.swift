import AVFoundation
import SwiftUI
import UIKit

struct ContentView: View {
    @EnvironmentObject private var model: CaptureAppModel
    @State private var isShowingSettings = false
    @State private var isDisplayHidden = false
    @State private var videoOrientation: AVCaptureVideoOrientation = .landscapeRight

    var body: some View {
        ZStack {
            CameraPreviewView(session: model.previewSession, videoOrientation: videoOrientation)
                .ignoresSafeArea()

            if model.isDepthOverlayEnabled, let depthOverlayImage = model.depthOverlayImage {
                GeometryReader { proxy in
                    ZStack {
                        Image(uiImage: depthOverlayImage)
                            .resizable()
                            .interpolation(.none)
                            .scaledToFill()
                            .frame(width: proxy.size.width, height: proxy.size.height)
                            .scaleEffect(model.depthOverlayScale)
                            .offset(
                                x: proxy.size.width * model.depthOverlayOffsetX,
                                y: proxy.size.height * model.depthOverlayOffsetY
                            )
                    }
                    .frame(width: proxy.size.width, height: proxy.size.height)
                    .clipped()
                }
                .opacity(model.depthOverlayOpacity)
                .ignoresSafeArea()
                .allowsHitTesting(false)
            }

            LinearGradient(
                colors: [.black.opacity(0.72), .clear, .black.opacity(0.82)],
                startPoint: .top,
                endPoint: .bottom
            )
            .ignoresSafeArea()

            VStack(spacing: 0) {
                topBar
                Spacer()
                bottomPanel
            }

            if isDisplayHidden {
                hiddenDisplayCurtain
            }
        }
        .foregroundStyle(.white)
        .preferredColorScheme(.dark)
        .statusBarHidden(isDisplayHidden)
        .persistentSystemOverlays(isDisplayHidden ? .hidden : .automatic)
        .sheet(isPresented: $isShowingSettings) {
            CameraSettingsView(model: model, isDisplayHidden: $isDisplayHidden)
        }
        .onAppear {
            updateVideoOrientation()
        }
        .onReceive(NotificationCenter.default.publisher(for: UIDevice.orientationDidChangeNotification)) { _ in
            updateVideoOrientation()
        }
    }

    private func updateVideoOrientation() {
        let orientation = AVCaptureVideoOrientation.currentInterfaceOrientation()
        videoOrientation = orientation
        model.updateVideoOrientation(orientation)
    }

    private var topBar: some View {
        HStack(spacing: 14) {
            Label(model.networkLabel, systemImage: "network")
                .font(.footnote.weight(.semibold))
                .lineLimit(1)

            Spacer()

            statusPill(icon: "speedometer", text: String(format: "%.1f fps", model.currentFPS))
            statusPill(icon: "viewfinder", text: String(format: "%.1f dps", model.currentDepthFPS))
            statusPill(icon: "thermometer.medium", text: model.thermalState)
            statusPill(icon: "battery.75", text: model.batteryLabel)

            Button {
                isDisplayHidden = true
            } label: {
                Image(systemName: "eye.slash")
                    .font(.title3.weight(.semibold))
                    .frame(width: 36, height: 36)
                    .background(.white.opacity(0.14), in: Circle())
            }
            .buttonStyle(.plain)

            Button {
                isShowingSettings = true
            } label: {
                Image(systemName: "slider.horizontal.3")
                    .font(.title3.weight(.semibold))
                    .frame(width: 36, height: 36)
                    .background(.white.opacity(0.14), in: Circle())
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .background(.black.opacity(0.28))
    }

    private var bottomPanel: some View {
        VStack(spacing: 12) {
            HStack(spacing: 12) {
                labeledField("Device", text: $model.deviceID)
                labeledField("Session", text: $model.sessionID)
            }

            HStack(spacing: 12) {
                readout(icon: "video.fill", text: model.isPreviewing ? "Streaming" : "Starting")
                readout(icon: "scope", text: model.cameraSettings.resolution.displayName)
                readout(icon: "timer", text: "\(model.cameraSettings.fps) fps")
                readout(icon: model.isDepthPreviewing ? "cube.transparent.fill" : "cube.transparent", text: model.isDepthPreviewing ? "LiDAR" : "RGB")
            }

            if let message = model.lastError {
                Text(message)
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(.red.opacity(0.95))
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .lineLimit(2)
            }
        }
        .padding(16)
        .background(.ultraThinMaterial)
    }

    private var hiddenDisplayCurtain: some View {
        Color.black
            .ignoresSafeArea()
            .contentShape(Rectangle())
            .onTapGesture {
                isDisplayHidden = false
            }
    }

    private func statusPill(icon: String, text: String) -> some View {
        Label(text, systemImage: icon)
            .font(.caption.weight(.semibold))
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            .background(.white.opacity(0.14), in: Capsule())
            .lineLimit(1)
    }

    private func readout(icon: String, text: String) -> some View {
        Label(text, systemImage: icon)
            .font(.callout.weight(.semibold))
            .frame(maxWidth: .infinity)
            .padding(.vertical, 11)
            .background(.black.opacity(0.32), in: RoundedRectangle(cornerRadius: 8))
            .lineLimit(1)
    }

    private func labeledField(_ label: String, text: Binding<String>) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label.uppercased())
                .font(.caption2.weight(.bold))
                .foregroundStyle(.white.opacity(0.62))
            TextField(label, text: text)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .font(.callout.monospaced())
                .padding(.horizontal, 10)
                .padding(.vertical, 9)
                .background(.black.opacity(0.35), in: RoundedRectangle(cornerRadius: 8))
        }
    }

}

private struct CameraSettingsView: View {
    @ObservedObject var model: CaptureAppModel
    @Binding var isDisplayHidden: Bool
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationView {
            Form {
                Section("Camera") {
                    Picker("Camera", selection: setting(\.cameraSelection)) {
                        ForEach(model.supportedCameraSettings.cameraSelections) { option in
                            Text(option.depthSupported ? option.displayName : "\(option.displayName) RGB")
                                .tag(option.id)
                        }
                    }

                    Picker("Resolution", selection: setting(\.resolution)) {
                        ForEach(model.supportedCameraSettings.resolutions) { resolution in
                            Text(resolution.displayName).tag(resolution)
                        }
                    }

                    Picker("FPS", selection: setting(\.fps)) {
                        ForEach(model.supportedCameraSettings.fps, id: \.self) { fps in
                            Text("\(fps)").tag(fps)
                        }
                    }
                }

                Section("Image") {
                    Picker("Exposure", selection: setting(\.exposureMode)) {
                        ForEach(model.supportedCameraSettings.exposureModes) { mode in
                            Text(mode.displayName).tag(mode)
                        }
                    }

                    Slider(
                        value: setting(\.exposureBias),
                        in: -4...4,
                        step: 0.1
                    ) {
                        Text("Exposure Bias")
                    } minimumValueLabel: {
                        Text("-4")
                    } maximumValueLabel: {
                        Text("+4")
                    }

                    Picker("Focus", selection: setting(\.focusMode)) {
                        ForEach(model.supportedCameraSettings.focusModes) { mode in
                            Text(mode.displayName).tag(mode)
                        }
                    }

                    Picker("White Balance", selection: setting(\.whiteBalanceMode)) {
                        ForEach(model.supportedCameraSettings.whiteBalanceModes) { mode in
                            Text(mode.displayName).tag(mode)
                        }
                    }
                }

                Section("Display") {
                    Button {
                        dismiss()
                        isDisplayHidden = true
                    } label: {
                        Label("Hide Display", systemImage: "eye.slash")
                    }
                }

                Section("LiDAR") {
                    Picker("Depth Mode", selection: setting(\.depthMode)) {
                        ForEach(model.supportedCameraSettings.depthModes) { mode in
                            Text(mode.displayName).tag(mode)
                        }
                    }
                    Toggle("Overlay", isOn: $model.isDepthOverlayEnabled)
                    Toggle("Auto 7x5 Align", isOn: $model.isDepthOverlayAutoAlignmentEnabled)
                    HStack {
                        Text("Alignment")
                        Spacer()
                        Text(model.charucoAlignmentStatus)
                            .foregroundStyle(.secondary)
                            .multilineTextAlignment(.trailing)
                    }
                    Slider(value: $model.depthOverlayOpacity, in: 0.15...0.85) {
                        Text("Opacity")
                    }
                    Slider(value: $model.depthOverlayOffsetX, in: -0.18...0.18, step: 0.0025) {
                        Text("X Align")
                    } minimumValueLabel: {
                        Text("L")
                    } maximumValueLabel: {
                        Text("R")
                    }
                    .disabled(model.isDepthOverlayAutoAlignmentEnabled)
                    Slider(value: $model.depthOverlayOffsetY, in: -0.18...0.18, step: 0.0025) {
                        Text("Y Align")
                    } minimumValueLabel: {
                        Text("Up")
                    } maximumValueLabel: {
                        Text("Down")
                    }
                    .disabled(model.isDepthOverlayAutoAlignmentEnabled)
                    Slider(value: $model.depthOverlayScale, in: 0.86...1.20, step: 0.0025) {
                        Text("Scale")
                    } minimumValueLabel: {
                        Text("-")
                    } maximumValueLabel: {
                        Text("+")
                    }
                    .disabled(model.isDepthOverlayAutoAlignmentEnabled)
                    Button("Reset Alignment") {
                        model.resetDepthOverlayAlignment()
                    }
                    HStack {
                        Text("Depth")
                        Spacer()
                        Text(model.isDepthPreviewing ? "On" : "Unavailable")
                            .foregroundStyle(.secondary)
                    }
                }
            }
            .navigationTitle("Settings")
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") {
                        dismiss()
                    }
                }
            }
        }
        .preferredColorScheme(.dark)
    }

    private func setting<Value>(_ keyPath: WritableKeyPath<CameraSettings, Value>) -> Binding<Value> {
        Binding(
            get: {
                model.cameraSettings[keyPath: keyPath]
            },
            set: { newValue in
                var settings = model.cameraSettings
                settings[keyPath: keyPath] = newValue
                model.applyCameraSettings(settings)
            }
        )
    }
}

struct ContentView_Previews: PreviewProvider {
    static var previews: some View {
        ContentView()
            .environmentObject(CaptureAppModel())
    }
}
