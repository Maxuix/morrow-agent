import SwiftUI

// Use the property wrapper so the fixture also builds without SDK macro plugins.
typealias FixtureState<Value> = SwiftUI.State<Value>

@main
struct ComputerUseFixture: App {
    var body: some Scene {
        WindowGroup("Morrow Computer Use Fixture") {
            FixtureView().frame(width: 520, height: 440)
        }
        .windowResizability(.contentSize)
    }
}

struct FixtureView: View {
    @FixtureState<Int> private var count = 0
    @FixtureState<String> private var text = ""
    @FixtureState<String> private var password = ""

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
            ScrollView {
                VStack(alignment: .leading) {
                    ForEach(0..<40) { index in Text("Fixture row \(index)") }
                }
            }.frame(height: 200)
        }.padding(20)
    }
}
