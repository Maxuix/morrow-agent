import SwiftUI
import AppKit

// Use the property wrapper so the fixture also builds without SDK macro plugins.
typealias FixtureState<Value> = SwiftUI.State<Value>

struct WindowFacts: Codable, Equatable {
    let number: Int
    let x: Double
    let y: Double
    let width: Double
    let height: Double
    let backingScale: Double
}

struct FixtureSnapshot: Encodable {
    let schemaVersion = 1
    let instanceId: String
    let pid: Int32
    let revision: Int
    let count: Int
    let text: String
    let secureFieldPopulated: Bool
    let scrollOffset: Double
    let window: WindowFacts?
}

// This file is independent of AX, screenshots, the SDK and model responses.
// Secure-field bytes never enter the snapshot, error messages or console.
final class FixtureStateWriter {
    let instanceId = UUID().uuidString
    private var revision = 0

    func write(count: Int, text: String, secure: Bool, scroll: Double,
               window: WindowFacts?) -> String {
        guard let path = Bundle.main.object(forInfoDictionaryKey:
                    "MorrowFixtureStateDirectory") as? String else { return "disabled" }
        revision += 1
        let snapshot = FixtureSnapshot(instanceId: instanceId,
            pid: ProcessInfo.processInfo.processIdentifier, revision: revision,
            count: count, text: text, secureFieldPopulated: secure,
            scrollOffset: scroll, window: window)
        do {
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.sortedKeys]
            let url = URL(fileURLWithPath: path, isDirectory: true)
                .appendingPathComponent("state.json")
            try encoder.encode(snapshot).write(to: url, options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600],
                                                  ofItemAtPath: url.path)
            return "ready"
        } catch { return "failed" }
    }
}

struct ScrollOffset: PreferenceKey {
    static var defaultValue: Double = 0
    static func reduce(value: inout Double, nextValue: () -> Double) { value = nextValue() }
}

struct ScrollObservation: ViewModifier {
    @Binding var offset: Double

    @ViewBuilder
    func body(content: Content) -> some View {
        if #available(macOS 15.0, *) {
            content.onScrollGeometryChange(for: Double.self) { geometry in
                Double(geometry.contentOffset.y + geometry.contentInsets.top)
            } action: { _, newValue in offset = newValue }
        } else {
            content.onPreferenceChange(ScrollOffset.self) { offset = $0 }
        }
    }
}

final class WindowProbeView: NSView {
    var onFacts: ((WindowFacts) -> Void)?
    private var observers: [NSObjectProtocol] = []

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        observers.forEach { NotificationCenter.default.removeObserver($0) }
        observers.removeAll()
        guard let window else { return }
        for name in [NSWindow.didMoveNotification, NSWindow.didResizeNotification,
                     NSWindow.didBecomeKeyNotification, NSWindow.didChangeBackingPropertiesNotification] {
            observers.append(NotificationCenter.default.addObserver(
                forName: name, object: window, queue: .main) { [weak self] _ in self?.publish() })
        }
        DispatchQueue.main.async { [weak self] in self?.publish() }
    }

    private func publish() {
        guard let window else { return }
        let frame = window.frame
        onFacts?(WindowFacts(number: window.windowNumber, x: frame.origin.x, y: frame.origin.y,
                            width: frame.width, height: frame.height,
                            backingScale: window.backingScaleFactor))
    }

    deinit { observers.forEach { NotificationCenter.default.removeObserver($0) } }
}

struct WindowProbe: NSViewRepresentable {
    let onFacts: (WindowFacts) -> Void
    func makeNSView(context: Context) -> WindowProbeView {
        let view = WindowProbeView()
        view.onFacts = onFacts
        return view
    }
    func updateNSView(_ view: WindowProbeView, context: Context) { view.onFacts = onFacts }
}

@main
struct ComputerUseFixture: App {
    var body: some Scene {
        WindowGroup("Morrow Computer Use Fixture") {
            FixtureView().frame(width: 520, height: 480)
        }
        .windowResizability(.contentSize)
    }
}

struct FixtureView: View {
    @FixtureState<Int> private var count = 0
    @FixtureState<String> private var text = ""
    @FixtureState<String> private var password = ""
    @FixtureState<Double> private var scrollOffset = 0
    @FixtureState<WindowFacts?> private var windowFacts = nil
    @FixtureState<String> private var exportStatus = "disabled"
    @FixtureState<FixtureStateWriter> private var writer = FixtureStateWriter()

    private func publish() {
        exportStatus = writer.write(count: count, text: text, secure: !password.isEmpty,
                                    scroll: scrollOffset, window: windowFacts)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Controlled local fixture — no network or account")
            Text("Count: \(count)").accessibilityIdentifier("fixture-count")
            Button("Increment") { count += 1 }
                .accessibilityIdentifier("fixture-increment")
            TextField("Unicode text", text: $text)
                .accessibilityIdentifier("fixture-text")
            Text("Echo: \(text)").accessibilityIdentifier("fixture-echo")
            SecureField("Synthetic secure field", text: $password)
                .accessibilityIdentifier("fixture-secure")
            Text("State output: \(exportStatus)").accessibilityIdentifier("fixture-state-output")
            ScrollView {
                VStack(alignment: .leading) {
                    ForEach(0..<40) { index in Text("Fixture row \(index)") }
                }
                .background(GeometryReader { proxy in
                    Color.clear.preference(key: ScrollOffset.self,
                        value: -proxy.frame(in: .named("fixture-scroll")).minY)
                })
            }.coordinateSpace(name: "fixture-scroll").frame(height: 200)
                .modifier(ScrollObservation(offset: $scrollOffset))
        }.padding(20)
            .background(WindowProbe { windowFacts = $0 })
            .onAppear { publish() }
            .onChange(of: count) { _, _ in publish() }
            .onChange(of: text) { _, _ in publish() }
            .onChange(of: password.isEmpty) { _, _ in publish() }
            .onChange(of: scrollOffset) { _, _ in publish() }
            .onChange(of: windowFacts) { _, _ in publish() }
    }
}
