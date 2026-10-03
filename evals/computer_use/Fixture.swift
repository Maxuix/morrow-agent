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
    let contentHeight: Double
}

struct RegionFacts: Codable, Equatable {
    let x: Double
    let y: Double
    let width: Double
    let height: Double
}

struct FixtureSnapshot: Encodable {
    let schemaVersion = 2
    let instanceId: String
    let pid: Int32
    let revision: Int
    let count: Int
    let text: String
    let secureFieldPopulated: Bool
    let scrollOffset: Double
    let window: WindowFacts?
    let liveText: String
    let liveSecureText: String
    let textChangeEvents: Int
    let secureChangeEvents: Int
    let lastEditedField: String?
    let mouseClickCounts: [Int]
    let rightMouseEvents: Int
    let menuActions: Int
    let keyDownCharacters: [String]
    let keyDownFields: [String]
    let keyUpEvents: Int
    let pointerEvents: [PointerEvent]
    let scrollRegion: RegionFacts?
    let windowIsKey: Bool
    let appActive: Bool
}

struct PointerEvent: Codable {
    let kind: String
    let clickCount: Int
    let windowX: Double
    let windowY: Double
    let deltaY: Double
}

// This file is independent of AX, screenshots, the SDK and model responses.
// Only synthetic test input belongs in this isolated fixture.
final class FixtureStateWriter {
    let instanceId = UUID().uuidString
    private var revision = 0
    var liveText = ""
    var liveSecureText = ""
    var textChangeEvents = 0
    var secureChangeEvents = 0
    var lastEditedField: String? = nil
    var mouseClickCounts: [Int] = []
    var rightMouseEvents = 0
    var menuActions = 0
    var keyDownCharacters: [String] = []
    var keyDownFields: [String] = []
    var keyUpEvents = 0
    var pointerEvents: [PointerEvent] = []
    var scrollRegion: RegionFacts? = nil
    var windowIsKey = false
    var appActive = false
    private var eventMonitor: Any?
    init() {
        eventMonitor = NSEvent.addLocalMonitorForEvents(matching: [.keyDown, .keyUp, .leftMouseDown, .rightMouseDown, .scrollWheel]) { [weak self] event in
            guard let self else { return event }
            if event.type == .leftMouseDown || event.type == .rightMouseDown || event.type == .scrollWheel {
                pointerEvents.append(PointerEvent(
                    kind: event.type == .scrollWheel ? "wheel" : event.type == .rightMouseDown ? "right" : "left",
                    clickCount: event.type == .scrollWheel ? 0 : event.clickCount,
                    windowX: event.locationInWindow.x, windowY: event.locationInWindow.y,
                    deltaY: event.type == .scrollWheel ? event.scrollingDeltaY : 0))
                publish?()
                return event
            }
            let field: String
            if normalControl?.currentEditor() != nil { field = "fixture-text" }
            else if secureControl?.currentEditor() != nil { field = "fixture-secure" }
            else { field = "other" }
            if event.type == .keyDown {
                keyDownCharacters.append(event.characters ?? "")
                keyDownFields.append(field)
            } else { keyUpEvents += 1 }
            publish?()
            return event
        }
    }
    deinit { if let eventMonitor { NSEvent.removeMonitor(eventMonitor) } }
    var publish: (() -> Void)?
    weak var normalControl: NSTextField?
    weak var secureControl: NSTextField?
    func sampleEditors() {
        // AX/SDK edits may bypass NSControlTextDidChange. Window update is an
        // independent AppKit observation of the actual focused field editor.
        for (control, secure) in [(normalControl, false), (secureControl, true)] {
            guard let control else { continue }
            let actual = (control.currentEditor() as? NSTextView)?.string ?? control.stringValue
            let previous = secure ? liveSecureText : liveText
            if actual != previous {
                if secure { liveSecureText = actual } else { liveText = actual }
                lastEditedField = secure ? "fixture-secure" : "fixture-text"
                publish?()
            }
        }
    }

    func edit(_ value: String, secure: Bool) {
        if secure { liveSecureText = value; secureChangeEvents += 1 }
        else { liveText = value; textChangeEvents += 1 }
        lastEditedField = secure ? "fixture-secure" : "fixture-text"
        publish?()
    }

