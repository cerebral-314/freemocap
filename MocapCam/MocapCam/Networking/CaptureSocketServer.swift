import Foundation
import Network

final class CaptureSocketServer: @unchecked Sendable {
    var commandHandler: ((CaptureCommand) -> Void)?
    var statusProvider: (() -> DeviceStatus?)?
    var stateHandler: ((String) -> Void)?

    private let queue = DispatchQueue(label: "org.freemocap.mocapcam.network")
    private var listener: NWListener?
    private var connections: [ObjectIdentifier: NWConnection] = [:]
    private var incomingBuffers: [ObjectIdentifier: Data] = [:]
    private(set) var currentPort: UInt16?

    var connectedClientCount: Int {
        queue.sync {
            connections.count
        }
    }

    func start(serviceName: String) {
        queue.async {
            do {
                let listener = try NWListener(using: .tcp, on: .any)
                listener.service = NWListener.Service(name: serviceName, type: "_mocapcam._tcp")
                listener.newConnectionHandler = { [weak self] connection in
                    self?.accept(connection)
                }
                listener.stateUpdateHandler = { [weak self] state in
                    self?.handle(listenerState: state)
                }
                self.listener = listener
                listener.start(queue: self.queue)
            } catch {
                self.stateHandler?("Network failed")
                self.broadcastError(error.localizedDescription)
            }
        }
    }

    func stop() {
        queue.async {
            self.listener?.cancel()
            self.listener = nil
            self.currentPort = nil
            self.connections.values.forEach { $0.cancel() }
            self.connections.removeAll()
            self.incomingBuffers.removeAll()
            self.stateHandler?("Offline")
        }
    }

    func broadcastStatus(_ status: DeviceStatus?) {
        guard let status else {
            return
        }
        do {
            let packet = try CapturePacket.make(
                type: .deviceStatus,
                metadata: DeviceStatusEnvelope(status: status)
            )
            broadcast(packet: packet)
        } catch {
            broadcastError(error.localizedDescription)
        }
    }

    func broadcastRecordingEvent(_ event: RecordingEvent) {
        do {
            let packet = try CapturePacket.make(
                type: .recordingEvent,
                metadata: RecordingEventEnvelope(event: event)
            )
            broadcast(packet: packet)
        } catch {
            broadcastError(error.localizedDescription)
        }
    }

    func broadcastDepthFrame(_ frame: DepthFrame) {
        do {
            let packet = try CapturePacket.make(
                type: .depthFrame,
                metadata: DepthFrameEnvelope(metadata: frame.metadata, payloadBytes: frame.payload.count),
                payload: frame.payload
            )
            broadcast(packet: packet)
        } catch {
            broadcastError(error.localizedDescription)
        }
    }

    func broadcastClockSync(_ reply: ClockSyncReply) {
        do {
            let packet = try CapturePacket.make(
                type: .clockSync,
                metadata: ClockSyncEnvelope(sync: reply)
            )
            broadcast(packet: packet)
        } catch {
            broadcastError(error.localizedDescription)
        }
    }

    func broadcastLocalFileManifest(_ manifest: LocalFileManifest) {
        do {
            let packet = try CapturePacket.make(
                type: .localFileManifest,
                metadata: LocalFileManifestEnvelope(manifest: manifest)
            )
            broadcast(packet: packet)
        } catch {
            broadcastError(error.localizedDescription)
        }
    }

    func broadcastLocalFileChunk(_ chunk: LocalFileChunk, payload: Data) {
        do {
            let packet = try CapturePacket.make(
                type: .localFileChunk,
                metadata: LocalFileChunkEnvelope(chunk: chunk),
                payload: payload
            )
            broadcast(packet: packet)
        } catch {
            broadcastError(error.localizedDescription)
        }
    }

    func broadcastError(_ message: String) {
        do {
            let packet = try CapturePacket.make(
                type: .error,
                metadata: ErrorEnvelope(message: message)
            )
            broadcast(packet: packet)
        } catch {
            stateHandler?("Packet error")
        }
    }

    func broadcast(packet: Data) {
        queue.async {
            for connection in self.connections.values {
                connection.send(content: packet, completion: .contentProcessed { [weak self] error in
                    if error != nil {
                        self?.remove(connection)
                    }
                })
            }
        }
    }

    private func accept(_ connection: NWConnection) {
        let id = ObjectIdentifier(connection)
        connections[id] = connection
        incomingBuffers[id] = Data()
        connection.stateUpdateHandler = { [weak self] state in
            self?.handle(connectionState: state, connection: connection)
        }
        connection.start(queue: queue)
        receive(on: connection)
        stateHandler?("\(connections.count) client(s)")
        broadcastStatus(statusProvider?())
    }

    private func receive(on connection: NWConnection) {
        connection.receive(minimumIncompleteLength: 1, maximumLength: 64 * 1024) { [weak self] data, _, isComplete, error in
            guard let self else {
                return
            }

            if let data, !data.isEmpty {
                self.handleIncoming(data, from: connection)
            }

            if isComplete || error != nil {
                self.remove(connection)
                return
            }

            self.receive(on: connection)
        }
    }

    private func handleIncoming(_ data: Data, from connection: NWConnection) {
        let id = ObjectIdentifier(connection)
        incomingBuffers[id, default: Data()].append(data)

        while let newlineRange = incomingBuffers[id]?.firstRange(of: Data([0x0A])) {
            guard let rawLine = incomingBuffers[id]?[..<newlineRange.lowerBound] else {
                break
            }

            incomingBuffers[id]?.removeSubrange(...newlineRange.lowerBound)
            guard !rawLine.isEmpty else {
                continue
            }

            do {
                let command = try JSONDecoder().decode(CaptureCommand.self, from: Data(rawLine))
                commandHandler?(command)
            } catch {
                broadcastError("Invalid command JSON: \(error.localizedDescription)")
            }
        }
    }

    private func handle(listenerState state: NWListener.State) {
        switch state {
        case .ready:
            currentPort = listener?.port?.rawValue
            if let currentPort {
                stateHandler?("Port \(currentPort)")
            } else {
                stateHandler?("Published")
            }
        case .failed(let error):
            stateHandler?("Failed: \(error.localizedDescription)")
        case .cancelled:
            stateHandler?("Offline")
        default:
            break
        }
    }

    private func handle(connectionState state: NWConnection.State, connection: NWConnection) {
        switch state {
        case .failed, .cancelled:
            remove(connection)
        default:
            break
        }
    }

    private func remove(_ connection: NWConnection) {
        let id = ObjectIdentifier(connection)
        connections.removeValue(forKey: id)
        incomingBuffers.removeValue(forKey: id)
        connection.cancel()
        stateHandler?(currentPort.map { "Port \($0)" } ?? "Published")
    }
}