    func write(count: Int, text: String, secure: Bool, scroll: Double,
               window: WindowFacts?) -> String {
        guard let path = Bundle.main.object(forInfoDictionaryKey:
                    "MorrowFixtureStateDirectory") as? String else { return "disabled" }
        revision += 1
        let snapshot = FixtureSnapshot(instanceId: instanceId,
            pid: ProcessInfo.processInfo.processIdentifier, revision: revision,
            count: count, text: text, secureFieldPopulated: secure,
            scrollOffset: scroll, window: window, liveText: liveText,
            liveSecureText: liveSecureText, textChangeEvents: textChangeEvents,
            secureChangeEvents: secureChangeEvents, lastEditedField: lastEditedField,
            mouseClickCounts: mouseClickCounts, rightMouseEvents: rightMouseEvents,
            menuActions: menuActions, keyDownCharacters: keyDownCharacters,
            keyDownFields: keyDownFields, keyUpEvents: keyUpEvents, pointerEvents: pointerEvents,
            scrollRegion: scrollRegion, windowIsKey: windowIsKey, appActive: appActive)
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
    var writer: FixtureStateWriter?
    private var observers: [NSObjectProtocol] = []

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        observers.forEach { NotificationCenter.default.removeObserver($0) }
        observers.removeAll()
        guard let window else { return }
        if ProcessInfo.processInfo.environment["MORROW_FIXTURE_SHIFTED"] == "1" {
            window.setFrameOrigin(NSPoint(x: 100, y: 120))
        }
        for name in [NSWindow.didMoveNotification, NSWindow.didResizeNotification,
                     NSWindow.didBecomeKeyNotification, NSWindow.didResignKeyNotification,
                     NSWindow.didChangeBackingPropertiesNotification,
                     NSWindow.didUpdateNotification] {
            observers.append(NotificationCenter.default.addObserver(
                forName: name, object: window, queue: .main) { [weak self] _ in self?.publish() })
        }
        for name in [NSApplication.didBecomeActiveNotification, NSApplication.didResignActiveNotification] {
            observers.append(NotificationCenter.default.addObserver(
                forName: name, object: NSApp, queue: .main) { [weak self] _ in self?.publish() })
        }
        DispatchQueue.main.async { [weak self] in
            if ProcessInfo.processInfo.environment["MORROW_FIXTURE_FOREGROUND"] == "1" {
                self?.window?.makeKeyAndOrderFront(nil)
                NSApp.activate(ignoringOtherApps: true)
            }
            if let self, let seed = ProcessInfo.processInfo.environment["MORROW_FIXTURE_EDIT_SEED"], !seed.isEmpty,
               let control = self.writer?.normalControl {
                control.stringValue = seed
                self.window?.makeFirstResponder(control)
                (control.currentEditor() as? NSTextView)?.string = seed
                self.writer?.sampleEditors()
            }
            self?.publish()
        }
    }

    private func publish() {
        guard let window else { return }
        // Diagnostic focus changes must not rebuild SwiftUI's native controls.
        if writer?.windowIsKey != window.isKeyWindow || writer?.appActive != NSApp.isActive {
            writer?.windowIsKey = window.isKeyWindow
            writer?.appActive = NSApp.isActive
            writer?.publish?()
        }
        if let content = window.contentView {
            let region = scrollRegion(in: content)
            if writer?.scrollRegion != region {
                writer?.scrollRegion = region
                writer?.publish?()
            }
        }
        writer?.sampleEditors()
        let frame = window.frame
        onFacts?(WindowFacts(number: window.windowNumber, x: frame.origin.x, y: frame.origin.y,
                            width: frame.width, height: frame.height,
                            backingScale: window.backingScaleFactor,
                            contentHeight: Double(window.contentView?.bounds.height ?? 0)))
    }

    private func scrollRegion(in view: NSView) -> RegionFacts? {
        // Same AppKit window coordinates as NSEvent.locationInWindow, independent of AX/SDK.
        if let scroll = view as? NSScrollView, scroll.bounds.height >= 150,
           scroll.bounds.height <= 250 {
            let frame = scroll.convert(scroll.bounds, to: nil)
            return RegionFacts(x: frame.minX, y: frame.minY,
                               width: frame.width, height: frame.height)
        }
        for child in view.subviews {
            if let region = scrollRegion(in: child) { return region }
        }
        return nil
    }

    deinit { observers.forEach { NotificationCenter.default.removeObserver($0) } }
}

struct WindowProbe: NSViewRepresentable {
    let writer: FixtureStateWriter
    let onFacts: (WindowFacts) -> Void
    func makeNSView(context: Context) -> WindowProbeView {
        let view = WindowProbeView()
        view.writer = writer
        view.onFacts = onFacts
        return view
    }
    func updateNSView(_ view: WindowProbeView, context: Context) { view.onFacts = onFacts }
}

// AppKit reports live editor changes independently from committed binding values.
struct OracleTextField: NSViewRepresentable {
    @Binding var value: String
    let secure: Bool
    let writer: FixtureStateWriter
    func makeCoordinator() -> Coordinator { Coordinator(self) }
    func makeNSView(context: Context) -> NSTextField {
        let field = secure ? NSSecureTextField() : NSTextField()
        field.placeholderString = secure ? "Synthetic secure field" : "Unicode text"
        field.setAccessibilityIdentifier(secure ? "fixture-secure" : "fixture-text")
        if secure { writer.secureControl = field } else { writer.normalControl = field }
        field.delegate = context.coordinator
        return field
    }
    func updateNSView(_ field: NSTextField, context: Context) {
        context.coordinator.parent = self
        // Live edits survive focus changes; committed binding is a separate oracle.
        if field.currentEditor() == nil {
            field.stringValue = secure ? writer.liveSecureText : writer.liveText
        }
    }
    final class Coordinator: NSObject, NSTextFieldDelegate {
        var parent: OracleTextField
        init(_ parent: OracleTextField) { self.parent = parent }
        func controlTextDidChange(_ notification: Notification) {
            guard let field = notification.object as? NSTextField else { return }
            parent.writer.edit(field.stringValue, secure: parent.secure)
        }
        func controlTextDidEndEditing(_ notification: Notification) {
            guard let field = notification.object as? NSTextField else { return }
            if notification.userInfo?["NSTextMovement"] as? Int == NSReturnTextMovement {
                parent.value = field.stringValue
            }
        }
    }
}

final class OracleButton: NSButton {
    var writer: FixtureStateWriter?
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func mouseDown(with event: NSEvent) {
        writer?.mouseClickCounts.append(event.clickCount)
        writer?.publish?()
        super.mouseDown(with: event)
    }
    override func rightMouseDown(with event: NSEvent) {
        writer?.rightMouseEvents += 1
        writer?.publish?()
        let menu = NSMenu()
        let item = NSMenuItem(title: "Fixture menu action", action: #selector(menuAction), keyEquivalent: "")
        item.target = self
        menu.addItem(item)
        NSMenu.popUpContextMenu(menu, with: event, for: self)
    }
    @objc func menuAction() {
        writer?.menuActions += 1
        writer?.publish?()
    }
}

struct OracleIncrement: NSViewRepresentable {
    let writer: FixtureStateWriter
    let increment: () -> Void
    func makeCoordinator() -> Coordinator { Coordinator(increment) }
    func makeNSView(context: Context) -> OracleButton {
        let button = OracleButton(title: "Increment", target: context.coordinator,
                                  action: #selector(Coordinator.press))
        button.bezelStyle = .rounded
        button.setAccessibilityIdentifier("fixture-increment")
        button.writer = writer
        return button
    }
    func updateNSView(_ button: OracleButton, context: Context) { context.coordinator.increment = increment }
    final class Coordinator: NSObject {
        var increment: () -> Void
        init(_ increment: @escaping () -> Void) { self.increment = increment }
        @objc func press() { increment() }
    }
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
            OracleIncrement(writer: writer) { count += 1 }.frame(width: 120, height: 28)
            OracleTextField(value: $text, secure: false, writer: writer).frame(height: 24)
            Text("Echo: \(text)").accessibilityIdentifier("fixture-echo")
            OracleTextField(value: $password, secure: true, writer: writer).frame(height: 24)
            Text("State output: \(exportStatus)").accessibilityIdentifier("fixture-state-output")
            ScrollView {
                VStack(alignment: .leading) {
                    ForEach(0..<40) { index in Text("Fixture row \(index)") }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(GeometryReader { proxy in
                    Color.clear.preference(key: ScrollOffset.self,
                        value: -proxy.frame(in: .named("fixture-scroll")).minY)
                })
            }.coordinateSpace(name: "fixture-scroll").frame(width: 480, height: 200)
                .background(Color.gray.opacity(0.08))
                .accessibilityIdentifier("fixture-scroll")
                .modifier(ScrollObservation(offset: $scrollOffset))
        }.padding(20)
            .background(WindowProbe(writer: writer) { windowFacts = $0 })
            .onAppear { writer.publish = { publish() }; publish() }
            .onChange(of: count) { _, _ in publish() }
            .onChange(of: text) { _, _ in publish() }
            .onChange(of: password.isEmpty) { _, _ in publish() }
            .onChange(of: scrollOffset) { _, _ in publish() }
            .onChange(of: windowFacts) { _, _ in publish() }
    }
}
